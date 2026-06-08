# NMMS — Complete PostgreSQL Fix & Deployment Guide

Your `app.py` is already written correctly for PostgreSQL.
The crash happens because **PostgreSQL isn't connected yet**.
Follow these steps in order.

---

## PART 1 — Local Fix (CentOS/RHEL)

### Step 1 — Install PostgreSQL (if not installed)
```bash
sudo dnf install -y postgresql-server postgresql
sudo postgresql-setup --initdb
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### Step 2 — Create the database
```bash
sudo -u postgres psql << 'EOF'
CREATE DATABASE nmms;
CREATE USER nmmsuser WITH PASSWORD 'nmms@dev2024!';
GRANT ALL PRIVILEGES ON DATABASE nmms TO nmmsuser;
\q
EOF
```

### Step 3 — Fix pg_hba.conf (allow password login)
```bash
sudo nano /var/lib/pgsql/data/pg_hba.conf
```
Find this line:
```
host    all    all    127.0.0.1/32    ident
```
Change `ident` to `md5`:
```
host    all    all    127.0.0.1/32    md5
```
Then reload:
```bash
sudo systemctl reload postgresql
```

### Step 4 — Set environment variable
```bash
export DATABASE_URL="postgresql://nmmsuser:nmms@dev2024!@localhost:5432/nmms"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```
To make permanent (survives reboot):
```bash
echo 'export DATABASE_URL="postgresql://nmmsuser:nmms@dev2024!@localhost:5432/nmms"' >> ~/.bashrc
echo 'export SECRET_KEY="your-key-here"' >> ~/.bashrc
source ~/.bashrc
```

### Step 5 — Install dependencies & run
```bash
pip3 install -r requirements.txt
python3 app.py
```
App runs at: http://localhost:5000

### Step 6 — Create admin account
```bash
python3 reset_admin.py
```
Login: **DEVADMIN** / **nmms@dev2024!**

---

## PART 2 — Deploy to Railway (Recommended — Free)

Railway auto-provides PostgreSQL. Easiest option.

### Step 1 — Push your code to GitHub
```bash
git init
git add .
git commit -m "initial commit"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOURNAME/nmms.git
git push -u origin main
```

### Step 2 — Create Railway project
1. Go to https://railway.app → Sign up with GitHub
2. Click **New Project** → **Deploy from GitHub repo** → select your repo
3. Railway auto-detects your Dockerfile and deploys

### Step 3 — Add PostgreSQL
1. In Railway dashboard → **+ New** → **Database** → **Add PostgreSQL**
2. Click the PostgreSQL service → **Variables** tab
3. Copy the `DATABASE_URL` value

### Step 4 — Set environment variables
In Railway → your app service → **Variables** tab, add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | (paste from PostgreSQL service — Railway fills this automatically) |
| `SECRET_KEY` | any long random string, e.g. `my-super-secret-key-change-this-123` |
| `HTTPS` | `1` |

> Railway usually links DATABASE_URL automatically. If not, copy it manually.

### Step 5 — Deploy & create admin
Railway redeploys automatically on every git push.

After first deploy, open Railway **Shell** tab and run:
```bash
python reset_admin.py
```

### Step 6 — Migrate existing SQLite data (optional)
If you have data in nmms.db to keep:
```bash
DATABASE_URL="your-railway-postgres-url" python migrate_to_postgres.py
```

---

## PART 3 — Deploy to Render (Alternative Free Option)

### Step 1 — Push to GitHub (same as Railway Step 1)

### Step 2 — Create Render Web Service
1. Go to https://render.com → New → **Web Service**
2. Connect GitHub repo
3. Set:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`

### Step 3 — Add PostgreSQL on Render
1. New → **PostgreSQL** → Free tier
2. Copy **Internal Database URL**

### Step 4 — Environment Variables on Render
Add under **Environment**:
```
DATABASE_URL = (paste Internal Database URL)
SECRET_KEY   = (any long random string)
HTTPS        = 1
```

---

## PART 4 — Fix Dockerfile (PORT issue)

Your current Dockerfile hardcodes port 8080 but Railway uses `$PORT`.
Replace your Dockerfile with this:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2
```

This uses Railway's `$PORT` if set, otherwise falls back to 8080.

---

## PART 5 — Fix Procfile (for Railway without Docker)

Replace your `Procfile` content with:
```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

---

## Quick Diagnosis Checklist

Run these to find the exact problem:

```bash
# 1. Is PostgreSQL running?
sudo systemctl status postgresql

# 2. Can you connect?
psql postgresql://nmmsuser:nmms@dev2024!@localhost:5432/nmms -c "SELECT 1"

# 3. Is DATABASE_URL set?
echo $DATABASE_URL

# 4. Full auto-diagnosis
python3 fix_everything.py
```

---

## Common Errors & Fixes

| Error | Fix |
|---|---|
| `Connection refused port 5432` | `sudo systemctl start postgresql` |
| `password authentication failed` | Fix pg_hba.conf (Step 3 above) |
| `database "nmms" does not exist` | Run `CREATE DATABASE nmms;` in psql |
| `Worker failed to boot` | DATABASE_URL not set — check Step 4 |
| `role "postgres" does not exist` | Use `sudo -u postgres psql` not just `psql` |
| `HaltServer WORKER_BOOT_ERROR` | App crashes on startup — DATABASE_URL wrong |
