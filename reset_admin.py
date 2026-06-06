"""
reset_admin.py
==============
Run this ONCE to fix/create the DEVADMIN account in your PostgreSQL database.

Usage:
    python reset_admin.py

Or with a custom password:
    ADMIN_PASSWORD="mynewpassword" python reset_admin.py

Or with a remote DB:
    DATABASE_URL="postgresql://..." python reset_admin.py
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/nmms')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

ADMIN_ID       = os.environ.get('ADMIN_ID',       'DEVADMIN')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')

print(f"Connecting to: {DATABASE_URL[:40]}...")

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cur  = conn.cursor()

# Make sure the table exists
cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_accounts (
        id         SERIAL PRIMARY KEY,
        admin_id   TEXT UNIQUE NOT NULL,
        password   TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
    )
""")

hashed = generate_password_hash(ADMIN_PASSWORD)

# Check if account exists
cur.execute("SELECT id, password FROM admin_accounts WHERE admin_id=%s", (ADMIN_ID,))
row = cur.fetchone()

if row:
    cur.execute("UPDATE admin_accounts SET password=%s WHERE admin_id=%s", (hashed, ADMIN_ID))
    print(f"✅ Updated password for existing account: {ADMIN_ID}")
else:
    cur.execute("INSERT INTO admin_accounts (admin_id, password) VALUES (%s, %s)", (ADMIN_ID, hashed))
    print(f"✅ Created new admin account: {ADMIN_ID}")

conn.commit()

# Verify it works
cur.execute("SELECT password FROM admin_accounts WHERE admin_id=%s", (ADMIN_ID,))
saved = cur.fetchone()
ok = check_password_hash(saved['password'], ADMIN_PASSWORD)
print(f"✅ Password verification: {'PASSED' if ok else 'FAILED'}")

conn.close()
print(f"\n🎉 Done! Login with:")
print(f"   Admin ID : {ADMIN_ID}")
print(f"   Password : {ADMIN_PASSWORD}")
