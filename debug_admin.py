"""
debug_admin.py
==============
Run this to diagnose exactly why admin login is failing.

    python debug_admin.py
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/nmms')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

TEST_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')

print(f"DB : {DATABASE_URL[:60]}")
print(f"Testing password: '{TEST_PASSWORD}'\n")

try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    print("✅ DB connection OK\n")
except Exception as e:
    print(f"❌ DB connection FAILED: {e}")
    exit(1)

cur = conn.cursor()

# Show all admin accounts
cur.execute("SELECT id, admin_id, password, created_at FROM admin_accounts")
rows = cur.fetchall()
print(f"Rows in admin_accounts: {len(rows)}")

if not rows:
    print("❌ NO ADMIN ACCOUNTS EXIST — table is empty!")
else:
    for row in rows:
        pw = row['password']
        print(f"\n  admin_id  : {row['admin_id']}")
        print(f"  created_at: {row['created_at']}")
        print(f"  hash type : {pw[:20]}...")

        # Test werkzeug verify
        try:
            wz_ok = check_password_hash(pw, TEST_PASSWORD)
            print(f"  werkzeug check_password_hash('{TEST_PASSWORD}'): {'✅ PASS' if wz_ok else '❌ FAIL'}")
        except Exception as e:
            print(f"  werkzeug error: {e}")

        # Test sha256 fallback
        sha_hash = hashlib.sha256(TEST_PASSWORD.encode()).hexdigest()
        sha_ok = (pw == sha_hash)
        print(f"  sha256 match: {'✅ PASS' if sha_ok else '❌ FAIL'}")

print("\n--- FIX: Force-setting DEVADMIN with fresh hash ---")
hashed = generate_password_hash(TEST_PASSWORD)
cur.execute("""
    INSERT INTO admin_accounts (admin_id, password)
    VALUES ('DEVADMIN', %s)
    ON CONFLICT (admin_id) DO UPDATE SET password = EXCLUDED.password
""", (hashed,))
conn.commit()

# Verify
cur.execute("SELECT password FROM admin_accounts WHERE admin_id='DEVADMIN'")
saved = cur.fetchone()
ok = check_password_hash(saved['password'], TEST_PASSWORD)
print(f"✅ Upserted DEVADMIN, verify: {'PASSED' if ok else 'FAILED'}")

conn.close()
print(f"\n🎉 Try logging in now:")
print(f"   Admin ID : DEVADMIN")
print(f"   Password : {TEST_PASSWORD}")
