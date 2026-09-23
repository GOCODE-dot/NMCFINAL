# PostgreSQL Setup — Railway & Namecheap VPS

This app uses **PostgreSQL only** (via `psycopg2`) and talks to it through a
single environment variable: **`DATABASE_URL`**. Whichever platform you use,
your only real job is producing a valid `DATABASE_URL` and setting it — the
app builds every table itself on startup (`init_db()` in `app.py`), so
there's no schema file to run by hand and no migration tool to install.

```
DATABASE_URL = postgresql://USERNAME:PASSWORD@HOST:PORT/DATABASE_NAME
```

The app also accepts the `postgres://` form (some platforms still emit that
older prefix) and automatically rewrites it to `postgresql://` at startup —
you don't need to fix that yourself.

---

## Option A — Railway (managed Postgres)

Railway hosts Postgres for you; you never install or configure the database
server itself.

1. **Create/open your Railway project** and add the Postgres plugin:
   Project → **+ New** → **Database** → **Add PostgreSQL**.

2. **Deploy the app as a second service** in the *same* Railway project
   (New → GitHub Repo, or Empty Service + upload). This project already
   ships a `Dockerfile` / `nixpacks.toml` / `Procfile`, so Railway will pick
   one of those up automatically — you don't need to configure a build
   command by hand.

3. **Link the database to the app** — this is the step people usually miss:
   open your app service → **Variables** tab → **New Variable** →
   **Add Reference** → pick the Postgres service's `DATABASE_URL`. Railway
   then keeps that value in sync automatically; you never type the
   host/password yourself.

   (If you'd rather do it manually: open the Postgres service → **Variables**
   tab → copy the `DATABASE_URL` value it generated → paste it as a plain
   variable named `DATABASE_URL` on your app service. The Reference method
   above is preferred because it updates itself if Railway ever rotates
   credentials.)

4. **Set the other required variables** on the app service (Variables tab):
   ```
   SECRET_KEY        = <run: python3 -c "import secrets; print(secrets.token_hex(32))">
   ADMIN_PASSWORD    = <strong password>
   MANAGER_PASSWORD  = <strong password>
   MANAGER_BKASH     = <manager's real bKash number>
   EMERGENCY_KEY     = <long random string>
   ```
   Railway already sets `RAILWAY_ENVIRONMENT`, which the app uses to detect
   it's running on HTTPS and mark cookies `Secure` automatically — you don't
   need to set `HTTPS` yourself on Railway.

5. **Deploy.** Watch the build logs — on first boot you should see:
   ```
   [NMMS] Generated new SECRET_KEY and saved to .secret_key   ← only if you skipped step 4's SECRET_KEY
   ```
   and no `FATAL: init_db() failed` line. If you do see that error, it's
   almost always variable #3 (DATABASE_URL) not being linked correctly —
   double-check the Reference points at the Postgres service.

6. **Connecting with a GUI/psql from your own machine** (optional, for
   inspecting data): Postgres service → **Connect** tab → Railway gives you
   a public connection string + a "Connect" button for `psql` directly in
   the browser. Use that same string in TablePlus/DBeaver/pgAdmin if you
   prefer a GUI.

Railway handles backups if you're on a paid plan (check your plan's backup
policy in the Postgres service settings); otherwise use the `pg_dump`
command in the **Backups** section below on a schedule of your own.

---

## Option B — Namecheap VPS (self-hosted Postgres)

On a VPS, you install and run Postgres yourself, alongside the app, on the
same machine. This is the approach `DEPLOY_VPS.md` in this project already
walks through end-to-end — the steps below are the database-only piece of
that guide, with more detail.

### 1. Install Postgres

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### 2. Create the database and a dedicated user

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE nmms;
CREATE USER nmms_user WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE nmms TO nmms_user;
ALTER DATABASE nmms OWNER TO nmms_user;
\q
```

Never use the `postgres` superuser account for the app itself — always a
dedicated, limited user like `nmms_user`.

### 3. Build your DATABASE_URL

With the values from step 2, on the **same VPS** (app and DB on one
machine, connecting over `localhost`):

```
DATABASE_URL=postgresql://nmms_user:CHANGE_ME_STRONG_PASSWORD@localhost:5432/nmms
```

Put this in `/etc/nmms/nmms.env` (see `DEPLOY_VPS.md` step 6) — that's the
only place it needs to live.

### 4. Confirm Postgres is listening correctly

By default, a fresh install only listens on `localhost`, which is exactly
what you want here (no reason to expose Postgres to the public internet
when the app and database live on the same server). Check:

```bash
sudo -u postgres psql -c "SHOW listen_addresses;"
```

Default is `localhost` — leave it that way. **Do not** change this to `*`
or add a public-facing `pg_hba.conf` rule unless you specifically need a
second server to reach this database remotely (uncommon for this app's
setup, and it widens your attack surface — see the Security note below).

### 5. Test the connection

```bash
psql "postgresql://nmms_user:CHANGE_ME_STRONG_PASSWORD@localhost:5432/nmms" -c "SELECT 1;"
```

If that returns `1`, the connection string is good — the app will connect
the same way.

### 6. Start the app

Once `DATABASE_URL` is set in `/etc/nmms/nmms.env` and the systemd service
is running (`DEPLOY_VPS.md` step 7), check the first-boot logs the same way
as the Railway section above:

```bash
sudo journalctl -u nmms -n 50 --no-pager
```

You're looking for a clean start with no `FATAL: init_db() failed`.

---

## Schema reference

You never need to create these by hand — listed here purely for reference
(e.g. if you're writing a report query, or restoring a backup and want to
sanity-check row counts). This is the exact table list `init_db()` creates,
grouped by what they're for:

**Accounts**
| Table | Purpose |
|---|---|
| `students` | One row per registered student (name, batch, roll_number, bkash_number, password hash, gender, floor/hostel, lock flags) |
| `meal_managers` | Meal manager accounts (currently one active manager at a time, `MGR001` by default) |
| `admin_accounts` | Admin/dev-admin login (`DEVADMIN` by default) |
| `manager_history` | Audit trail of who has held the manager duty and when |
| `manager_transfer_invites` | Pending "hand off manager duty to another student" invites |
| `manager_rotation` | Weekly manager duty rotation schedule |
| `duty_invites` | Invites for the weekly duty rotation slots |

**Orders & payments**
| Table | Purpose |
|---|---|
| `meal_orders` | One row per (student, date, lunch/dinner) — the actual meal bookings |
| `payments` | Manually-submitted bKash transaction records awaiting manager verification |
| `cash_payment_requests` | Cash payment claims awaiting manager confirmation |
| `bkash_gateway_sessions` | Automated bKash **gateway** checkout sessions (the "Pay with bKash" button flow) |
| `bkash_proposals` / `bkash_proposal_votes` / `weekly_bkash` | The manager team's weekly bKash-number proposal/approval workflow |

**Requests & admin**
| Table | Purpose |
|---|---|
| `meal_edit_requests` | Student requests to edit/cancel an already-placed order |
| `floor_change_requests` | Student requests to change hostel/floor assignment |
| `phone_change_requests` | Student requests to change their registered bKash number |
| `ordering_unlock_requests` | Student requests to lift a manager-set ordering lock |
| `registration_codes` | Invite codes used to gate new student registration |
| `admin_reset_log` | Audit log of admin actions (password resets, unlocks, etc.) |
| `site_settings` | Misc key/value app settings (e.g. maintenance mode) |

### Default seeded accounts

On first boot (empty database), `init_db()` seeds exactly two logins —
**change both immediately** if you didn't already set the env vars below:

| Account | Default login ID | Default password | Overridden by env var |
|---|---|---|---|
| Meal Manager | `MGR001` | `manager123` | `MANAGER_PASSWORD` |
| Admin | `DEVADMIN` | `nmms@dev2024!` | `ADMIN_PASSWORD` |

If `MANAGER_PASSWORD` / `ADMIN_PASSWORD` are set in your environment
**before** the very first boot, the app seeds the strong password directly
and you never touch the weak default at all — that's the recommended way,
already reflected in both the Railway and VPS steps above.

---

## Backups

**Railway:** use the **Backups** tab on the Postgres service if your plan
includes it, or run the manual command below via Railway's `psql` connect
button / any client using the connection string from the Postgres service's
**Connect** tab.

**VPS (manual, run anytime):**
```bash
sudo -u postgres pg_dump nmms > nmms_backup_$(date +%F).sql
```

**VPS (automatic daily backup via cron):**
```bash
sudo crontab -e
# add this line — runs every day at 3 AM, keeps files in /var/backups/nmms
0 3 * * * mkdir -p /var/backups/nmms && sudo -u postgres pg_dump nmms | gzip > /var/backups/nmms/nmms_$(date +\%F).sql.gz
```

**Restoring a backup:**
```bash
# plain .sql
sudo -u postgres psql nmms < nmms_backup_2026-09-23.sql
# gzipped
gunzip -c nmms_2026-09-23.sql.gz | sudo -u postgres psql nmms
```

---

## Migrating data between Railway and a VPS (either direction)

Since both are plain Postgres, moving your data is just a dump-and-restore:

```bash
# 1. Dump from the SOURCE database (use its DATABASE_URL)
pg_dump "postgresql://user:pass@source-host:5432/dbname" > migration.sql

# 2. Restore into the DESTINATION database (use its DATABASE_URL)
psql "postgresql://user:pass@dest-host:5432/dbname" < migration.sql
```

Do this with the app **stopped** on both ends (or at least not accepting
writes) so you don't dump a database mid-write and end up with an
inconsistent snapshot.

---

## Security notes

- Never commit `DATABASE_URL` (or any `.env`/`nmms.env` file containing it)
  to git — both this project's `.gitignore` and your own habits should keep
  real credentials out of version control.
- On the VPS, keep Postgres listening on `localhost` only (the default) and
  reach it exclusively from the app running on the same machine. There's no
  reason for `5432` to be reachable from the internet for this app's setup.
- Use a dedicated database user (`nmms_user`) with privileges scoped to just
  the `nmms` database — never point the app at the `postgres` superuser
  account.
- Rotate the database password if it's ever been shared, pasted somewhere
  public, or you're unsure who's seen `nmms.env` — update it in both
  Postgres (`ALTER USER nmms_user WITH PASSWORD '...'`) and `DATABASE_URL`,
  then restart the app.
