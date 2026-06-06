"""
fix_everything.py
=================
Run this to fully diagnose AND fix your local PostgreSQL + admin account.

    python fix_everything.py

If you want a custom password:
    ADMIN_PASSWORD="mypassword" python fix_everything.py
"""

import os, sys
import subprocess

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')
DATABASE_URL   = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/nmms')

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print("=" * 55)
print("  NMMS Admin Fix Script")
print("=" * 55)

# ── Step 1: Check psycopg2 installed ─────────────────────────
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from werkzeug.security import generate_password_hash, check_password_hash
    print("✅ psycopg2 + werkzeug available")
except ImportError as e:
    print(f"❌ Missing package: {e}")
    print("   Run: pip install psycopg2-binary werkzeug")
    sys.exit(1)

# ── Step 2: Try connecting ────────────────────────────────────
print(f"\n🔌 Connecting to: {DATABASE_URL[:55]}...")
try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    print("✅ Connected successfully")
except psycopg2.OperationalError as e:
    err = str(e).strip()
    print(f"❌ Connection failed:\n   {err}\n")

    if 'does not exist' in err:
        print("👉 The database 'nmms' does not exist yet.")
        print("   Creating it now...\n")
        try:
            # Connect to default 'postgres' db to create 'nmms'
            base_url = DATABASE_URL.rsplit('/', 1)[0] + '/postgres'
            c2 = psycopg2.connect(base_url)
            c2.autocommit = True
            c2.cursor().execute("CREATE DATABASE nmms")
            c2.close()
            print("✅ Database 'nmms' created!")
            conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
            print("✅ Connected to new database")
        except Exception as e2:
            print(f"❌ Could not create database: {e2}")
            print("\n── MANUAL FIX ───────────────────────────────────────")
            print("Open a terminal and run:")
            print("  psql -U postgres -c \"CREATE DATABASE nmms;\"")
            print("Then run this script again.")
            sys.exit(1)

    elif 'password authentication' in err or 'role' in err:
        print("👉 Wrong username or password for PostgreSQL.")
        print("\n── MANUAL FIX ───────────────────────────────────────")
        print("Option A — use the correct DATABASE_URL:")
        print("  DATABASE_URL='postgresql://YOUR_USER:YOUR_PASS@localhost:5432/nmms' python fix_everything.py")
        print("\nOption B — reset postgres password:")
        print("  psql -U postgres")
        print("  ALTER USER postgres WITH PASSWORD 'postgres';")
        sys.exit(1)

    elif 'Connection refused' in err or 'could not connect' in err:
        print("👉 PostgreSQL is NOT running on localhost:5432.")
        print("\n── MANUAL FIX ───────────────────────────────────────")
        print("Start PostgreSQL:")
        print("  Windows : Open 'Services' → start 'postgresql-x64-XX'")
        print("            OR: pg_ctl start")
        print("  Mac     : brew services start postgresql")
        print("  Linux   : sudo systemctl start postgresql")
        print("\nThen run this script again.")
        sys.exit(1)
    else:
        print(f"Unknown error. Full message:\n{err}")
        sys.exit(1)

# ── Step 3: Create admin_accounts table if missing ───────────
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_accounts (
        id         SERIAL PRIMARY KEY,
        admin_id   TEXT UNIQUE NOT NULL,
        password   TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
    )
""")
conn.commit()
print("✅ admin_accounts table ready")

# ── Step 4: Show current rows ─────────────────────────────────
cur.execute("SELECT admin_id, password, created_at FROM admin_accounts")
rows = cur.fetchall()
print(f"\n📋 Current admin accounts: {len(rows)}")
for r in rows:
    pw = r['password']
    is_werkzeug = pw.startswith('pbkdf2:') or pw.startswith('scrypt:')
    match = False
    if is_werkzeug:
        try:
            match = check_password_hash(pw, ADMIN_PASSWORD)
        except Exception:
            pass
    print(f"   {r['admin_id']} | hash: {pw[:18]}... | werkzeug={is_werkzeug} | matches_test_pw={match}")

# ── Step 5: Upsert DEVADMIN with fresh hash ───────────────────
print(f"\n🔧 Setting DEVADMIN password to: '{ADMIN_PASSWORD}'")
fresh_hash = generate_password_hash(ADMIN_PASSWORD)
cur.execute("""
    INSERT INTO admin_accounts (admin_id, password)
    VALUES ('DEVADMIN', %s)
    ON CONFLICT (admin_id) DO UPDATE SET password = EXCLUDED.password
""", (fresh_hash,))
conn.commit()

# ── Step 6: Verify ────────────────────────────────────────────
cur.execute("SELECT password FROM admin_accounts WHERE admin_id='DEVADMIN'")
saved = cur.fetchone()
verified = check_password_hash(saved['password'], ADMIN_PASSWORD)
print(f"✅ Saved and verified: {'PASSED ✅' if verified else 'FAILED ❌'}")

conn.close()

print("\n" + "=" * 55)
print("  LOGIN DETAILS")
print("=" * 55)
print(f"  URL      : http://127.0.0.1:5000/admin/login")
print(f"  Admin ID : DEVADMIN")
print(f"  Password : {ADMIN_PASSWORD}")
print("=" * 55)

if not verified:
    print("\n❌ Verification failed — something is wrong with your werkzeug install.")
    print("   Try: pip install --upgrade werkzeug")
