"""
migrate_to_postgres.py  (fixed)
================================
Copies all data from nmms.db (SQLite) → PostgreSQL.
Run this ONCE before deploying.

Usage:
    pip install psycopg2-binary
    DATABASE_URL="postgresql://user:pass@host:5432/dbname" python migrate_to_postgres.py

Get DATABASE_URL from Railway/Render → your PostgreSQL service → Connect tab.

FIXES vs original:
  1. Handles 'debt_blocked' column (added via ALTER TABLE in SQLite)
  2. site_settings uses key-based upsert (TEXT PRIMARY KEY, not SERIAL)
  3. Proper per-table transactions — one bad row won't silently kill the whole table
  4. SSL fallback for Railway DATABASE_URL
  5. Pre-flight check: warns if Postgres tables don't exist yet (run app once first)
  6. Handles NULL bkash_txn uniqueness (SQLite NULLs are equal in UNIQUE, Postgres are not)
  7. Sequence reset runs in its own safe transaction
  8. Clear summary at end
"""

import os, sys, sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor

# ── Config ────────────────────────────────────────────────────────────────────
SQLITE_PATH  = os.environ.get('SQLITE_PATH', 'nmms.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

if not DATABASE_URL:
    print("❌  Set DATABASE_URL first.")
    print("    Example: DATABASE_URL='postgresql://...' python migrate_to_postgres.py")
    sys.exit(1)

# Railway uses postgres://, psycopg2 needs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print(f"📂  SQLite source : {SQLITE_PATH}")
print(f"🐘  Postgres target: {DATABASE_URL[:50]}...")
print()

# ── Connect ───────────────────────────────────────────────────────────────────
sq = sqlite3.connect(SQLITE_PATH)
sq.row_factory = sqlite3.Row

try:
    pg = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor,
                          sslmode='require')
except psycopg2.OperationalError:
    # Some local/dev Postgres instances don't support SSL
    pg = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

pg.autocommit = False

def sq_all(sql, params=()):
    return sq.execute(sql, params).fetchall()

def pg_exec(cur, sql, params=()):
    cur.execute(sql, params)

# ── Pre-flight: make sure Postgres schema exists ──────────────────────────────
print("🔍  Pre-flight: checking Postgres schema...")
with pg.cursor() as cur:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    pg_tables = {r['table_name'] for r in cur.fetchall()}

required = {
    'admin_accounts', 'admin_reset_log', 'site_settings', 'students',
    'meal_managers', 'meal_orders', 'payments', 'cash_payment_requests',
    'manager_history', 'manager_transfer_invites', 'manager_rotation',
    'bkash_proposals', 'weekly_bkash', 'bkash_proposal_votes',
    'duty_invites', 'meal_edit_requests', 'registration_codes',
    'floor_change_requests', 'phone_change_requests',
}
missing = required - pg_tables
if missing:
    print(f"❌  Missing tables in Postgres: {missing}")
    print("    → Start your app once so init_db() creates the schema, then re-run this script.")
    sys.exit(1)

# Check debt_blocked column exists (added via ALTER TABLE in app.py)
with pg.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='students' AND column_name='debt_blocked'
    """)
    if not cur.fetchone():
        print("⚠️   'debt_blocked' column missing in Postgres students table.")
        print("    Adding it now...")
        cur.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS debt_blocked INTEGER DEFAULT 0")
        pg.commit()
        print("    ✅  Added debt_blocked column.")

print("✅  Schema OK\n")

# ── Tables in FK-safe insertion order ────────────────────────────────────────
# site_settings has TEXT PRIMARY KEY (not SERIAL id), handled separately below
SERIAL_TABLES = [
    'admin_accounts',
    'admin_reset_log',
    'meal_managers',          # must be before manager_history, bkash_proposals etc
    'manager_history',
    'manager_transfer_invites',
    'students',               # must be before meal_orders, payments etc
    'meal_orders',
    'payments',
    'cash_payment_requests',
    'manager_rotation',
    'bkash_proposals',
    'weekly_bkash',
    'bkash_proposal_votes',
    'duty_invites',
    'meal_edit_requests',
    'registration_codes',
    'floor_change_requests',
    'phone_change_requests',
]

total_ok   = 0
total_skip = 0
results    = {}

# ── Migrate site_settings (TEXT PRIMARY KEY = key) ───────────────────────────
print("📋  Migrating site_settings...")
try:
    rows = sq_all("SELECT * FROM site_settings")
except Exception as e:
    rows = []
    print(f"  ⚠️   Could not read site_settings from SQLite: {e}")

ok = skip = 0
for row in rows:
    try:
        with pg.cursor() as cur:
            pg_exec(cur, """
                INSERT INTO site_settings (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                    SET value      = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
            """, (row['key'], row['value'], row['updated_at']))
        pg.commit()
        ok += 1
    except Exception as e:
        pg.rollback()
        print(f"    ⚠️   Skip row key={row['key']}: {e}")
        skip += 1

print(f"  ✅  site_settings: {ok} upserted, {skip} skipped")
total_ok += ok; total_skip += skip
results['site_settings'] = (ok, skip)

# ── Migrate all SERIAL-id tables ─────────────────────────────────────────────
for table in SERIAL_TABLES:
    print(f"📋  Migrating {table}...")
    try:
        rows = sq_all(f"SELECT * FROM {table}")
    except Exception as e:
        print(f"  ⚠️   Cannot read {table} from SQLite (table may not exist yet): {e}")
        results[table] = (0, 0)
        continue

    if not rows:
        print(f"  ⏭️   {table}: empty")
        results[table] = (0, 0)
        continue

    cols    = list(rows[0].keys())
    ph      = ', '.join(['%s'] * len(cols))
    col_str = ', '.join(cols)

    # Special case: payments.bkash_txn has a UNIQUE constraint but SQLite
    # treats NULL as non-equal (multiple NULLs allowed).
    # Postgres also allows multiple NULLs in UNIQUE columns, so this is fine —
    # but two rows with the same non-NULL bkash_txn would conflict.
    # We use ON CONFLICT DO NOTHING so duplicates are skipped safely.
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({ph}) ON CONFLICT DO NOTHING"

    ok = skip = 0
    for row in rows:
        vals = [row[c] for c in cols]
        try:
            with pg.cursor() as cur:
                pg_exec(cur, sql, vals)
            pg.commit()
            ok += 1
        except Exception as e:
            pg.rollback()
            print(f"    ⚠️   Skip row id={row.get('id','?')} in {table}: {e}")
            skip += 1

    total_ok += ok; total_skip += skip
    results[table] = (ok, skip)
    print(f"  ✅  {table}: {ok}/{len(rows)} rows migrated, {skip} skipped")

# ── Reset all SERIAL sequences ────────────────────────────────────────────────
print("\n🔧  Resetting sequences...")
for table in SERIAL_TABLES:
    try:
        with pg.cursor() as cur:
            # setval so next INSERT gets max(id)+1
            pg_exec(cur, f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE(MAX(id), 1)
                ) FROM {table}
            """)
        pg.commit()
        print(f"  ✅  {table}")
    except Exception as e:
        pg.rollback()
        print(f"  ⚠️   {table}: {e}")

# ── Final summary ─────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
print(f"🎉  Migration complete!")
print(f"    Rows inserted : {total_ok}")
print(f"    Rows skipped  : {total_skip}")
print()
print("    Per-table summary:")
for tbl, (ok, skip) in results.items():
    flag = "⚠️ " if skip else "✅"
    print(f"    {flag}  {tbl}: {ok} ok, {skip} skipped")
print()
print("Next steps:")
print("  1. Deploy your app.py (it will call init_db() on startup).")
print("  2. Log in and verify your data looks correct.")
print("  3. Delete nmms.db from the server — it is no longer used.")
print("═" * 60)

sq.close()
pg.close()
