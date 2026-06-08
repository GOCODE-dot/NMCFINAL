#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  NMMS Local Setup Script — CentOS/RHEL
#  Run: bash local_setup.sh
# ═══════════════════════════════════════════════════════════════

set -e

echo "════════════════════════════════════"
echo "  NMMS PostgreSQL Setup"
echo "════════════════════════════════════"

DB_NAME="nmms"
DB_USER="nmmsuser"
DB_PASS="nmms@dev2024!"

# 1. Install PostgreSQL
echo ""
echo "▶ Step 1: Installing PostgreSQL..."
if command -v psql &>/dev/null; then
    echo "  ✅ Already installed"
else
    sudo dnf install -y postgresql-server postgresql
    sudo postgresql-setup --initdb
fi

# 2. Start PostgreSQL
echo ""
echo "▶ Step 2: Starting PostgreSQL..."
sudo systemctl enable postgresql
sudo systemctl start postgresql
echo "  ✅ PostgreSQL running"

# 3. Fix pg_hba.conf
echo ""
echo "▶ Step 3: Fixing authentication config..."
HBA="/var/lib/pgsql/data/pg_hba.conf"
if grep -q "127.0.0.1/32.*ident" "$HBA" 2>/dev/null; then
    sudo sed -i 's/127.0.0.1\/32.*ident/127.0.0.1\/32            md5/g' "$HBA"
    sudo systemctl reload postgresql
    echo "  ✅ Fixed pg_hba.conf (ident → md5)"
else
    echo "  ✅ pg_hba.conf already OK"
fi

# 4. Create DB and user
echo ""
echo "▶ Step 4: Creating database and user..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null || true
echo "  ✅ Database '$DB_NAME' ready"

# 5. Set environment variables
echo ""
echo "▶ Step 5: Setting environment variables..."
export DATABASE_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# Save to .env file
cat > .env << EOF
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME
SECRET_KEY=$SECRET_KEY
EOF
echo "  ✅ Saved to .env file"

# 6. Install Python deps
echo ""
echo "▶ Step 6: Installing Python packages..."
pip3 install -r requirements.txt -q
echo "  ✅ Packages installed"

# 7. Create admin account
echo ""
echo "▶ Step 7: Creating admin account..."
DATABASE_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME" python3 reset_admin.py

echo ""
echo "════════════════════════════════════"
echo "  ✅ SETUP COMPLETE!"
echo "════════════════════════════════════"
echo ""
echo "  Start the app:"
echo "    source .env && python3 app.py"
echo "    (or: set -a; source .env; set +a; python3 app.py)"
echo ""
echo "  Login at http://localhost:5000/admin/login"
echo "  Admin ID : DEVADMIN"
echo "  Password : nmms@dev2024!"
echo ""
