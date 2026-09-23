# Deploying NMMS on a Namecheap VPS (Spark plan)

Namecheap's Spark VPS gives you a plain root Linux server (Ubuntu, most
commonly) — no built-in Python/Postgres hosting like Railway/Render provide.
This guide sets the app up the standard way: **Gunicorn (app server) behind
Nginx (reverse proxy + SSL)**, running as a **systemd service**, backed by a
**local PostgreSQL database**.

Everything below assumes Ubuntu 22.04/24.04, the OS Namecheap installs by
default on Spark VPS unless you picked something else. Run all commands over
SSH as a sudo-capable user (or root).

---

## 0. Point your domain at the VPS

In Namecheap's dashboard → **Domain List → Manage → Advanced DNS**, add an
**A Record**:
- Host: `@` (and another one for `www` if you want both)
- Value: your VPS's public IP (find it in Namecheap's VPS dashboard)
- TTL: Automatic

DNS can take a few minutes to a few hours to propagate. You can start the
steps below while you wait — you just won't be able to run `certbot` (step 7)
until the domain resolves to the VPS.

---

## 1. Update the server & install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx git unzip
```

---

## 2. Create a dedicated system user

Running the app as its own unprivileged user (not root) is standard practice.

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin nmms
```

---

## 3. Upload and unpack the project

From your **local machine**, upload the zip (replace with your VPS IP):

```bash
scp NMCFINAL-main.zip root@YOUR_VPS_IP:/opt/
```

Back on the **VPS**:

```bash
sudo mkdir -p /opt/nmms
cd /opt/nmms
sudo unzip /opt/NMCFINAL-main.zip -d /opt/nmms
# You should now have /opt/nmms/NMCFINAL-main/
```

---

## 4. Set up PostgreSQL

```bash
sudo -u postgres psql
```

Inside the `psql` prompt (replace the password):

```sql
CREATE DATABASE nmms;
CREATE USER nmms_user WITH PASSWORD 'CHANGE_ME_DB_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE nmms TO nmms_user;
ALTER DATABASE nmms OWNER TO nmms_user;
\q
```

You don't need to create any tables by hand — `app.py` calls `init_db()` on
startup and creates every table itself if it's missing.

---

## 5. Python virtual environment

```bash
cd /opt/nmms
sudo python3 -m venv venv
sudo ./venv/bin/pip install --upgrade pip
sudo ./venv/bin/pip install -r NMCFINAL-main/requirements.txt
```

---

## 6. Configure environment variables

```bash
sudo mkdir -p /etc/nmms
sudo cp /opt/nmms/NMCFINAL-main/deploy/nmms.env.example /etc/nmms/nmms.env
sudo nano /etc/nmms/nmms.env
```

Fill in real values for every `CHANGE_ME_...` placeholder:
- `DATABASE_URL` — use the DB user/password from step 4
- `SECRET_KEY` — generate one with:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- `MANAGER_PASSWORD`, `ADMIN_PASSWORD` — pick strong passwords (the app ships
  with weak defaults like `manager123` — don't leave those in production)
- `EMERGENCY_KEY` — any long random string

**bKash section:** leave `BKASH_MOCK_MODE=1` for now — this lets you test
the whole order → pay → confirm flow with no real bKash account. Only fill
in `BKASH_APP_KEY`/`BKASH_APP_SECRET`/`BKASH_USERNAME`/`BKASH_PASSWORD` and
flip `BKASH_MOCK_MODE=0` once you have real bKash Merchant/PGW credentials
**and** SSL is working (step 9) — bKash needs to redirect users back to a
real HTTPS URL (`BKASH_CALLBACK_URL`), not localhost.

Lock down the file (it holds real passwords):

```bash
sudo chmod 600 /etc/nmms/nmms.env
sudo chown nmms:nmms /etc/nmms/nmms.env
```

Then set ownership on the app files themselves:

```bash
sudo chown -R nmms:nmms /opt/nmms
```

---

## 7. Install the systemd service

```bash
sudo mkdir -p /var/log/nmms
sudo chown nmms:nmms /var/log/nmms

sudo cp /opt/nmms/NMCFINAL-main/deploy/nmms.service /etc/systemd/system/nmms.service
sudo systemctl daemon-reload
sudo systemctl enable --now nmms
sudo systemctl status nmms
```

You should see `active (running)`. If not, check logs:

```bash
sudo journalctl -u nmms -n 50 --no-pager
tail -n 50 /var/log/nmms/error.log
```

Common first-run issues: wrong `DATABASE_URL` password, or the venv path in
`nmms.service` not matching where you actually put the venv.

---

## 8. Configure Nginx

```bash
sudo cp /opt/nmms/NMCFINAL-main/deploy/nginx_nmms.conf /etc/nginx/sites-available/nmms
sudo nano /etc/nginx/sites-available/nmms   # replace yourdomain.com with your real domain

sudo ln -s /etc/nginx/sites-available/nmms /etc/nginx/sites-enabled/nmms
sudo rm -f /etc/nginx/sites-enabled/default   # avoid the default page conflicting
sudo nginx -t
sudo systemctl reload nginx
```

At this point, visiting `http://yourdomain.com` should load the app over
plain HTTP.

---

## 9. Enable HTTPS (SSL)

Once DNS has propagated (check with `dig yourdomain.com` — it should return
your VPS IP):

```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Certbot edits the Nginx config automatically to add the SSL certificate and
redirect HTTP → HTTPS. Certificates auto-renew via a systemd timer certbot
installs for you (`systemctl status certbot.timer` to confirm).

Once this is live, update `BKASH_CALLBACK_URL` in `/etc/nmms/nmms.env` to
`https://yourdomain.com/student/bkash/callback` and restart the app:

```bash
sudo systemctl restart nmms
```

---

## 10. Firewall

Only allow SSH, HTTP, and HTTPS from the outside — Gunicorn's port (8000)
should never be reachable directly, only via Nginx on localhost.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

---

## 11. First login

Visit `https://yourdomain.com`:
- **Admin panel**: `/admin/login` — use the `ADMIN_PASSWORD` you set in step 6
- **Manager login**: use the `MANAGER_PASSWORD` you set in step 6
- **Student registration**: `/register`

---

## Day-2 operations

**Deploying an update:**

```bash
sudo systemctl stop nmms
# replace files in /opt/nmms/NMCFINAL-main/ with the new version
sudo systemctl start nmms
```

**Viewing logs:**

```bash
sudo journalctl -u nmms -f          # live app/systemd logs
tail -f /var/log/nmms/access.log    # gunicorn access log
tail -f /var/log/nmms/error.log     # gunicorn error log
```

**Database backups** (do this on a schedule, e.g. via cron):

```bash
sudo -u postgres pg_dump nmms > nmms_backup_$(date +%F).sql
```

**Restarting after a server reboot:** nothing to do — `systemctl enable` in
step 7 already made `nmms`, `nginx`, and `postgresql` start on boot.

---

## Notes specific to this app

- The app auto-creates its database schema on startup (`init_db()`), so
  there's no manual migration step for a fresh install.
- `debug` mode is now off unless you explicitly set `FLASK_DEBUG=1` in the
  env file — keep it off in production; Flask's debug mode allows arbitrary
  code execution from the browser if left on and exposed to the internet.
- **bKash payments default to mock mode.** Real payments require a bKash
  Merchant/PGW account, real credentials in `nmms.env`, and a working HTTPS
  domain for the callback URL — see step 6/9 above.
- Rate limiting (`flask-limiter`) uses in-memory storage, not shared across
  Gunicorn's multiple workers. Fine at this scale; move to Redis if you ever
  need strict shared rate limits.
- This zip has a stray duplicate `templates/app.py` sitting next to the real
  `app.py` in the project root — it's an old backup copy, not imported or
  used by anything, and safe to ignore or delete. Only `/opt/nmms/NMCFINAL-main/app.py`
  (the one `nmms.service` points Gunicorn at) actually runs.
- There are many `fix_*.py` / `app_*_additions.py` files in the project root
  (patch notes from earlier development). None of them are imported by
  `app.py` — everything they describe is already merged into the real
  `app.py`. Safe to ignore.
