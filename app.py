from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, make_response
import os, re
from datetime import datetime, timedelta, date
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── PostgreSQL via psycopg2 ───────────────────────────────────────────────────
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError as PgIntegrityError

app = Flask(__name__)

# ── Trust reverse-proxy headers (Railway / Render / Heroku all set these) ─────
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ── Security config ───────────────────────────────────────────────────────────
import secrets as _secrets

_secret = os.environ.get('SECRET_KEY')
if not _secret:
    _key_file = os.path.join(os.path.dirname(__file__), '.secret_key')
    if os.path.exists(_key_file):
        with open(_key_file) as f:
            _secret = f.read().strip()
    else:
        _secret = _secrets.token_hex(32)
        with open(_key_file, 'w') as f:
            f.write(_secret)
        print(f"[NMMS] Generated new SECRET_KEY and saved to {_key_file}")

app.secret_key = _secret

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Only set Secure flag when running over HTTPS (Railway/Render set FLASK_ENV=production AND serve HTTPS)
# Using HTTPS env var as the actual signal avoids locking out HTTP dev/local testing
_is_https = os.environ.get('HTTPS', '').lower() in ('1', 'true', 'on') or \
            os.environ.get('DYNO') or os.environ.get('RAILWAY_ENVIRONMENT') or \
            os.environ.get('RENDER')
app.config['SESSION_COOKIE_SECURE']   = bool(_is_https)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)   # "remember me" default

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri='memory://',
)

# ── Rate-limit error handler ──────────────────────────────────────────────────
from flask_limiter.errors import RateLimitExceeded

@app.errorhandler(RateLimitExceeded)
def handle_rate_limit(e):
    """Show a readable message instead of a blank 429 page."""
    if request.path == '/admin/login':
        flash('Too many login attempts. Please wait a minute and try again.', 'error')
        return redirect(url_for('admin_login'))
    return jsonify({'ok': False, 'msg': 'Rate limit exceeded. Try again later.'}), 429

# ── Database connection ───────────────────────────────────────────────────────
# Set DATABASE_URL in your environment (Railway/Render provide this automatically)
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/nmms')

# Railway sometimes gives postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

def get_db():
    """Return a new psycopg2 connection with RealDictCursor (rows behave like dicts)."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

class _DbCtx:
    """Context manager: auto-commits on success, always closes."""
    def __init__(self):
        self._conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    def __enter__(self):
        return self._conn
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        self._conn.close()
        return False

def db():
    return _DbCtx()

# ── Password helpers ──────────────────────────────────────────────────────────

def hash_pass(p):
    return generate_password_hash(p)

def verify_pass(stored_hash, provided_password):
    if stored_hash.startswith('pbkdf2:') or stored_hash.startswith('scrypt:'):
        return check_password_hash(stored_hash, provided_password)
    import hashlib
    return stored_hash == hashlib.sha256(provided_password.encode()).hexdigest()

# ── DB helper: run a query and return all rows ───────────────────────────────

def query(conn, sql, params=()):
    """Execute sql and return all rows as a list of dicts."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()

def queryOne(conn, sql, params=()):
    """Execute sql and return one row as a dict (or None)."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()

def execute(conn, sql, params=()):
    """Execute sql and return the cursor (for lastrowid via cur.fetchone())."""
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

# ── Schema init ───────────────────────────────────────────────────────────────

def init_db():
    conn = get_db()
    cur  = conn.cursor()

    # PostgreSQL uses SERIAL instead of INTEGER PRIMARY KEY AUTOINCREMENT
    # and NOW() / CURRENT_TIMESTAMP instead of datetime('now')
    # %s placeholders instead of ?
    # ON CONFLICT ... DO UPDATE instead of INSERT OR IGNORE / INSERT OR REPLACE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meal_managers (
            id                    SERIAL PRIMARY KEY,
            manager_id            TEXT UNIQUE NOT NULL,
            name                  TEXT NOT NULL,
            password              TEXT NOT NULL,
            bkash_number          TEXT NOT NULL,
            is_active             INTEGER DEFAULT 1,
            student_id            INTEGER DEFAULT NULL,
            temp_password_expires TEXT DEFAULT NULL,
            must_change_password  INTEGER DEFAULT 0,
            created_at            TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS manager_history (
            id           SERIAL PRIMARY KEY,
            manager_id   TEXT NOT NULL,
            student_name TEXT NOT NULL,
            roll_number  TEXT,
            batch        TEXT,
            floor        INTEGER,
            assigned_by  TEXT,
            tenure_start TEXT NOT NULL,
            tenure_end   TEXT,
            created_at   TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS manager_transfer_invites (
            id              SERIAL PRIMARY KEY,
            from_manager_id TEXT NOT NULL,
            to_student_id   INTEGER NOT NULL,
            status          TEXT DEFAULT 'pending',
            temp_password   TEXT,
            new_manager_id  TEXT,
            created_at      TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            responded_at    TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id             SERIAL PRIMARY KEY,
            name           TEXT NOT NULL,
            batch          TEXT NOT NULL,
            roll_number    TEXT UNIQUE NOT NULL,
            bkash_number   TEXT NOT NULL,
            password       TEXT NOT NULL,
            gender         TEXT DEFAULT 'male',
            floor          INTEGER DEFAULT 1,
            is_locked      INTEGER DEFAULT 0,
            is_demo        INTEGER DEFAULT 0,
            ordering_locked INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meal_orders (
            id             SERIAL PRIMARY KEY,
            student_id     INTEGER NOT NULL,
            meal_date      TEXT NOT NULL,
            meal_type      TEXT NOT NULL,
            payment_status TEXT DEFAULT 'pending',
            amount         REAL DEFAULT 50.0,
            ordered_at     TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            UNIQUE(student_id, meal_date, meal_type)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id             SERIAL PRIMARY KEY,
            student_id     INTEGER NOT NULL,
            amount         REAL NOT NULL,
            bkash_txn      TEXT,
            payment_date   TEXT NOT NULL,
            status         TEXT DEFAULT 'pending_verification',
            manager_bkash  TEXT,
            screenshot_note TEXT,
            verified_at    TEXT,
            verified_by    TEXT,
            created_at     TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cash_payment_requests (
            id           SERIAL PRIMARY KEY,
            student_id   INTEGER NOT NULL,
            amount       REAL NOT NULL,
            note         TEXT,
            status       TEXT DEFAULT 'pending',
            requested_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            reviewed_at  TEXT,
            reviewed_by  TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_accounts (
            id         SERIAL PRIMARY KEY,
            admin_id   TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_reset_log (
            id       SERIAL PRIMARY KEY,
            admin_id TEXT NOT NULL,
            action   TEXT NOT NULL,
            reset_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS site_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS manager_rotation (
            id           SERIAL PRIMARY KEY,
            week_start   TEXT NOT NULL,
            slot         INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 4),
            student_id   INTEGER,
            student_name TEXT,
            roll_number  TEXT,
            day_from     INTEGER NOT NULL DEFAULT 1,
            day_to       INTEGER NOT NULL DEFAULT 7,
            note         TEXT,
            created_at   TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bkash_proposals (
            id                  SERIAL PRIMARY KEY,
            proposer_manager_id INTEGER NOT NULL,
            proposed_bkash      TEXT NOT NULL,
            status              TEXT DEFAULT 'pending',
            week_start          TEXT,
            created_at          TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            resolved_at         TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS weekly_bkash (
            id           SERIAL PRIMARY KEY,
            week_start   TEXT NOT NULL UNIQUE,
            bkash_number TEXT NOT NULL,
            approved_at  TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            proposal_id  INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bkash_proposal_votes (
            id               SERIAL PRIMARY KEY,
            proposal_id      INTEGER NOT NULL,
            voter_manager_id INTEGER NOT NULL,
            vote             TEXT NOT NULL CHECK(vote IN ('approve','reject')),
            voted_at         TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
            UNIQUE(proposal_id, voter_manager_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS duty_invites (
            id            SERIAL PRIMARY KEY,
            week_start    TEXT NOT NULL,
            student_id    INTEGER NOT NULL,
            slot          INTEGER NOT NULL,
            duty_id       TEXT NOT NULL,
            duty_password TEXT NOT NULL,
            status        TEXT DEFAULT 'pending',
            accepted_at   TEXT,
            created_at    TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meal_edit_requests (
            id         SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL,
            meal_date  TEXT NOT NULL,
            meal_type  TEXT NOT NULL,
            action     TEXT NOT NULL,
            reason     TEXT DEFAULT '',
            status     TEXT DEFAULT 'pending',
            approved_at TEXT,
            expires_at  TEXT,
            decided_by  INTEGER,
            created_at  TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS registration_codes (
            id         SERIAL PRIMARY KEY,
            code       TEXT UNIQUE NOT NULL,
            batch      TEXT,
            created_by TEXT NOT NULL,
            note       TEXT DEFAULT '',
            is_used    INTEGER DEFAULT 0,
            used_by    TEXT,
            used_at    TEXT,
            created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS floor_change_requests (
            id              SERIAL PRIMARY KEY,
            student_id      INTEGER NOT NULL,
            current_floor   INTEGER NOT NULL,
            requested_floor INTEGER NOT NULL,
            reason          TEXT DEFAULT '',
            status          TEXT DEFAULT 'pending',
            reviewed_at     TEXT,
            reviewed_by     TEXT,
            created_at      TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS phone_change_requests (
            id         SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL,
            old_bkash  TEXT NOT NULL,
            new_bkash  TEXT NOT NULL,
            reason     TEXT DEFAULT '',
            status     TEXT DEFAULT 'pending',
            decided_by TEXT DEFAULT NULL,
            decided_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)

    conn.commit()

    # ── Safe migrations (ADD COLUMN if not exists) ───────────────────────────
    try:
        cur.execute("ALTER TABLE students ADD COLUMN debt_blocked INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        conn.rollback()   # column already exists — safe to ignore

    # ── Seed MGR001 ─────────────────────────────────────────────────────────
    existing_mgr = queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s", ('MGR001',))
    if not existing_mgr:
        mgr_pass  = os.environ.get('MANAGER_PASSWORD', 'manager123')
        mgr_bkash = os.environ.get('MANAGER_BKASH',    '01712345678')
        execute(conn,
            "INSERT INTO meal_managers (manager_id, name, password, bkash_number) VALUES (%s,%s,%s,%s)",
            ('MGR001', 'Meal Manager', hash_pass(mgr_pass), mgr_bkash)
        )
    else:
        env_pass  = os.environ.get('MANAGER_PASSWORD')
        env_bkash = os.environ.get('MANAGER_BKASH')
        if env_pass:
            execute(conn,
                "UPDATE meal_managers SET password=%s, must_change_password=0, temp_password_expires=NULL WHERE manager_id='MGR001'",
                (hash_pass(env_pass),)
            )
        if env_bkash:
            execute(conn, "UPDATE meal_managers SET bkash_number=%s WHERE manager_id='MGR001'", (env_bkash,))

    # ── Seed DEVADMIN ────────────────────────────────────────────────────────
    if not queryOne(conn, "SELECT id FROM admin_accounts WHERE admin_id=%s", ('DEVADMIN',)):
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')
        execute(conn,
            "INSERT INTO admin_accounts (admin_id, password) VALUES (%s,%s)",
            ('DEVADMIN', hash_pass(admin_pass))
        )

    # ── Remove ALL demo/test students permanently ────────────────────────────
    demo_rows = query(conn, "SELECT id FROM students WHERE is_demo=1 OR roll_number LIKE 'DEMO%%'")
    demo_ids  = [r['id'] for r in demo_rows]
    if demo_ids:
        ph = ','.join(['%s'] * len(demo_ids))
        execute(conn, f"DELETE FROM meal_orders           WHERE student_id IN ({ph})", demo_ids)
        execute(conn, f"DELETE FROM payments              WHERE student_id IN ({ph})", demo_ids)
        execute(conn, f"DELETE FROM cash_payment_requests WHERE student_id IN ({ph})", demo_ids)
        execute(conn, f"DELETE FROM students              WHERE id          IN ({ph})", demo_ids)
        print(f"[NMMS] Cleaned up {len(demo_ids)} demo student(s) on startup.")

    # ── Fix female students stored with text hostel names (old form bug) ─────
    # Old registration form sent "Campus"/"Sentu House"/"Chairman House" as text,
    # which failed int() conversion and silently defaulted to floor=1 for everyone.
    # This migration fixes any female student whose floor is NULL or invalid.
    try:
        bad_floor_females = query(conn,
            "SELECT id, name, floor FROM students WHERE gender='female' AND (floor IS NULL OR floor NOT IN (1,2,3))"
        )
        if bad_floor_females:
            for row in bad_floor_females:
                execute(conn, "UPDATE students SET floor=1 WHERE id=%s", (row['id'],))
            print(f"[NMMS] Fixed {len(bad_floor_females)} female student(s) with invalid hostel value (set to Campus=1). Please correct manually if needed.")
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[NMMS] Female floor migration skipped: {e}")

    conn.commit()
    conn.close()

# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session or session.get('role') != role:
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── Weekly bKash helper ───────────────────────────────────────────────────────

def get_current_weekly_bkash():
    try:
        today      = date.today()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        conn       = get_db()
        row = queryOne(conn,
            "SELECT bkash_number, week_start FROM weekly_bkash WHERE week_start=%s",
            (week_start,)
        )
        if row:
            conn.close()
            return {'bkash_number': row['bkash_number'], 'week_start': row['week_start'], 'approved': True}
        row = queryOne(conn,
            "SELECT bkash_number, week_start FROM weekly_bkash ORDER BY week_start DESC LIMIT 1"
        )
        if row:
            conn.close()
            return {'bkash_number': row['bkash_number'], 'week_start': row['week_start'], 'approved': True}
        mgr = queryOne(conn, "SELECT bkash_number FROM meal_managers WHERE is_active=1 LIMIT 1")
        conn.close()
        return {'bkash_number': mgr['bkash_number'] if mgr else '', 'week_start': week_start, 'approved': False}
    except Exception:
        return {'bkash_number': '', 'week_start': '', 'approved': False}

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

# ── STUDENT AUTH ──────────────────────────────────────────────────────────────

@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        d    = request.form
        conn = get_db()
        try:
            name = d.get('name', '').strip()[:150]
            if not name:
                flash('Full name is required.', 'error')
                return render_template('student_register.html')

            bkash = d.get('bkash_number', '').strip()
            if not re.match(r'^01[3-9]\d{8}$', bkash):
                flash('Invalid bKash number. Must be 11 digits starting with 013–019.', 'error')
                return render_template('student_register.html')

            password = d.get('password', '')
            if len(password) < 6:
                flash('Password must be at least 6 characters.', 'error')
                return render_template('student_register.html')

            gender    = d.get('gender', 'male')
            if gender == 'female':
                # Female students choose a hostel: 1=Campus, 2=Sentu House, 3=Chairman House
                try:
                    floor_val = int(d.get('floor') or 1)
                    if floor_val not in (1, 2, 3):
                        floor_val = 1
                except (TypeError, ValueError):
                    floor_val = 1
            else:
                floor_val = int(d.get('floor') or 1)

            try:
                batch_num = int(d['batch'])
            except (ValueError, KeyError):
                flash('Please select a valid batch.', 'error')
                return render_template('student_register.html')

            current_year = datetime.now().year
            max_batch    = min(8 + max(0, current_year - 2026), 20)
            if batch_num < 2 or batch_num > max_batch:
                flash(f'Batch {batch_num} is not available. Currently batches 2–{max_batch} are open.', 'error')
                return render_template('student_register.html')

            try:
                roll_raw = int(d['roll_number'])
            except (ValueError, KeyError):
                flash('Roll number must be a number between 1 and 75.', 'error')
                return render_template('student_register.html')

            if roll_raw < 1 or roll_raw > 75:
                flash('Roll number must be between 1 and 75.', 'error')
                return render_template('student_register.html')

            roll_number = f'{batch_num}-{roll_raw}'
            execute(conn,
                "INSERT INTO students (name,batch,roll_number,bkash_number,password,gender,floor) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (name, str(batch_num), roll_number, bkash, hash_pass(password), gender, floor_val)
            )
            conn.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('student_login'))
        except PgIntegrityError:
            conn.rollback()
            flash('This roll number for this batch is already registered.', 'error')
        finally:
            conn.close()
    return render_template('student_register.html')


def check_and_apply_due_lock(conn, student_id):
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
    overdue = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due') AND meal_date<%s",
        (student_id, seven_days_ago)
    )['c']
    if overdue > 0:
        execute(conn, "UPDATE students SET is_locked=1 WHERE id=%s", (student_id,))
        conn.commit()
        return True
    return False


@app.route('/student/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 50 per hour")
def student_login():
    if request.method == 'POST':
        d    = request.form
        conn = get_db()
        row  = queryOne(conn, "SELECT * FROM students WHERE roll_number=%s", (d['roll_number'],))
        if row and verify_pass(row['password'], d['password']):
            if not (row['password'].startswith('pbkdf2:') or row['password'].startswith('scrypt:')):
                execute(conn, "UPDATE students SET password=%s WHERE id=%s", (hash_pass(d['password']), row['id']))
                conn.commit()
            check_and_apply_due_lock(conn, row['id'])
            row = queryOne(conn, "SELECT * FROM students WHERE id=%s", (row['id'],))
            conn.close()
            if row['is_locked']:
                flash('⚠️ Your account is locked due to unpaid dues older than 7 days. Please contact the Meal Manager.', 'error')
                return render_template('student_login.html')
            remember_me       = request.form.get('remember_me') in ('1', 'on')
            session.permanent = remember_me
            session['user_id'] = row['id']
            session['role']    = 'student'
            session['name']    = row['name']
            session['roll']    = row['roll_number']
            return redirect(url_for('student_dashboard'))
        else:
            conn.close()
        flash('Invalid credentials.', 'error')
    return render_template('student_login.html')


@app.route('/student/reset_password', methods=['GET', 'POST'])
def student_reset_password():
    if request.method == 'POST':
        roll         = request.form.get('roll_number', '').strip()
        bkash        = request.form.get('bkash_number', '').strip()
        new_pass     = request.form.get('new_password', '').strip()
        confirm_pass = request.form.get('confirm_password', '').strip()
        if not roll or not bkash or not new_pass:
            flash('All fields are required.', 'error')
            return render_template('student_reset_password.html')
        if new_pass != confirm_pass:
            flash('Passwords do not match.', 'error')
            return render_template('student_reset_password.html')
        if len(new_pass) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('student_reset_password.html')
        conn    = get_db()
        student = queryOne(conn, "SELECT id FROM students WHERE roll_number=%s AND bkash_number=%s", (roll, bkash))
        if student:
            execute(conn, "UPDATE students SET password=%s WHERE id=%s", (hash_pass(new_pass), student['id']))
            conn.commit()
            conn.close()
            flash('Password reset successful! Please login.', 'success')
            return redirect(url_for('student_login'))
        conn.close()
        flash('Roll number and bKash number do not match our records.', 'error')
    return render_template('student_reset_password.html')


@app.route('/student/logout')
def student_logout():
    session.clear()
    return redirect(url_for('index'))

# ── STUDENT DASHBOARD ─────────────────────────────────────────────────────────

@app.route('/student/dashboard')
@login_required('student')
def student_dashboard():
    conn  = get_db()
    sid   = session['user_id']
    today = (datetime.utcnow() + timedelta(hours=6)).date()
    # Fixed weekly cycle — always anchored to Sunday.
    # The grid shows the same 7 days (Sun–Sat) for everyone until the week flips.
    # Python weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    WEEK_START_DAY   = 6   # 6 = Sunday
    days_since_start = (today.weekday() - WEEK_START_DAY) % 7
    week_start_date  = today - timedelta(days=days_since_start)
    week_end_date    = week_start_date + timedelta(days=6)
    week_start  = week_start_date.isoformat()
    week_end    = week_end_date.isoformat()
    week_dates  = [(week_start_date + timedelta(days=i)).isoformat() for i in range(7)]

    # 3-day payment window: ordering allowed only Sun(0), Mon(1), Tue(2)
    days_into_week  = days_since_start
    pay_window_open = days_into_week <= 2

    orders = query(conn,
        "SELECT meal_date, meal_type, payment_status, amount FROM meal_orders "
        "WHERE student_id=%s AND meal_date>=%s AND meal_date<=%s ORDER BY meal_date",
        (sid, week_start, week_end)
    )
    order_map = {(r['meal_date'], r['meal_type']): r for r in orders}

    payments = query(conn,
        "SELECT * FROM payments WHERE student_id=%s ORDER BY created_at DESC LIMIT 20", (sid,)
    )

    mgr = get_current_weekly_bkash()

    _mc = get_db()
    _mr = queryOne(_mc, "SELECT manager_id, name, student_id FROM meal_managers WHERE is_active=1 LIMIT 1")
    mgr_name_val  = _mr['name']       if _mr else ''
    mgr_login_id  = _mr['manager_id'] if _mr else ''
    mgr_roll_val  = ''
    mgr_batch_val = ''
    if _mr and _mr['student_id']:
        _sr = queryOne(_mc, "SELECT roll_number, batch FROM students WHERE id=%s", (_mr['student_id'],))
        if _sr:
            mgr_roll_val  = _sr['roll_number']
            mgr_batch_val = _sr['batch']
    _mc.close()

    pending_due = queryOne(conn,
        "SELECT SUM(amount) as total FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')", (sid,)
    )
    due_only = queryOne(conn,
        "SELECT SUM(amount) as total, COUNT(*) as cnt FROM meal_orders WHERE student_id=%s AND payment_status='due'", (sid,)
    )
    month_ago   = (today - timedelta(days=30)).isoformat()
    overdue_old = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due') AND meal_date<%s",
        (sid, month_ago)
    )['c']

    student_row     = queryOne(conn, "SELECT * FROM students WHERE id=%s", (sid,))
    ordering_locked = bool(student_row and student_row['ordering_locked'])
    debt_blocked    = bool(student_row and student_row.get('debt_blocked'))
    student_bkash   = student_row['bkash_number'] if student_row else ''

    pending_payment_row = queryOne(conn,
        "SELECT bkash_txn, amount, created_at FROM payments WHERE student_id=%s AND status='pending_verification' ORDER BY created_at DESC LIMIT 1",
        (sid,)
    )
    has_pending_payment = pending_payment_row is not None
    pending_payment_txn = pending_payment_row['bkash_txn'] if pending_payment_row else ''

    pending_cash_row = queryOne(conn,
        "SELECT amount, note, requested_at FROM cash_payment_requests WHERE student_id=%s AND status='pending' ORDER BY requested_at DESC LIMIT 1",
        (sid,)
    )
    has_pending_cash    = pending_cash_row is not None
    pending_cash_amount = pending_cash_row['amount'] if pending_cash_row else 0

    conn.close()
    return render_template('student_dashboard.html',
        week_dates      = week_dates,
        order_map       = order_map,
        payments        = payments,
        mgr_bkash       = mgr['bkash_number'],
        mgr_name        = mgr_name_val,
        mgr_roll        = mgr_roll_val,
        mgr_batch       = mgr_batch_val,
        mgr_login_id    = mgr_login_id,
        bkash_week      = mgr['week_start'],
        bkash_approved  = mgr['approved'],
        due             = pending_due['total'] or 0,
        due_marked      = due_only['total']    or 0,
        due_cnt         = due_only['cnt']      or 0,
        meal_locked     = overdue_old > 0,
        ordering_open   = True,
        ordering_locked = ordering_locked,
        week_deadline   = week_end,
        today_str       = today.isoformat(),
        student_bkash   = student_bkash,
        has_pending_payment = has_pending_payment,
        pending_payment_txn = pending_payment_txn,
        has_pending_cash    = has_pending_cash,
        pending_cash_amount = pending_cash_amount,
        debt_blocked        = debt_blocked,
        pay_window_open     = pay_window_open,
    )


@app.route('/student/order', methods=['POST'])
@login_required('student')
def student_order():
    d        = request.json
    sid      = session['user_id']
    conn     = get_db()
    today_bd = (datetime.utcnow() + timedelta(hours=6)).date()

    student_info = queryOne(conn, "SELECT ordering_locked, debt_blocked FROM students WHERE id=%s", (sid,))
    if student_info and student_info['ordering_locked']:
        conn.close()
        return jsonify({'ok': False, 'msg': '🔒 The meal manager has locked your weekly meal ordering.', 'locked': True})
    if student_info and student_info['debt_blocked']:
        conn.close()
        return jsonify({'ok': False, 'msg': '🚫 Your meal ordering is blocked due to an unpaid bill from a previous period. Please clear your old balance first.'})

    meal_date_str = d.get('meal_date', '')

    if meal_date_str < today_bd.isoformat():
        conn.close()
        return jsonify({'ok': False, 'msg': '⛔ You cannot order meals for past dates.'})

    # ── MIDNIGHT DEADLINE CHECK ──────────────────────────────────────────────
    # To book a meal for Day X, the student must order before 00:00 AM (midnight)
    # of Day X — i.e. right now (BD time) must be before midnight of meal_date.
    # Today's meals are always open until midnight tonight.
    try:
        order_date  = datetime.strptime(meal_date_str, '%Y-%m-%d').date()
        now_bd      = datetime.utcnow() + timedelta(hours=6)  # Bangladesh time (UTC+6)
        deadline_dt = datetime(order_date.year, order_date.month, order_date.day, 0, 0, 0)
        if now_bd >= deadline_dt:
            conn.close()
            return jsonify({
                'ok':    False,
                'msg':   f'⏰ Ordering deadline passed — meals for {order_date.strftime("%d %b")} had to be booked before midnight. You can still order upcoming days.',
                'locked': True
            })
    except ValueError:
        conn.close()
        return jsonify({'ok': False, 'msg': '⛔ Invalid date format.'})

    WEEK_START_DAY   = 6   # 6 = Sunday (must match student_dashboard())
    days_since_start = (today_bd.weekday() - WEEK_START_DAY) % 7
    week_start_bd    = today_bd - timedelta(days=days_since_start)
    week_end_bd      = week_start_bd + timedelta(days=6)
    if meal_date_str > week_end_bd.isoformat():
        conn.close()
        return jsonify({'ok': False, 'msg': '📅 You can only order meals within the current week (Sun–Sat).'})

    # 3-day payment window: new orders only allowed Sun(0), Mon(1), Tue(2)
    days_into_week = days_since_start
    if days_into_week > 2:
        conn.close()
        return jsonify({'ok': False, 'msg': '🔒 Ordering window closed. New meals can only be ordered on Sunday, Monday, and Tuesday. The window reopens next Sunday.'})

    month_ago = (today_bd - timedelta(days=30)).isoformat()
    overdue = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due') AND meal_date<%s",
        (sid, month_ago)
    )['c']
    if overdue > 0:
        conn.close()
        return jsonify({'ok': False, 'msg': '🔒 Your meal ordering is locked due to unpaid meals older than 30 days.', 'locked': True})

    try:
        execute(conn,
            "INSERT INTO meal_orders (student_id,meal_date,meal_type,payment_status) VALUES (%s,%s,%s,'pending') ON CONFLICT DO NOTHING",
            (sid, meal_date_str, d['meal_type'])
        )
        conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        conn.close()


@app.route('/student/cancel_order', methods=['POST'])
@login_required('student')
def cancel_order():
    d        = request.json
    sid      = session['user_id']
    today_bd = (datetime.utcnow() + timedelta(hours=6)).date()
    conn     = get_db()
    meal_date = d.get('meal_date', '')
    meal_type = d.get('meal_type', '')
    if meal_date < today_bd.isoformat():
        conn.close()
        return jsonify({'ok': False, 'msg': 'Cannot cancel a past meal order.'})
    dup = queryOne(conn,
        "SELECT id FROM meal_edit_requests WHERE student_id=%s AND meal_date=%s AND meal_type=%s AND status='pending'",
        (sid, meal_date, meal_type)
    )
    if dup:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending cancel request for this meal.'})
    execute(conn,
        "INSERT INTO meal_edit_requests (student_id,meal_date,meal_type,action,reason) VALUES (%s,%s,%s,'cancel','Student requested cancellation')",
        (sid, meal_date, meal_type)
    )
    conn.commit()

    # Auto-cancel any pending cash payment request for this student
    cash_cancelled = False
    cash_row = queryOne(conn,
        "SELECT id FROM cash_payment_requests WHERE student_id=%s AND status='pending' ORDER BY requested_at DESC LIMIT 1",
        (sid,)
    )
    if cash_row:
        execute(conn, "UPDATE cash_payment_requests SET status='cancelled' WHERE id=%s", (cash_row['id'],))
        conn.commit()
        cash_cancelled = True

    # Auto-cancel any pending bKash payment for this student
    bkash_cancelled = False
    bkash_row = queryOne(conn,
        "SELECT id FROM payments WHERE student_id=%s AND status='pending_verification' ORDER BY created_at DESC LIMIT 1",
        (sid,)
    )
    if bkash_row:
        execute(conn, "UPDATE payments SET status='cancelled' WHERE id=%s", (bkash_row['id'],))
        conn.commit()
        bkash_cancelled = True

    conn.close()
    return jsonify({'ok': True, 'via_request': True, 'cash_cancelled': cash_cancelled, 'bkash_cancelled': bkash_cancelled, 'msg': '📩 Cancel request sent to manager for approval.'})


@app.route('/student/request_meal_edit', methods=['POST'])
@login_required('student')
def request_meal_edit():
    d      = request.json
    sid    = session['user_id']
    dt     = d.get('meal_date', '')
    mtype  = d.get('meal_type', '')
    action = d.get('action', '')
    reason = (d.get('reason') or '').strip()[:200]
    today_bd = (datetime.utcnow() + timedelta(hours=6)).date()
    if not dt or not mtype or action not in ('cancel', 'add'):
        return jsonify({'ok': False, 'msg': 'Invalid request.'})
    if dt < today_bd.isoformat():
        return jsonify({'ok': False, 'msg': 'Cannot request edit for a past date.'})
    conn = get_db()
    dup = queryOne(conn,
        "SELECT id FROM meal_edit_requests WHERE student_id=%s AND meal_date=%s AND meal_type=%s AND status='pending'",
        (sid, dt, mtype)
    )
    if dup:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending request for this meal.'})
    execute(conn,
        "INSERT INTO meal_edit_requests (student_id,meal_date,meal_type,action,reason) VALUES (%s,%s,%s,%s,%s)",
        (sid, dt, mtype, action, reason)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Request sent to manager.'})


@app.route('/student/edit_request_status')
@login_required('student')
def edit_request_status():
    sid  = session['user_id']
    now  = (datetime.utcnow() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    rows = query(conn,
        "SELECT id, meal_date, meal_type, action, reason, status, approved_at, expires_at, created_at "
        "FROM meal_edit_requests "
        "WHERE student_id=%s AND status IN ('pending','approved') AND (expires_at IS NULL OR expires_at > %s) "
        "ORDER BY created_at DESC",
        (sid, now)
    )
    conn.close()
    return jsonify({'requests': [dict(r) for r in rows]})


@app.route('/student/execute_meal_edit', methods=['POST'])
@login_required('student')
def execute_meal_edit():
    d      = request.json
    sid    = session['user_id']
    req_id = d.get('request_id')
    now    = (datetime.utcnow() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
    conn   = get_db()
    req = queryOne(conn,
        "SELECT * FROM meal_edit_requests WHERE id=%s AND student_id=%s AND status='approved' AND (expires_at IS NULL OR expires_at > %s)",
        (req_id, sid, now)
    )
    if not req:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Permission not found or expired.'})
    meal_date = req['meal_date']
    meal_type = req['meal_type']
    action    = req['action']
    if action == 'cancel':
        execute(conn,
            "DELETE FROM meal_orders WHERE student_id=%s AND meal_date=%s AND meal_type=%s AND payment_status='pending'",
            (sid, meal_date, meal_type)
        )
    else:
        student_info = queryOne(conn, "SELECT ordering_locked, is_locked FROM students WHERE id=%s", (sid,))
        if student_info and student_info['ordering_locked']:
            conn.close()
            return jsonify({'ok': False, 'msg': '🔒 Your ordering is locked by the manager.'})
        existing = queryOne(conn,
            "SELECT id FROM meal_orders WHERE student_id=%s AND meal_date=%s AND meal_type=%s",
            (sid, meal_date, meal_type)
        )
        if not existing:
            execute(conn,
                "INSERT INTO meal_orders (student_id,meal_date,meal_type,amount,payment_status) VALUES (%s,%s,%s,50,'pending')",
                (sid, meal_date, meal_type)
            )
    execute(conn, "UPDATE meal_edit_requests SET status='used' WHERE id=%s", (req_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/manager/edit_requests')
@login_required('manager')
def manager_edit_requests():
    conn = get_db()
    rows = query(conn,
        "SELECT r.id, r.meal_date, r.meal_type, r.action, r.reason, r.status, r.created_at, "
        "s.name as student_name, s.roll_number, s.batch "
        "FROM meal_edit_requests r JOIN students s ON s.id=r.student_id "
        "WHERE r.status='pending' ORDER BY r.created_at ASC"
    )
    conn.close()
    return jsonify({'requests': [dict(r) for r in rows]})


@app.route('/manager/decide_edit_request', methods=['POST'])
@login_required('manager')
def decide_edit_request():
    d       = request.json
    req_id  = d.get('request_id')
    verdict = d.get('verdict')
    if verdict not in ('approved', 'rejected'):
        return jsonify({'ok': False, 'msg': 'Invalid verdict.'})
    now     = (datetime.utcnow() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')
    expires = (datetime.utcnow() + timedelta(hours=6, minutes=120)).strftime('%Y-%m-%d %H:%M:%S')
    conn    = get_db()
    req = queryOne(conn, "SELECT * FROM meal_edit_requests WHERE id=%s AND status='pending'", (req_id,))
    if not req:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request not found or already decided.'})
    if verdict == 'approved' and req['action'] == 'cancel':
        execute(conn,
            "DELETE FROM meal_orders WHERE student_id=%s AND meal_date=%s AND meal_type=%s AND payment_status='pending'",
            (req['student_id'], req['meal_date'], req['meal_type'])
        )
        execute(conn,
            "UPDATE meal_edit_requests SET status='used', approved_at=%s, decided_by=%s WHERE id=%s",
            (now, session['user_id'], req_id)
        )
    else:
        execute(conn,
            "UPDATE meal_edit_requests SET status=%s, approved_at=%s, expires_at=%s, decided_by=%s WHERE id=%s",
            (verdict, now, expires if verdict == 'approved' else None, session['user_id'], req_id)
        )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'verdict': verdict})


@app.route('/student/mark_due', methods=['POST'])
@login_required('student')
def mark_due():
    sid = session['user_id']
    with db() as conn:
        execute(conn,
            "UPDATE meal_orders SET payment_status='due' WHERE student_id=%s AND payment_status='pending'", (sid,)
        )
    return jsonify({'ok': True, 'msg': 'Marked as due.'})


@app.route('/student/submit_payment', methods=['POST'])
@login_required('student')
def submit_payment():
    d         = request.json
    sid       = session['user_id']
    bkash_txn = d.get('bkash_txn', '').strip()
    note      = d.get('note', '').strip()
    if not bkash_txn:
        return jsonify({'ok': False, 'msg': 'Transaction ID is required.'})
    conn = get_db()
    try:
        # ── DUPLICATE GUARD 1: student already has a pending payment ─────────
        existing_pending = queryOne(conn,
            "SELECT id, bkash_txn FROM payments WHERE student_id=%s AND status='pending_verification'", (sid,)
        )
        if existing_pending:
            return jsonify({'ok': False,
                'msg': f"You already have a payment pending verification (TxnID: {existing_pending['bkash_txn']}). Wait for the manager to verify it first."})

        # ── DUPLICATE GUARD 2: same TxnID already exists anywhere in system ──
        existing_txn = queryOne(conn,
            "SELECT id FROM payments WHERE bkash_txn=%s", (bkash_txn,)
        )
        if existing_txn:
            return jsonify({'ok': False,
                'msg': f'Transaction ID "{bkash_txn}" has already been submitted. If this is an error, contact your manager.'})

        row = queryOne(conn,
            "SELECT COUNT(*) as cnt FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')", (sid,)
        )
        amount = (row['cnt'] or 0) * 50.0
        if amount <= 0:
            return jsonify({'ok': False, 'msg': 'No unpaid meals found.'})
        weekly = get_current_weekly_bkash()
        execute(conn,
            "INSERT INTO payments (student_id,amount,bkash_txn,payment_date,status,manager_bkash,screenshot_note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (sid, amount, bkash_txn, date.today().isoformat(), 'pending_verification', weekly['bkash_number'], note)
        )
        conn.commit()
        return jsonify({'ok': True, 'msg': 'Payment proof submitted! Awaiting manager verification.'})
    finally:
        conn.close()


@app.route('/student/request_cash_payment', methods=['POST'])
@login_required('student')
def request_cash_payment():
    d    = request.json
    sid  = session['user_id']
    note = d.get('note', '').strip()
    conn = get_db()
    row  = queryOne(conn,
        "SELECT COUNT(*) as cnt FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')", (sid,)
    )
    amount = (row['cnt'] or 0) * 50.0
    if amount <= 0:
        conn.close()
        return jsonify({'ok': False, 'msg': 'No unpaid meals found.'})
    existing = queryOne(conn, "SELECT id FROM cash_payment_requests WHERE student_id=%s AND status='pending'", (sid,))
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending cash request.'})
    execute(conn,
        "INSERT INTO cash_payment_requests (student_id,amount,note) VALUES (%s,%s,%s)",
        (sid, amount, note)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Cash payment request of ৳{int(amount)} sent!'})


@app.route('/student/cash_request_status')
@login_required('student')
def cash_request_status():
    sid  = session['user_id']
    conn = get_db()
    req  = queryOne(conn,
        "SELECT * FROM cash_payment_requests WHERE student_id=%s AND status='pending' ORDER BY requested_at DESC LIMIT 1", (sid,)
    )
    if not req:
        req = queryOne(conn,
            "SELECT * FROM cash_payment_requests WHERE student_id=%s ORDER BY requested_at DESC LIMIT 1", (sid,)
        )
    conn.close()
    if req:
        return jsonify({'ok': True, 'request': dict(req)})
    return jsonify({'ok': True, 'request': None})


@app.route('/student/unpaid_meal_count')
@login_required('student')
def student_unpaid_meal_count():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn,
        "SELECT COUNT(*) as cnt FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')", (sid,)
    )
    conn.close()
    cnt = row['cnt'] or 0
    return jsonify({'ok': True, 'count': cnt, 'amount': cnt * 50})


@app.route('/student/bkash_payment_status')
@login_required('student')
def bkash_payment_status():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn,
        "SELECT id, bkash_txn, amount, created_at FROM payments "
        "WHERE student_id=%s AND status='pending_verification' ORDER BY created_at DESC LIMIT 1", (sid,)
    )
    conn.close()
    if row:
        return jsonify({'ok': True, 'pending': True, 'payment': dict(row)})
    return jsonify({'ok': True, 'pending': False, 'payment': None})


@app.route('/student/cancel_bkash_payment', methods=['POST'])
@login_required('student')
def cancel_bkash_payment():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn,
        "SELECT id FROM payments WHERE student_id=%s AND status='pending_verification'", (sid,)
    )
    if not row:
        conn.close()
        return jsonify({'ok': False, 'msg': 'No pending bKash payment found.'})
    execute(conn,
        "DELETE FROM payments WHERE id=%s AND student_id=%s AND status='pending_verification'",
        (row['id'], sid)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'bKash payment submission cancelled.'})


@app.route('/student/update_bkash', methods=['POST'])
@login_required('student')
def student_update_bkash():
    d         = request.json or {}
    new_bkash = d.get('bkash_number', '').strip()
    password  = d.get('password', '').strip()
    if not new_bkash or not password:
        return jsonify({'ok': False, 'msg': 'bKash number and password are required.'})
    if not re.match(r'^01[3-9]\d{8}$', new_bkash):
        return jsonify({'ok': False, 'msg': 'Invalid bKash number.'})
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn, "SELECT id, password FROM students WHERE id=%s", (sid,))
    if not row or not verify_pass(row['password'], password):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Incorrect password.'})
    execute(conn, "UPDATE students SET bkash_number=%s WHERE id=%s", (new_bkash, sid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Your bKash number has been updated to {new_bkash}.'})


@app.route('/student/request_phone_change', methods=['POST'])
@login_required('student')
def student_request_phone_change():
    d         = request.json or {}
    new_bkash = d.get('new_bkash', '').strip()
    password  = d.get('password', '').strip()
    reason    = d.get('reason', '').strip()[:300]
    if not new_bkash or not password:
        return jsonify({'ok': False, 'msg': 'New bKash number and password are required.'})
    if not re.match(r'^01[3-9]\d{8}$', new_bkash):
        return jsonify({'ok': False, 'msg': 'Invalid bKash number.'})
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn, "SELECT id, password, bkash_number FROM students WHERE id=%s", (sid,))
    if not row or not verify_pass(row['password'], password):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Incorrect password.'})
    if row['bkash_number'] == new_bkash:
        conn.close()
        return jsonify({'ok': False, 'msg': 'New number is the same as current bKash number.'})
    existing = queryOne(conn, "SELECT id FROM phone_change_requests WHERE student_id=%s AND status='pending'", (sid,))
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending request.'})
    execute(conn,
        "INSERT INTO phone_change_requests (student_id,old_bkash,new_bkash,reason) VALUES (%s,%s,%s,%s)",
        (sid, row['bkash_number'], new_bkash, reason)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Request submitted! Admin will review it.'})


@app.route('/student/phone_change_status')
@login_required('student')
def student_phone_change_status():
    sid  = session['user_id']
    conn = get_db()
    rows = query(conn,
        "SELECT id, old_bkash, new_bkash, reason, status, decided_at, created_at "
        "FROM phone_change_requests WHERE student_id=%s ORDER BY created_at DESC LIMIT 10", (sid,)
    )
    conn.close()
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})

# ── MANAGER AUTH ──────────────────────────────────────────────────────────────

@app.route('/manager/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 50 per hour")
def manager_login():
    if request.method == 'POST':
        d    = request.form
        conn = get_db()
        row  = queryOne(conn,
            "SELECT * FROM meal_managers WHERE manager_id=%s AND is_active=1", (d['manager_id'],)
        )
        if row and verify_pass(row['password'], d['password']):
            if not (row['password'].startswith('pbkdf2:') or row['password'].startswith('scrypt:')):
                execute(conn, "UPDATE meal_managers SET password=%s WHERE id=%s",
                        (hash_pass(d['password']), row['id']))
                conn.commit()
            if row['temp_password_expires']:
                if datetime.now() > datetime.strptime(row['temp_password_expires'], '%Y-%m-%d %H:%M:%S'):
                    conn.close()
                    flash('Your temporary password has expired. Please reset your password.', 'error')
                    return render_template('manager_login.html')
            display_name = row['name']
            if row['student_id']:
                s_row = queryOne(conn, "SELECT roll_number FROM students WHERE id=%s", (row['student_id'],))
                if s_row:
                    display_name = f"{row['name']} ({s_row['roll_number']})"
            remember_me       = request.form.get('remember_me') in ('1', 'on')
            session.permanent = remember_me
            session['user_id']          = row['id']
            session['role']             = 'manager'
            session['name']             = display_name
            session['mgr_bkash']        = row['bkash_number']
            session['must_change_pass'] = bool(row['must_change_password'])
            if row['must_change_password']:
                conn.close()
                flash('Welcome! Please set a new password.', 'success')
                return redirect(url_for('manager_change_password'))
            conn.close()
            return redirect(url_for('manager_dashboard'))
        conn.close()
        flash('Invalid credentials.', 'error')
    return render_template('manager_login.html')


@app.route('/manager/reset_password', methods=['GET', 'POST'])
def manager_reset_password():
    if request.method == 'POST':
        manager_id   = request.form.get('manager_id', '').strip()
        reset_code   = request.form.get('reset_code', '').strip()
        new_pass     = request.form.get('new_password', '').strip()
        confirm_pass = request.form.get('confirm_password', '').strip()
        if not manager_id or not reset_code or not new_pass:
            flash('All fields are required.', 'error')
            return render_template('manager_reset_password.html')
        if new_pass != confirm_pass:
            flash('Passwords do not match.', 'error')
            return render_template('manager_reset_password.html')
        if len(new_pass) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('manager_reset_password.html')
        conn      = get_db()
        today_str = datetime.utcnow().isoformat()
        mgr = queryOne(conn,
            "SELECT id, password FROM meal_managers "
            "WHERE manager_id=%s AND is_active=1 AND temp_password_expires IS NOT NULL AND temp_password_expires > %s",
            (manager_id, today_str)
        )
        if mgr and verify_pass(mgr['password'], reset_code):
            execute(conn,
                "UPDATE meal_managers SET password=%s, must_change_password=0, temp_password_expires=NULL WHERE id=%s",
                (hash_pass(new_pass), mgr['id'])
            )
            execute(conn,
                "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
                ('self_reset', f'Manager {manager_id} used reset code to change password')
            )
            conn.commit()
            conn.close()
            flash('Password reset successful! Please login.', 'success')
            return redirect(url_for('manager_login'))
        conn.close()
        flash('Invalid Manager ID or reset code.', 'error')
    return render_template('manager_reset_password.html')


@app.route('/manager/logout')
def manager_logout():
    session.clear()
    return redirect(url_for('index'))

# ── MANAGER DASHBOARD ─────────────────────────────────────────────────────────

@app.route('/manager/dashboard')
@login_required('manager')
def manager_dashboard():
    conn  = get_db()
    today = date.today().isoformat()

    total_students = queryOne(conn, "SELECT COUNT(*) as c FROM students")['c']
    today_lunch    = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'", (today,))['c']
    today_dinner   = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", (today,))['c']
    pending_amount = queryOne(conn, "SELECT SUM(amount) as t FROM meal_orders WHERE payment_status IN ('pending','due')")['t'] or 0
    due_count      = queryOne(conn, "SELECT COUNT(DISTINCT student_id) as c FROM meal_orders WHERE payment_status='due'")['c']
    total_received = queryOne(conn, "SELECT COALESCE(SUM(amount),0) as t FROM payments WHERE status='verified'")['t']

    week_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(7)]
    weekly = []
    for d in week_dates:
        l  = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",  (d,))['c']
        dn = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", (d,))['c']
        weekly.append({'date': d, 'lunch': l, 'dinner': dn, 'total_amount': (l + dn) * 50})

    students_due = query(conn, """
        SELECT s.name, s.roll_number, s.batch, s.bkash_number,
               COUNT(mo.id) as meals, SUM(mo.amount) as due,
               SUM(CASE WHEN mo.payment_status='due' THEN 1 ELSE 0 END) as due_meals
        FROM students s
        LEFT JOIN meal_orders mo ON mo.student_id=s.id AND mo.payment_status IN ('pending','due')
        GROUP BY s.id, s.name, s.roll_number, s.batch, s.bkash_number HAVING SUM(mo.amount) > 0
        ORDER BY due DESC LIMIT 50
    """)

    pending_payments = query(conn, """
        SELECT p.*, s.name as student_name, s.roll_number, s.batch, s.bkash_number as student_bkash
        FROM payments p JOIN students s ON s.id=p.student_id
        WHERE p.status='pending_verification' ORDER BY p.created_at DESC
    """)

    verified_payments = query(conn, """
        SELECT p.*, s.name as student_name, s.roll_number, s.batch
        FROM payments p JOIN students s ON s.id=p.student_id
        WHERE p.status='verified' ORDER BY p.verified_at DESC
    """)

    male_count   = queryOne(conn, "SELECT COUNT(*) as c FROM students WHERE gender='male'")['c']
    female_count = queryOne(conn, "SELECT COUNT(*) as c FROM students WHERE gender='female'")['c']

    locked_students = query(conn, """
        SELECT s.name, s.roll_number, s.batch, s.bkash_number, SUM(mo.amount) as due_total
        FROM students s
        LEFT JOIN meal_orders mo ON mo.student_id=s.id AND mo.payment_status IN ('pending','due')
        WHERE s.is_locked=1
        GROUP BY s.id, s.name, s.roll_number, s.batch, s.bkash_number ORDER BY s.name
    """)

    floor_breakdown = query(conn, """
        SELECT floor, COUNT(*) as total,
               COUNT(*) as males
        FROM students WHERE gender='male' GROUP BY floor ORDER BY floor
    """)

    floor_meals_today = query(conn, """
        SELECT s.floor,
               SUM(CASE WHEN mo.meal_type='lunch'  AND mo.meal_date=%s THEN 1 ELSE 0 END) as lunch,
               SUM(CASE WHEN mo.meal_type='dinner' AND mo.meal_date=%s THEN 1 ELSE 0 END) as dinner
        FROM students s
        LEFT JOIN meal_orders mo ON mo.student_id=s.id
        WHERE s.gender='male'
        GROUP BY s.floor ORDER BY s.floor
    """, (today, today))

    female_lunch_today  = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE s.gender='female' AND mo.meal_type='lunch'  AND mo.meal_date=%s", (today,))['c']
    female_dinner_today = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE s.gender='female' AND mo.meal_type='dinner' AND mo.meal_date=%s", (today,))['c']

    # Female hostel breakdown by floor number (1=Campus, 2=Sentu House, 3=Chairman House)
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}
    female_hostel_breakdown = query(conn, """
        SELECT s.floor as hostel_num,
               COUNT(DISTINCT s.id) as student_count,
               SUM(CASE WHEN mo.meal_type='lunch'  AND mo.meal_date=%s THEN 1 ELSE 0 END) as lunch_today,
               SUM(CASE WHEN mo.meal_type='dinner' AND mo.meal_date=%s THEN 1 ELSE 0 END) as dinner_today
        FROM students s
        LEFT JOIN meal_orders mo ON mo.student_id=s.id
        WHERE s.gender='female'
        GROUP BY s.floor ORDER BY s.floor
    """, (today, today))
    female_hostels = []
    female_hostels_by_num = {1: {'student_count': 0, 'lunch_today': 0, 'dinner_today': 0},
                              2: {'student_count': 0, 'lunch_today': 0, 'dinner_today': 0},
                              3: {'student_count': 0, 'lunch_today': 0, 'dinner_today': 0}}
    for r in female_hostel_breakdown:
        d = dict(r)
        d['hostel_name'] = HOSTEL_NAMES.get(d['hostel_num'], f"Hostel {d['hostel_num']}")
        female_hostels.append(d)
        key = int(d['hostel_num']) if d['hostel_num'] is not None else None
        if key in female_hostels_by_num:
            female_hostels_by_num[key] = {
                'student_count': int(d['student_count'] or 0),
                'lunch_today':   int(d['lunch_today']   or 0),
                'dinner_today':  int(d['dinner_today']  or 0),
            }

    mgr_row      = queryOne(conn, "SELECT bkash_number, manager_id FROM meal_managers WHERE id=%s", (session['user_id'],))
    mgr_bkash_val = mgr_row['bkash_number'] if mgr_row else session.get('mgr_bkash', '')
    mgr_id_val    = mgr_row['manager_id']   if mgr_row else ''

    mgr_student  = queryOne(conn,
        "SELECT s.roll_number FROM meal_managers m LEFT JOIN students s ON s.id=m.student_id WHERE m.id=%s",
        (session['user_id'],)
    )
    mgr_roll_val = mgr_student['roll_number'] if mgr_student and mgr_student['roll_number'] else ''

    conn.close()
    return render_template('manager_dashboard.html',
        total_students    = total_students,
        today_lunch       = today_lunch,
        today_dinner      = today_dinner,
        pending_amount    = pending_amount,
        due_count         = due_count,
        total_received    = total_received,
        weekly            = weekly,
        students_due      = students_due,
        mgr_bkash         = mgr_bkash_val,
        mgr_id            = mgr_id_val,
        mgr_roll          = mgr_roll_val,
        pending_payments  = pending_payments,
        verified_payments = verified_payments,
        male_count        = male_count,
        female_count      = female_count,
        locked_students   = locked_students,
        floor_breakdown   = floor_breakdown,
        floor_meals_today = floor_meals_today,
        female_lunch_today      = female_lunch_today,
        female_dinner_today     = female_dinner_today,
        female_hostels          = female_hostels,
        female_hostels_by_num   = female_hostels_by_num,
    )

# ── MANAGER PAYMENT ACTIONS ───────────────────────────────────────────────────

@app.route('/manager/verify_payment', methods=['POST'])
@login_required('manager')
def verify_payment():
    d    = request.json
    conn = get_db()
    try:
        execute(conn,
            "UPDATE payments SET status='verified', verified_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), verified_by=%s WHERE id=%s",
            (session['name'], d['payment_id'])
        )
        student = queryOne(conn, "SELECT id FROM students WHERE roll_number=%s", (d['roll'],))
        if student:
            execute(conn,
                "UPDATE meal_orders SET payment_status='paid' WHERE student_id=%s AND payment_status IN ('pending','due')",
                (student['id'],)
            )
            remaining = queryOne(conn,
                "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')",
                (student['id'],)
            )['c']
            if remaining == 0:
                execute(conn, "UPDATE students SET is_locked=0 WHERE id=%s", (student['id'],))
        conn.commit()
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/manager/reject_payment', methods=['POST'])
@login_required('manager')
def reject_payment():
    d    = request.json
    conn = get_db()
    try:
        execute(conn,
            "UPDATE payments SET status='rejected', verified_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), verified_by=%s WHERE id=%s",
            (session['name'], d['payment_id'])
        )
        conn.commit()
        return jsonify({'ok': True})
    finally:
        conn.close()


@app.route('/manager/mark_paid', methods=['POST'])
@login_required('manager')
def mark_paid():
    d       = request.json
    conn    = get_db()
    student = queryOne(conn, "SELECT id FROM students WHERE roll_number=%s", (d['roll'],))
    if student:
        amount = queryOne(conn,
            "SELECT SUM(amount) as t FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')",
            (student['id'],)
        )['t'] or 0
        if amount > 0:
            execute(conn,
                "INSERT INTO payments (student_id,amount,bkash_txn,payment_date,status,screenshot_note,"
                "verified_at,verified_by) VALUES (%s,%s,'MANUAL-MARK',%s,'verified','Manually marked paid by manager',"
                "to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),%s)",
                (student['id'], amount, date.today().isoformat(), session['name'])
            )
        execute(conn,
            "UPDATE meal_orders SET payment_status='paid' WHERE student_id=%s AND payment_status IN ('pending','due')",
            (student['id'],)
        )
        execute(conn, "UPDATE students SET is_locked=0 WHERE id=%s", (student['id'],))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/manager/collect_due', methods=['POST'])
@login_required('manager')
def collect_due():
    d       = request.json
    conn    = get_db()
    student = queryOne(conn, "SELECT id FROM students WHERE roll_number=%s", (d['roll'],))
    if student:
        amount = queryOne(conn,
            "SELECT SUM(amount) as t FROM meal_orders WHERE student_id=%s AND payment_status='due'",
            (student['id'],)
        )['t'] or 0
        if amount > 0:
            execute(conn,
                "INSERT INTO payments (student_id,amount,bkash_txn,payment_date,status,screenshot_note,"
                "verified_at,verified_by) VALUES (%s,%s,'CASH-DUE-COLLECTED',%s,'verified',"
                "'Due amount collected by manager in person',"
                "to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),%s)",
                (student['id'], amount, date.today().isoformat(), session['name'])
            )
        execute(conn,
            "UPDATE meal_orders SET payment_status='paid' WHERE student_id=%s AND payment_status='due'",
            (student['id'],)
        )
        remaining = queryOne(conn,
            "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')",
            (student['id'],)
        )['c']
        if remaining == 0:
            execute(conn, "UPDATE students SET is_locked=0 WHERE id=%s", (student['id'],))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── MANAGER CASH REQUESTS ─────────────────────────────────────────────────────

@app.route('/manager/cash_requests')
@login_required('manager')
def manager_cash_requests():
    conn = get_db()
    rows = query(conn, """
        SELECT cr.*, s.name as student_name, s.roll_number, s.batch, s.bkash_number
        FROM cash_payment_requests cr JOIN students s ON s.id=cr.student_id
        WHERE cr.status='pending' ORDER BY cr.requested_at DESC
    """)
    conn.close()
    return jsonify({'requests': [dict(r) for r in rows]})


@app.route('/manager/accept_cash', methods=['POST'])
@login_required('manager')
def accept_cash():
    d      = request.json
    req_id = d['request_id']
    conn   = get_db()
    req    = queryOne(conn, "SELECT * FROM cash_payment_requests WHERE id=%s", (req_id,))
    if not req:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request not found.'})
    execute(conn,
        "UPDATE cash_payment_requests SET status='accepted', reviewed_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), reviewed_by=%s WHERE id=%s",
        (session['name'], req_id)
    )
    execute(conn,
        "INSERT INTO payments (student_id,amount,bkash_txn,payment_date,status,screenshot_note,"
        "verified_at,verified_by) VALUES (%s,%s,'CASH-PAYMENT',%s,'verified',%s,"
        "to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),%s)",
        (req['student_id'], req['amount'], date.today().isoformat(),
         f"Cash payment. Note: {req['note'] or 'N/A'}", session['name'])
    )
    execute(conn,
        "UPDATE meal_orders SET payment_status='paid' WHERE student_id=%s AND payment_status IN ('pending','due')",
        (req['student_id'],)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/manager/decline_cash', methods=['POST'])
@login_required('manager')
def decline_cash():
    d      = request.json
    req_id = d['request_id']
    conn   = get_db()
    execute(conn,
        "UPDATE cash_payment_requests SET status='declined', reviewed_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), reviewed_by=%s WHERE id=%s",
        (session['name'], req_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── MANAGER STUDENTS ──────────────────────────────────────────────────────────

@app.route('/manager/students')
@login_required('manager')
def manager_students():
    conn  = get_db()
    today = date.today().isoformat()
    students = query(conn, """
        SELECT s.*,
               COUNT(mo.id) as total_meals,
               SUM(CASE WHEN mo.payment_status='pending' THEN mo.amount ELSE 0 END) as pending_amount,
               SUM(CASE WHEN mo.payment_status='due'     THEN mo.amount ELSE 0 END) as due_amount
        FROM students s
        LEFT JOIN meal_orders mo ON mo.student_id=s.id
        GROUP BY s.id
        ORDER BY s.floor,
                 s.batch,
                 CAST(SPLIT_PART(s.roll_number, '-', 2) AS INTEGER)
    """)

    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}

    # Male floor totals (numeric floors 1-7)
    floor_totals = query(conn, """
        SELECT s.floor, COUNT(DISTINCT s.id) as student_count,
               SUM(CASE WHEN s.gender='male'   THEN 1 ELSE 0 END) as males,
               SUM(CASE WHEN s.gender='female' THEN 1 ELSE 0 END) as females,
               SUM(CASE WHEN mo.meal_type='lunch'  AND mo.meal_date=%s THEN 1 ELSE 0 END) as today_lunch,
               SUM(CASE WHEN mo.meal_type='dinner' AND mo.meal_date=%s THEN 1 ELSE 0 END) as today_dinner
        FROM students s LEFT JOIN meal_orders mo ON mo.student_id=s.id
        WHERE s.gender='male'
        GROUP BY s.floor ORDER BY s.floor
    """, (today, today))

    # Female hostel totals (floor 1=Campus, 2=Sentu House, 3=Chairman House)
    female_hostel_totals_raw = query(conn, """
        SELECT s.floor as hostel_num, COUNT(DISTINCT s.id) as student_count,
               SUM(CASE WHEN mo.meal_type='lunch'  AND mo.meal_date=%s THEN 1 ELSE 0 END) as today_lunch,
               SUM(CASE WHEN mo.meal_type='dinner' AND mo.meal_date=%s THEN 1 ELSE 0 END) as today_dinner
        FROM students s LEFT JOIN meal_orders mo ON mo.student_id=s.id
        WHERE s.gender='female'
        GROUP BY s.floor ORDER BY s.floor
    """, (today, today))
    female_hostel_totals = []
    for r in female_hostel_totals_raw:
        d = dict(r)
        d['hostel_name'] = HOSTEL_NAMES.get(d['hostel_num'], f"Hostel {d['hostel_num']}")
        female_hostel_totals.append(d)

    conn.close()
    return render_template('manager_students.html',
        students=students,
        floor_totals=floor_totals,
        female_hostel_totals=female_hostel_totals,
        hostel_names=HOSTEL_NAMES,
    )


@app.route('/manager/unlock_student', methods=['POST'])
@login_required('manager')
def unlock_student():
    d       = request.json
    conn    = get_db()
    student = queryOne(conn, "SELECT id FROM students WHERE roll_number=%s", (d['roll'],))
    if student:
        execute(conn, "UPDATE students SET is_locked=0 WHERE id=%s", (student['id'],))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    conn.close()
    return jsonify({'ok': False, 'msg': 'Student not found.'})


@app.route('/manager/floor_students')
@login_required('manager')
def floor_students():
    gender = request.args.get('gender')
    floor  = request.args.get('floor')
    sub    = request.args.get('sub', 'all')
    today  = str(date.today())
    conn   = get_db()

    # Female hostel mapping: integer floor -> name
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}

    if sub == 'all':
        if gender == 'male' and floor:
            rows = query(conn,
                "SELECT name, roll_number, batch, bkash_number, floor FROM students WHERE gender='male' AND floor=%s ORDER BY name",
                (floor,)
            )
        elif gender == 'female' and floor:
            # floor param is the hostel number (1=Campus, 2=Sentu House, 3=Chairman House)
            rows = query(conn,
                "SELECT name, roll_number, batch, bkash_number, floor FROM students WHERE gender='female' AND floor=%s ORDER BY name",
                (floor,)
            )
        elif gender == 'female':
            # All female students across all hostels
            rows = query(conn,
                "SELECT name, roll_number, batch, bkash_number, floor FROM students WHERE gender='female' ORDER BY name"
            )
        else:
            rows = []
    else:
        meal_type = sub
        if gender == 'male' and floor:
            rows = query(conn,
                "SELECT s.name, s.roll_number, s.batch, s.bkash_number, s.floor "
                "FROM students s JOIN meal_orders mo ON mo.student_id=s.id "
                "WHERE s.gender='male' AND s.floor=%s AND mo.meal_type=%s AND mo.meal_date=%s ORDER BY s.name",
                (floor, meal_type, today)
            )
        elif gender == 'female' and floor:
            rows = query(conn,
                "SELECT s.name, s.roll_number, s.batch, s.bkash_number, s.floor "
                "FROM students s JOIN meal_orders mo ON mo.student_id=s.id "
                "WHERE s.gender='female' AND s.floor=%s AND mo.meal_type=%s AND mo.meal_date=%s ORDER BY s.name",
                (floor, meal_type, today)
            )
        elif gender == 'female':
            rows = query(conn,
                "SELECT s.name, s.roll_number, s.batch, s.bkash_number, s.floor "
                "FROM students s JOIN meal_orders mo ON mo.student_id=s.id "
                "WHERE s.gender='female' AND mo.meal_type=%s AND mo.meal_date=%s ORDER BY s.name",
                (meal_type, today)
            )
        else:
            rows = []

    conn.close()
    # Attach hostel name for female students
    result = []
    for r in rows:
        d = dict(r)
        if gender == 'female' and d.get('floor'):
            d['hostel_name'] = HOSTEL_NAMES.get(d['floor'], f"Hostel {d['floor']}")
        result.append(d)
    return jsonify({'students': result})


@app.route('/manager/clear_weekly', methods=['POST'])
@login_required('manager')
def clear_weekly():
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    conn = get_db()
    execute(conn, "DELETE FROM meal_orders WHERE meal_date<%s", (week_ago,))
    execute(conn, "DELETE FROM payments WHERE payment_date<%s AND status='verified'", (week_ago,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Weekly data cleared.'})


import secrets, string

def generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ── MANAGER TRANSFER ───────────────────────────────────────────────────────────

@app.route('/manager/search_student', methods=['POST'])
@login_required('manager')
def manager_search_student():
    d    = request.json
    conn = get_db()
    q    = d.get('query','').strip()
    rows = query(conn,
        "SELECT id, name, roll_number, batch, floor, gender, bkash_number FROM students "
        "WHERE roll_number LIKE %s OR name LIKE %s LIMIT 10",
        (f'%{q}%', f'%{q}%')
    )
    conn.close()
    return jsonify({'students': [dict(r) for r in rows]})


@app.route('/manager/send_transfer', methods=['POST'])
@login_required('manager')
def manager_send_transfer():
    d          = request.json
    student_id = d.get('student_id')
    conn       = get_db()
    existing = queryOne(conn,
        "SELECT id FROM manager_transfer_invites WHERE to_student_id=%s AND status='pending'", (student_id,)
    )
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': 'This student already has a pending transfer invite.'})
    student = queryOne(conn, "SELECT * FROM students WHERE id=%s", (student_id,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Student not found.'})
    temp_pass  = generate_temp_password()
    batch_num  = queryOne(conn, "SELECT COUNT(*) as c FROM meal_managers")['c'] + 1
    new_mgr_id = f"MGR{batch_num:03d}"
    while queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s", (new_mgr_id,)):
        batch_num += 1
        new_mgr_id = f"MGR{batch_num:03d}"
    execute(conn,
        "INSERT INTO manager_transfer_invites (from_manager_id, to_student_id, status, temp_password, new_manager_id) VALUES (%s,%s,%s,%s,%s)",
        (session['name'], student_id, 'pending', temp_pass, new_mgr_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Transfer invite sent to {student["name"]}.'})


@app.route('/manager/transfer_history')
@login_required('manager')
def manager_transfer_history():
    conn = get_db()
    rows = query(conn, "SELECT * FROM manager_history ORDER BY tenure_start DESC")
    conn.close()
    return jsonify({'history': [dict(r) for r in rows]})


@app.route('/manager/active_managers')
@login_required('manager')
def active_managers():
    conn = get_db()
    rows = query(conn, "SELECT manager_id, name, bkash_number, created_at FROM meal_managers WHERE is_active=1")
    conn.close()
    return jsonify({'managers': [dict(r) for r in rows]})

# ── STUDENT TRANSFER INVITES ──────────────────────────────────────────────────

@app.route('/student/transfer_invites')
@login_required('student')
def student_transfer_invites():
    sid  = session['user_id']
    conn = get_db()
    rows = query(conn,
        "SELECT * FROM manager_transfer_invites WHERE to_student_id=%s AND status='pending' ORDER BY created_at DESC",
        (sid,)
    )
    conn.close()
    return jsonify({'invites': [dict(r) for r in rows]})


@app.route('/student/respond_transfer', methods=['POST'])
@login_required('student')
def student_respond_transfer():
    d         = request.json
    invite_id = d.get('invite_id')
    action    = d.get('action')
    sid       = session['user_id']
    conn      = get_db()
    invite = queryOne(conn,
        "SELECT * FROM manager_transfer_invites WHERE id=%s AND to_student_id=%s AND status='pending'",
        (invite_id, sid)
    )
    if not invite:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invite not found or already responded.'})
    if action == 'decline':
        execute(conn,
            "UPDATE manager_transfer_invites SET status='declined', responded_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
            (invite_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'msg': 'You declined the manager transfer.'})
    student   = queryOne(conn, "SELECT * FROM students WHERE id=%s", (sid,))
    temp_pass = invite['temp_password']
    new_mgr_id = invite['new_manager_id']
    expires   = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    old_mgrs  = query(conn, "SELECT * FROM meal_managers WHERE is_active=1")
    for mgr in old_mgrs:
        mgr_roll = None
        if mgr['student_id']:
            s_row = queryOne(conn, "SELECT roll_number FROM students WHERE id=%s", (mgr['student_id'],))
            if s_row:
                mgr_roll = s_row['roll_number']
        execute(conn,
            "INSERT INTO manager_history (manager_id, student_name, roll_number, batch, floor, assigned_by, tenure_start, tenure_end) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))",
            (mgr['manager_id'], mgr['name'], mgr_roll, None, None, invite['from_manager_id'], mgr['created_at'])
        )
        execute(conn, "UPDATE meal_managers SET is_active=0 WHERE id=%s", (mgr['id'],))
    execute(conn,
        "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, student_id, temp_password_expires, must_change_password) "
        "VALUES (%s,%s,%s,%s,1,%s,%s,1)",
        (new_mgr_id, student['name'], hash_pass(temp_pass), student['bkash_number'], sid, expires)
    )
    execute(conn,
        "UPDATE manager_transfer_invites SET status='accepted', responded_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
        (invite_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({
        'ok': True, 'manager_id': new_mgr_id, 'temp_password': temp_pass,
        'expires': expires, 'msg': f'You are now the meal manager! Login with ID: {new_mgr_id}'
    })

# ── MANAGER CHANGE PASSWORD ───────────────────────────────────────────────────

@app.route('/manager/change_password', methods=['GET', 'POST'])
@login_required('manager')
def manager_change_password():
    if request.method == 'POST':
        d        = request.form
        new_pass = d.get('new_password','').strip()
        confirm  = d.get('confirm_password','').strip()
        if len(new_pass) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('manager_change_password.html')
        if new_pass != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('manager_change_password.html')
        conn = get_db()
        execute(conn,
            "UPDATE meal_managers SET password=%s, must_change_password=0, temp_password_expires=NULL WHERE id=%s",
            (hash_pass(new_pass), session['user_id'])
        )
        conn.commit()
        conn.close()
        flash('Password changed successfully!', 'success')
        return redirect(url_for('manager_dashboard'))
    return render_template('manager_change_password.html')

# ── LOCK / ORDERING STATUS ────────────────────────────────────────────────────

@app.route('/api/meal_lock_status')
@login_required('student')
def meal_lock_status():
    sid       = session['user_id']
    month_ago = (date.today() - timedelta(days=30)).isoformat()
    conn      = get_db()
    overdue   = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due') AND meal_date<%s",
        (sid, month_ago)
    )['c']
    conn.close()
    return jsonify({'locked': overdue > 0, 'overdue_count': overdue})


@app.route('/api/ordering_lock_status')
@login_required('student')
def ordering_lock_status():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn, "SELECT ordering_locked FROM students WHERE id=%s", (sid,))
    conn.close()
    return jsonify({'ordering_locked': bool(row and row['ordering_locked'])})


@app.route('/api/current_bkash')
@login_required('student')
def current_bkash():
    mgr = get_current_weekly_bkash()
    return jsonify({'bkash_number': mgr['bkash_number'], 'week_start': mgr['week_start'], 'approved': mgr['approved']})


@app.route('/api/today_summary')
@login_required('manager')
def today_summary():
    today = date.today().isoformat()
    conn  = get_db()
    lunch  = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",  (today,))['c']
    dinner = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", (today,))['c']
    conn.close()
    return jsonify({'lunch': lunch, 'dinner': dinner, 'total': (lunch + dinner) * 50})

# ── ADMIN ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            accept     = request.headers.get('Accept', '')
            fetch_mode = request.headers.get('Sec-Fetch-Mode', '')
            # Only treat as AJAX/API call when there is clear programmatic intent.
            # Do NOT use Sec-Fetch-Dest=='empty' alone — that fires on browser
            # navigations in some Chromium versions and swallows the redirect.
            is_ajax = (
                request.is_json or
                request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
                (fetch_mode == 'cors' and 'application/json' in accept) or
                ('application/json' in accept and 'text/html' not in accept)
            )
            if is_ajax:
                return jsonify({'ok': False, 'error': 'not_authenticated', 'msg': 'Admin session expired.'}), 401
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per hour")
def admin_login():
    # If already logged in, go straight to dashboard (don't redirect-loop)
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        admin_id    = request.form.get('admin_id', '').strip()
        password    = request.form.get('password', '').strip()
        remember_me = request.form.get('remember_me') in ('1', 'on')

        print(f"\n[ADMIN LOGIN] Attempting login for admin_id='{admin_id}'")

        conn = get_db()
        try:
            row = queryOne(conn, "SELECT * FROM admin_accounts WHERE admin_id=%s", (admin_id,))
        except Exception as e:
            conn.close()
            print(f"[ADMIN LOGIN] ❌ DB ERROR: {e}")
            flash(f'Database error: {e}', 'error')
            return render_template('admin_login.html')
        conn.close()

        print(f"[ADMIN LOGIN] Row found: {row is not None}")
        if row:
            pw_hash = row['password']
            print(f"[ADMIN LOGIN] Hash in DB: {pw_hash[:40]}...")
            pw_ok = verify_pass(pw_hash, password)
            print(f"[ADMIN LOGIN] Password match: {pw_ok}")
            if not pw_ok:
                # Hash is corrupted/wrong version — check against env var directly
                import os as _os
                env_pass = _os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')
                if password == env_pass:
                    # Password is correct but hash is broken — rewrite it
                    print(f"[ADMIN LOGIN] Hash mismatch but password matches env — force-rewriting hash")
                    fix_conn = get_db()
                    execute(fix_conn, "UPDATE admin_accounts SET password=%s WHERE admin_id=%s",
                            (hash_pass(password), admin_id))
                    fix_conn.commit()
                    fix_conn.close()
                    pw_ok = True  # allow login
                    row = dict(row)
                    row['password'] = hash_pass(password)
        else:
            print(f"[ADMIN LOGIN] ❌ No account found with admin_id='{admin_id}'")
            conn2 = get_db()
            all_admins = query(conn2, "SELECT admin_id, created_at FROM admin_accounts")
            conn2.close()
            print(f"[ADMIN LOGIN] All admins in DB: {[dict(r) for r in all_admins]}")
            # Auto-create if totally missing
            if admin_id == 'DEVADMIN':
                import os as _os
                env_pass = _os.environ.get('ADMIN_PASSWORD', 'nmms@dev2024!')
                if password == env_pass:
                    print(f"[ADMIN LOGIN] Auto-creating missing DEVADMIN account")
                    fix_conn = get_db()
                    execute(fix_conn,
                        "INSERT INTO admin_accounts (admin_id, password) VALUES (%s,%s) ON CONFLICT (admin_id) DO UPDATE SET password=EXCLUDED.password",
                        (admin_id, hash_pass(password)))
                    fix_conn.commit()
                    fix_conn.close()
                    row = {'admin_id': admin_id, 'password': hash_pass(password)}
                    pw_ok = True

        if row and (verify_pass(row['password'], password) or pw_ok):
            if not (row['password'].startswith('pbkdf2:') or row['password'].startswith('scrypt:')):
                conn2 = get_db()
                execute(conn2, "UPDATE admin_accounts SET password=%s WHERE admin_id=%s", (hash_pass(password), admin_id))
                conn2.commit()
                conn2.close()
            session.clear()
            session.permanent    = remember_me
            session['role']      = 'admin'
            session['admin_id']  = admin_id
            session['remember']  = remember_me
            print(f"[ADMIN LOGIN] ✅ Login SUCCESS for {admin_id}")
            response = redirect(url_for('admin_dashboard'))
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            response.headers['Pragma']        = 'no-cache'
            return response

        print(f"[ADMIN LOGIN] ❌ Login FAILED for {admin_id}")
        flash('Invalid admin credentials.', 'error')
    response = make_response(render_template('admin_login.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma']        = 'no-cache'
    return response


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    response = redirect(url_for('index'))
    # Force browser to not cache any admin page after logout
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma']        = 'no-cache'
    return response


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db()
    stats = {
        'students':       queryOne(conn, "SELECT COUNT(*) as c FROM students")['c'],
        'managers':       queryOne(conn, "SELECT COUNT(*) as c FROM meal_managers WHERE is_active=1")['c'],
        'total_orders':   queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders")['c'],
        'total_payments': queryOne(conn, "SELECT COUNT(*) as c FROM payments")['c'],
        'pending_amount': queryOne(conn, "SELECT SUM(amount) as t FROM meal_orders WHERE payment_status IN ('pending','due')")['t'] or 0,
    }
    reset_log = query(conn, "SELECT * FROM admin_reset_log ORDER BY reset_at DESC LIMIT 20")
    conn.close()
    response = make_response(render_template('admin_dashboard.html', stats=stats, reset_log=reset_log))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    response.headers['Pragma']        = 'no-cache'
    return response


@app.route('/admin/students')
@admin_required
def admin_students():
    conn     = get_db()
    students = query(conn, """
        SELECT s.*,
               COUNT(mo.id) as total_meals,
               COALESCE(SUM(CASE WHEN mo.payment_status='pending' THEN mo.amount ELSE 0 END),0) as pending_amount,
               COALESCE(SUM(CASE WHEN mo.payment_status='due'     THEN mo.amount ELSE 0 END),0) as due_amount
        FROM students s LEFT JOIN meal_orders mo ON mo.student_id=s.id
        GROUP BY s.id
        ORDER BY s.floor,
                 s.batch,
                 CAST(SPLIT_PART(s.roll_number, '-', 2) AS INTEGER)
    """)
    conn.close()
    return render_template('admin_students.html', students=students)


@app.route('/admin/delete_student', methods=['POST'])
@admin_required
def admin_delete_student():
    """Permanently delete a single student and all their associated data."""
    d          = request.json or {}
    student_id = d.get('student_id')
    if not student_id:
        return jsonify({'ok': False, 'msg': 'student_id required'})

    conn = get_db()
    # Check student exists
    student = queryOne(conn, "SELECT id, name, roll_number, is_demo FROM students WHERE id=%s", (student_id,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Student not found.'})

    name = student['name']
    roll = student['roll_number']

    # Delete all related data first (FK order)
    execute(conn, "DELETE FROM meal_orders            WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM payments               WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM cash_payment_requests  WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM phone_change_requests  WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM meal_edit_requests     WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM floor_change_requests  WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM duty_invites           WHERE student_id=%s", (student_id,))
    execute(conn, "DELETE FROM manager_transfer_invites WHERE to_student_id=%s", (student_id,))
    # Finally delete the student
    execute(conn, "DELETE FROM students WHERE id=%s", (student_id,))

    # Log it
    admin_id = session.get('admin_id', 'admin')
    execute(conn,
        "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
        (admin_id, f"delete_student: id={student_id} name={name} roll={roll}")
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Student "{name}" ({roll}) and all their data have been permanently deleted.'})


@app.route('/admin/reset', methods=['POST'])
@admin_required
def admin_reset():
    action = request.json.get('action')
    conn   = get_db()
    msg    = ''
    if action == 'reset_meals':
        execute(conn, "DELETE FROM meal_orders")
        execute(conn, "DELETE FROM payments")
        execute(conn, "DELETE FROM cash_payment_requests")
        execute(conn, "UPDATE students SET is_locked=0")
        msg = 'All meal orders, payments, and cash requests cleared.'
    elif action == 'reset_students':
        execute(conn, "DELETE FROM meal_orders")
        execute(conn, "DELETE FROM payments")
        execute(conn, "DELETE FROM cash_payment_requests")
        execute(conn, "DELETE FROM students")
        msg = 'All students and their meal data deleted.'
    elif action == 'reset_all':
        for tbl in ['meal_orders','payments','cash_payment_requests','students',
                    'manager_transfer_invites','manager_history','meal_managers']:
            execute(conn, f"DELETE FROM {tbl}")
        mgr_pass  = os.environ.get('MANAGER_PASSWORD', 'manager123')
        mgr_bkash = os.environ.get('MANAGER_BKASH',    '01712345678')
        execute(conn,
            "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, must_change_password) VALUES (%s,%s,%s,%s,1,0)",
            ('MGR001', 'Meal Manager', hash_pass(mgr_pass), mgr_bkash)
        )
        msg = f'Full system reset done. MGR001 restored.'
    elif action == 'reset_weekly':
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        execute(conn, "DELETE FROM meal_orders WHERE meal_date<%s", (week_ago,))
        execute(conn, "DELETE FROM payments WHERE payment_date<%s AND status='verified'", (week_ago,))
        msg = 'Meal data older than 7 days cleared.'
    elif action == 'reset_manager_password':
        new_pass = request.json.get('new_password', 'manager123').strip()
        if len(new_pass) < 6:
            conn.close()
            return jsonify({'ok': False, 'msg': 'Password must be at least 6 characters.'})
        existing = queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id='MGR001'")
        if existing:
            execute(conn,
                "UPDATE meal_managers SET password=%s, must_change_password=0, temp_password_expires=NULL, is_active=1 WHERE manager_id='MGR001'",
                (hash_pass(new_pass),)
            )
        else:
            mgr_bkash = os.environ.get('MANAGER_BKASH', '01712345678')
            execute(conn,
                "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, must_change_password) VALUES ('MGR001','Meal Manager',%s,%s,1,0)",
                (hash_pass(new_pass), mgr_bkash)
            )
        msg = f'MGR001 password reset to "{new_pass}".'
    elif action == 'reset_manager_bkash':
        new_bkash = request.json.get('new_bkash', '').strip()
        if not new_bkash:
            conn.close()
            return jsonify({'ok': False, 'msg': 'bKash number cannot be empty.'})
        execute(conn, "UPDATE meal_managers SET bkash_number=%s WHERE manager_id='MGR001'", (new_bkash,))
        msg = f'MGR001 bKash number updated to "{new_bkash}".'
    elif action == 'reset_all_managers':
        execute(conn, "DELETE FROM manager_transfer_invites")
        execute(conn, "DELETE FROM manager_history")
        execute(conn, "DELETE FROM meal_managers")
        mgr_pass  = os.environ.get('MANAGER_PASSWORD', 'manager123')
        mgr_bkash = os.environ.get('MANAGER_BKASH',    '01712345678')
        execute(conn,
            "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, must_change_password) VALUES (%s,%s,%s,%s,1,0)",
            ('MGR001', 'Meal Manager', hash_pass(mgr_pass), mgr_bkash)
        )
        msg = 'All managers wiped. MGR001 recreated.'
    elif action == 'deactivate_extra_managers':
        execute(conn, "UPDATE meal_managers SET is_active=0 WHERE manager_id != 'MGR001'")
        msg = 'All managers except MGR001 deactivated.'
    else:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Unknown action.'})
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f'{action}: {msg}'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': msg})


@app.route('/admin/lookup_student')
@admin_required
def admin_lookup_student():
    roll = request.args.get('roll', '').strip()
    if not roll:
        return jsonify({'ok': False, 'msg': 'Roll number required.'})
    conn    = get_db()
    student = queryOne(conn,
        "SELECT id, name, roll_number, batch, gender, bkash_number, is_locked FROM students WHERE roll_number=%s", (roll,)
    )
    conn.close()
    if student:
        return jsonify({'ok': True, 'student': dict(student)})
    return jsonify({'ok': False, 'msg': f'No student found with roll number "{roll}".'})


@app.route('/admin/reset_student_password', methods=['POST'])
@admin_required
def admin_reset_student_password():
    data     = request.json
    roll     = data.get('roll_number', '').strip()
    new_pass = data.get('new_password', '').strip()
    if not roll or not new_pass or len(new_pass) < 6:
        return jsonify({'ok': False, 'msg': 'Roll number and password (min 6 chars) required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Student "{roll}" not found.'})
    execute(conn, "UPDATE students SET password=%s WHERE id=%s", (hash_pass(new_pass), student['id']))
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"reset_student_password: {student['name']} ({roll})"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Password for {student["name"]} ({roll}) reset.'})


@app.route('/admin/unlock_student', methods=['POST'])
@admin_required
def admin_unlock_student():
    data    = request.json
    roll    = data.get('roll_number', '').strip()
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Student "{roll}" not found.'})
    execute(conn, "UPDATE students SET is_locked=0 WHERE id=%s", (student['id'],))
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"admin_unlock_student: {student['name']} ({roll})"))
    conn.commit()
    conn.close()
@app.route('/admin/fix_female_hostel', methods=['POST'])
@admin_required
def admin_fix_female_hostel():
    """Fix a female student's hostel assignment (floor value 1=Campus, 2=Sentu House, 3=Chairman House)."""
    data      = request.json or {}
    roll      = (data.get('roll_number') or '').strip()
    new_floor = data.get('floor')
    if not roll or new_floor is None:
        return jsonify({'ok': False, 'msg': 'roll_number and floor (1/2/3) required.'})
    try:
        new_floor = int(new_floor)
        if new_floor not in (1, 2, 3):
            return jsonify({'ok': False, 'msg': 'floor must be 1 (Campus), 2 (Sentu House), or 3 (Chairman House).'})
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'floor must be an integer 1, 2, or 3.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name, gender FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Student "{roll}" not found.'})
    if student['gender'] != 'female':
        conn.close()
        return jsonify({'ok': False, 'msg': 'This route is only for female students.'})
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}
    execute(conn, "UPDATE students SET floor=%s WHERE id=%s", (new_floor, student['id']))
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"fix_female_hostel: {student['name']} ({roll}) -> {HOSTEL_NAMES[new_floor]}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'{student["name"]} moved to {HOSTEL_NAMES[new_floor]}.'})


@app.route('/admin/list_female_students')
@admin_required
def admin_list_female_students():
    """List all female students with their current hostel for verification."""
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}
    conn = get_db()
    rows = query(conn, "SELECT id, name, roll_number, batch, floor FROM students WHERE gender='female' ORDER BY batch, roll_number")
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['hostel_name'] = HOSTEL_NAMES.get(d['floor'], f'Unknown (floor={d["floor"]})')
        result.append(d)
    return jsonify({'ok': True, 'students': result})

# ── FLOOR CHANGE REQUESTS ─────────────────────────────────────────────────────

@app.route('/student/request_floor_change', methods=['POST'])
@login_required('student')
def student_request_floor_change():
    d               = request.json or {}
    sid             = session['user_id']
    requested_floor = d.get('requested_floor')
    reason          = (d.get('reason') or '').strip()[:200]
    if not requested_floor:
        return jsonify({'ok': False, 'msg': 'Please select a floor.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT name, floor, gender FROM students WHERE id=%s", (sid,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Student not found.'})
    is_female = student['gender'] == 'female'
    try:
        requested_floor = int(requested_floor)
        if is_female:
            if requested_floor not in (1, 2, 3):
                conn.close()
                return jsonify({'ok': False, 'msg': 'Hostel must be 1 (Campus), 2 (Sentu House), or 3 (Chairman House).'})
        else:
            if requested_floor < 1 or requested_floor > 7:
                conn.close()
                return jsonify({'ok': False, 'msg': 'Floor must be between 1 and 7.'})
    except (TypeError, ValueError):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invalid value.'})
    if student['floor'] == requested_floor:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You are already on that floor!'})
    dup = queryOne(conn, "SELECT id FROM floor_change_requests WHERE student_id=%s AND status='pending'", (sid,))
    if dup:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending floor change request.'})
    execute(conn,
        "INSERT INTO floor_change_requests (student_id, current_floor, requested_floor, reason) VALUES (%s,%s,%s,%s)",
        (sid, student['floor'], requested_floor, reason)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Floor change request submitted!'})


@app.route('/student/floor_change_status')
@login_required('student')
def student_floor_change_status():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn,
        "SELECT * FROM floor_change_requests WHERE student_id=%s ORDER BY created_at DESC LIMIT 1", (sid,)
    )
    student = queryOne(conn, "SELECT floor, gender FROM students WHERE id=%s", (sid,))
    conn.close()
    return jsonify({
        'current_floor': student['floor'] if student else None,
        'gender':        student['gender'] if student else None,
        'request':       dict(row) if row else None,
    })


@app.route('/admin/floor_change_requests')
@admin_required
def admin_floor_change_requests():
    conn = get_db()
    rows = query(conn, """
        SELECT fcr.*, s.name as student_name, s.roll_number, s.batch, s.floor as current_floor_db
        FROM floor_change_requests fcr JOIN students s ON s.id=fcr.student_id
        WHERE fcr.status='pending' ORDER BY fcr.created_at ASC
    """)
    conn.close()
    return jsonify({'requests': [dict(r) for r in rows]})


@app.route('/admin/update_student_floor', methods=['POST'])
@admin_required
def admin_update_student_floor():
    data      = request.json or {}
    roll      = (data.get('roll_number') or '').strip()
    new_floor = data.get('floor')
    req_id    = data.get('request_id')
    if not roll or new_floor is None:
        return jsonify({'ok': False, 'msg': 'Roll number and floor are required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name, gender FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Student not found.'})
    try:
        new_floor = int(new_floor)
        if student['gender'] == 'female':
            if new_floor not in (1, 2, 3):
                conn.close()
                return jsonify({'ok': False, 'msg': 'Hostel must be 1, 2, or 3 for female students.'})
        else:
            if new_floor < 1 or new_floor > 7:
                conn.close()
                return jsonify({'ok': False, 'msg': 'Floor must be between 1 and 7.'})
    except (TypeError, ValueError):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invalid value.'})
    execute(conn, "UPDATE students SET floor=%s WHERE id=%s", (new_floor, student['id']))
    if req_id:
        execute(conn,
            "UPDATE floor_change_requests SET status='approved', reviewed_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), reviewed_by=%s WHERE id=%s",
            (session['admin_id'], req_id)
        )
    execute(conn,
        "UPDATE floor_change_requests SET status='approved', reviewed_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), reviewed_by=%s WHERE student_id=%s AND status='pending'",
        (session['admin_id'], student['id'])
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"update_floor: {student['name']} ({roll}) -> floor {new_floor}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f"Floor updated to {new_floor} for {student['name']}."})


@app.route('/admin/reject_floor_change', methods=['POST'])
@admin_required
def admin_reject_floor_change():
    data   = request.json or {}
    req_id = data.get('request_id')
    if not req_id:
        return jsonify({'ok': False, 'msg': 'Request ID required.'})
    conn = get_db()
    execute(conn,
        "UPDATE floor_change_requests SET status='rejected', reviewed_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), reviewed_by=%s WHERE id=%s",
        (session['admin_id'], req_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Floor change request rejected.'})

# ── PHONE CHANGE (ADMIN) ──────────────────────────────────────────────────────

@app.route('/admin/phone_change_requests')
@admin_required
def admin_phone_change_requests():
    conn = get_db()
    rows = query(conn, """
        SELECT pcr.*, s.name, s.roll_number, s.batch, s.floor
        FROM phone_change_requests pcr JOIN students s ON s.id=pcr.student_id
        ORDER BY CASE pcr.status WHEN 'pending' THEN 0 ELSE 1 END, pcr.created_at DESC LIMIT 100
    """)
    conn.close()
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})


@app.route('/admin/decide_phone_change', methods=['POST'])
@admin_required
def admin_decide_phone_change():
    d       = request.json or {}
    req_id  = d.get('request_id')
    verdict = d.get('verdict')
    if verdict not in ('approved', 'rejected'):
        return jsonify({'ok': False, 'msg': 'Invalid verdict.'})
    conn     = get_db()
    req      = queryOne(conn, "SELECT * FROM phone_change_requests WHERE id=%s AND status='pending'", (req_id,))
    if not req:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request not found or already decided.'})
    now      = datetime.now().isoformat(timespec='seconds')
    admin_id = session.get('admin_id', 'admin')
    if verdict == 'approved':
        execute(conn, "UPDATE students SET bkash_number=%s WHERE id=%s", (req['new_bkash'], req['student_id']))
        msg = f"Approved. bKash updated to {req['new_bkash']}."
    else:
        msg = 'Request rejected.'
    execute(conn,
        "UPDATE phone_change_requests SET status=%s, decided_by=%s, decided_at=%s WHERE id=%s",
        (verdict, admin_id, now, req_id)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (admin_id, f"phone_change_{verdict}: student_id={req['student_id']}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': msg})

# ── ADMIN TRANSFER / ADD MANAGER ─────────────────────────────────────────────

@app.route('/admin/list_managers')
@admin_required
def admin_list_managers():
    conn     = get_db()
    managers = query(conn, "SELECT manager_id, name, bkash_number, is_active, created_at FROM meal_managers ORDER BY is_active DESC, created_at DESC")
    conn.close()
    return jsonify({'managers': [dict(m) for m in managers]})


@app.route('/admin/remove_rotation_manager', methods=['POST'])
@admin_required
def admin_remove_rotation_manager():
    data       = request.json or {}
    manager_id = data.get('manager_id', '').strip().upper()
    if not manager_id:
        return jsonify({'ok': False, 'msg': 'No manager_id provided.'}), 400
    if manager_id == 'MGR001':
        return jsonify({'ok': False, 'msg': 'MGR001 cannot be removed via this action.'}), 400
    conn = get_db()
    mgr  = queryOne(conn, "SELECT * FROM meal_managers WHERE manager_id=%s", (manager_id,))
    if not mgr:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Manager {manager_id} not found.'}), 404
    if not mgr['is_active']:
        conn.close()
        return jsonify({'ok': False, 'msg': f'{manager_id} is already inactive.'}), 400
    execute(conn, "UPDATE meal_managers SET is_active=0 WHERE manager_id=%s", (manager_id,))
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"remove_rotation_manager: {manager_id} deactivated"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Manager {manager_id} deactivated.'})


@app.route('/admin/transfer_manager', methods=['POST'])
@admin_required
def admin_transfer_manager():
    data           = request.json
    roll           = data.get('roll_number', '').strip()
    new_manager_id = data.get('new_manager_id', '').strip().upper()
    temp_password  = data.get('temp_password', '').strip()
    if not roll or not new_manager_id or not temp_password or len(temp_password) < 6:
        return jsonify({'ok': False, 'msg': 'Roll number, new manager ID, and temp password (min 6 chars) required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name, bkash_number FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'No student found with roll "{roll}".'})
    if queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s", (new_manager_id,)):
        conn.close()
        return jsonify({'ok': False, 'msg': f'Manager ID "{new_manager_id}" is already taken.'})
    active_mgrs = query(conn, "SELECT * FROM meal_managers WHERE is_active=1")
    for mgr in active_mgrs:
        mgr_roll = None
        if mgr['student_id']:
            s_row = queryOne(conn, "SELECT roll_number FROM students WHERE id=%s", (mgr['student_id'],))
            if s_row:
                mgr_roll = s_row['roll_number']
        execute(conn,
            "INSERT INTO manager_history (manager_id, student_name, roll_number, batch, floor, assigned_by, tenure_start, tenure_end) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))",
            (mgr['manager_id'], mgr['name'], mgr_roll, None, None, session['admin_id'], mgr['created_at'])
        )
        execute(conn, "UPDATE meal_managers SET is_active=0 WHERE id=%s", (mgr['id'],))
    expires = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    execute(conn,
        "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, student_id, temp_password_expires, must_change_password) VALUES (%s,%s,%s,%s,1,%s,%s,1)",
        (new_manager_id, student['name'], hash_pass(temp_password), student['bkash_number'], student['id'], expires)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"admin_transfer_manager: {student['name']} ({roll}) -> {new_manager_id}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Manager role transferred to {student["name"]} ({roll}). Login ID: {new_manager_id}.'})


@app.route('/admin/add_manager', methods=['POST'])
@admin_required
def admin_add_manager():
    data           = request.json or {}
    roll           = (data.get('roll_number') or '').strip()
    new_manager_id = (data.get('new_manager_id') or '').strip().upper()
    temp_password  = (data.get('temp_password') or '').strip()
    if not roll or not new_manager_id or not temp_password or len(temp_password) < 6:
        return jsonify({'ok': False, 'msg': 'Roll, Manager ID, and temp password (min 6 chars) required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name, bkash_number FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'No student found with roll "{roll}".'})
    if queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s", (new_manager_id,)):
        conn.close()
        return jsonify({'ok': False, 'msg': f'Manager ID "{new_manager_id}" is already taken.'})
    active_count = queryOne(conn, "SELECT COUNT(*) as c FROM meal_managers WHERE is_active=1")['c']
    if active_count >= 4:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Maximum 4 active managers allowed.'})
    expires = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    execute(conn,
        "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, student_id, temp_password_expires, must_change_password) VALUES (%s,%s,%s,%s,1,%s,%s,1)",
        (new_manager_id, student['name'], hash_pass(temp_password), student['bkash_number'], student['id'], expires)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"add_manager: {student['name']} ({roll}) -> {new_manager_id}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Manager {new_manager_id} created for {student["name"]}.'})

# ── TOTAL BILL ────────────────────────────────────────────────────────────────

def _get_total_bill(bill_date):
    conn = get_db()
    lunch_total  = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",  (bill_date,))['c']
    dinner_total = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", (bill_date,))['c']
    male_lunch   = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch'  AND s.gender='male'", (bill_date,))['c']
    male_dinner  = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='male'", (bill_date,))['c']
    female_lunch = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch'  AND s.gender='female'", (bill_date,))['c']
    female_dinner = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='female'", (bill_date,))['c']
    floor_rows = query(conn, """
        SELECT s.floor,
               SUM(CASE WHEN mo.meal_type='lunch'  THEN 1 ELSE 0 END) as lunch,
               SUM(CASE WHEN mo.meal_type='dinner' THEN 1 ELSE 0 END) as dinner,
               COUNT(*) as total
        FROM meal_orders mo JOIN students s ON s.id=mo.student_id
        WHERE mo.meal_date=%s AND s.gender='male'
        GROUP BY s.floor ORDER BY s.floor
    """, (bill_date,))
    conn.close()
    return {
        'date': bill_date, 'price_per_meal': 50,
        'lunch_total': lunch_total, 'dinner_total': dinner_total,
        'total_meals': lunch_total + dinner_total,
        'grand_total': (lunch_total + dinner_total) * 50,
        'male_lunch': male_lunch, 'male_dinner': male_dinner,
        'female_lunch': female_lunch, 'female_dinner': female_dinner,
        'floors': [{'floor': r['floor'], 'lunch': r['lunch'], 'dinner': r['dinner'], 'total': r['total']} for r in floor_rows],
    }


@app.route('/admin/total_bill')
@admin_required
def admin_total_bill():
    return jsonify(_get_total_bill(request.args.get('date', str(date.today()))))


@app.route('/manager/total_bill')
@login_required('manager')
def total_bill():
    return jsonify(_get_total_bill(request.args.get('date', str(date.today()))))


@app.route('/manager/ordered_not_paid')
@login_required('manager')
def manager_ordered_not_paid():
    """
    Return students who have placed meal orders but have NOT paid
    (payment_status = 'pending' or 'due') — ALL unpaid orders, no date filter.
    This matches the "Students Who Forgot to Pay" panel exactly.
    """
    conn = get_db()
    rows = query(conn, """
        SELECT
            s.name,
            s.roll_number,
            s.batch,
            s.floor,
            s.gender,
            s.bkash_number,
            COUNT(mo.id)        AS order_count,
            SUM(mo.amount)      AS total_amount,
            MIN(mo.meal_date)   AS earliest_order,
            MAX(mo.meal_date)   AS latest_order,
            STRING_AGG(DISTINCT mo.meal_date || \'\' || mo.meal_type, \', \'
                       ORDER BY mo.meal_date || \'\' || mo.meal_type) AS order_details
        FROM students s
        JOIN meal_orders mo ON mo.student_id = s.id
        WHERE mo.payment_status IN (\'pending\', \'due\')
          AND s.is_demo = 0
        GROUP BY s.id, s.name, s.roll_number, s.batch, s.floor, s.gender, s.bkash_number
        HAVING COUNT(mo.id) > 0
        ORDER BY total_amount DESC, s.name
    """)
    conn.close()
    return jsonify({
        'ok':      True,
        'count':   len(rows),
        'students': [dict(r) for r in rows]
    })

# ── ADMIN: ADD / SEED / ERASE STUDENTS ───────────────────────────────────────

@app.route('/admin/add_student', methods=['POST'])
@admin_required
def admin_add_student():
    d         = request.json or {}
    name      = (d.get('name') or '').strip()
    roll_raw  = str(d.get('roll_number') or '').strip()
    batch_raw = str(d.get('batch') or '').strip()
    gender    = (d.get('gender') or 'male').strip()
    floor_val = d.get('floor')
    bkash     = (d.get('bkash_number') or '').strip()
    password  = (d.get('password') or '').strip()
    if not all([name, roll_raw, batch_raw, gender, bkash, password]) or len(password) < 6:
        return jsonify({'ok': False, 'msg': 'All fields required, password min 6 chars.'})
    try:
        batch_num = int(batch_raw)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Batch must be a number.'})
    current_year = datetime.now().year
    max_batch    = min(8 + max(0, current_year - 2026), 20)
    if batch_num < 2 or batch_num > max_batch:
        return jsonify({'ok': False, 'msg': f'Batch must be between 2 and {max_batch}.'})
    try:
        roll_num = int(roll_raw)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Roll number must be a number between 1 and 75.'})
    if roll_num < 1 or roll_num > 75:
        return jsonify({'ok': False, 'msg': 'Roll number must be between 1 and 75.'})
    roll_number = f'{batch_num}-{roll_num}'
    if gender == 'male':
        try:
            floor_val = int(floor_val)
            if floor_val < 1 or floor_val > 7:
                return jsonify({'ok': False, 'msg': 'Floor must be between 1 and 7.'})
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'Please select a floor for male students.'})
    else:
        # Female hostel: 1=Campus, 2=Sentu House, 3=Chairman House
        try:
            floor_val = int(floor_val)
            if floor_val not in (1, 2, 3):
                return jsonify({'ok': False, 'msg': 'Please select a hostel for female students.'})
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'Please select a hostel for female students.'})
    conn = get_db()
    try:
        execute(conn,
            "INSERT INTO students (name, batch, roll_number, bkash_number, password, gender, floor) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (name, str(batch_num), roll_number, bkash, hash_pass(password), gender, floor_val)
        )
        execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
                (session['admin_id'], f"add_student: {name} roll {roll_number}"))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'msg': f"Student '{name}' added with roll {roll_number}."})
    except PgIntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'ok': False, 'msg': f'Roll number {roll_number} is already taken.'})


@app.route('/admin/generate_manager_reset_code', methods=['POST'])
@admin_required
def admin_generate_manager_reset_code():
    data       = request.json or {}
    manager_id = (data.get('manager_id') or '').strip().upper()
    if not manager_id:
        return jsonify({'ok': False, 'msg': 'Manager ID is required.'})
    conn = get_db()
    mgr  = queryOne(conn, "SELECT id, name FROM meal_managers WHERE manager_id=%s AND is_active=1", (manager_id,))
    if not mgr:
        conn.close()
        return jsonify({'ok': False, 'msg': f'No active manager "{manager_id}" found.'})
    reset_code = generate_temp_password(10)
    expires    = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    execute(conn,
        "UPDATE meal_managers SET password=%s, temp_password_expires=%s, must_change_password=1 WHERE id=%s",
        (hash_pass(reset_code), expires, mgr['id'])
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"generate_reset_code: {mgr['name']} ({manager_id})"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'manager_id': manager_id, 'name': mgr['name'],
                    'reset_code': reset_code, 'expires': expires,
                    'msg': f'Reset code generated for {mgr["name"]} ({manager_id}).'})

# ── BACKUP / EXPORT ───────────────────────────────────────────────────────────

import json as _json
from flask import Response as _Response

@app.route('/admin/backup_status')
@admin_required
def admin_backup_status():
    return jsonify({
        'ok': True,
        'message': 'Use /admin/export_json to download a full JSON backup of all tables.',
        'backups': []
    })


@app.route('/admin/backup_now', methods=['POST'])
@admin_required
def admin_backup_now():
    return jsonify({
        'ok': True,
        'msg': 'Use the Download JSON Backup button on the dashboard to export all data.'
    })


@app.route('/admin/export_json')
@admin_required
def admin_export_json():
    """Export all key tables as a downloadable JSON backup."""
    conn   = get_db()
    tables = [
        'students', 'meal_managers', 'meal_orders', 'payments',
        'cash_payment_requests', 'manager_history', 'floor_change_requests',
        'phone_change_requests', 'registration_codes', 'site_settings',
        'admin_reset_log',
    ]
    backup = {}
    for table in tables:
        try:
            rows = query(conn, f"SELECT * FROM {table}")
            backup[table] = [dict(r) for r in rows]
        except Exception as e:
            backup[table] = {'error': str(e)}
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], 'export_json: full database export downloaded'))
    conn.commit()
    conn.close()
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename  = f"nmms_backup_{timestamp}.json"
    return _Response(
        _json.dumps(backup, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

# ── MAINTENANCE MODE ──────────────────────────────────────────────────────────

def is_maintenance_mode():
    try:
        conn = get_db()
        row  = queryOne(conn, "SELECT value FROM site_settings WHERE key='maintenance_mode'")
        conn.close()
        return row and row['value'] == '1'
    except Exception:
        return False


def get_maintenance_message():
    try:
        conn = get_db()
        row  = queryOne(conn, "SELECT value FROM site_settings WHERE key='maintenance_message'")
        conn.close()
        return row['value'] if row else ''
    except Exception:
        return ''


@app.before_request
def check_maintenance():
    allowed_exact   = ['/', '/admin/login', '/admin/logout']
    allowed_prefixes = ['/admin/', '/static/', '/favicon']
    if request.path in allowed_exact:
        return None
    if any(request.path.startswith(p) for p in allowed_prefixes):
        return None
    if is_maintenance_mode():
        return render_template('maintenance.html', maintenance_message=get_maintenance_message()), 503


@app.route('/admin/maintenance_status')
@admin_required
def admin_maintenance_status():
    conn     = get_db()
    mode_row = queryOne(conn, "SELECT value FROM site_settings WHERE key='maintenance_mode'")
    msg_row  = queryOne(conn, "SELECT value FROM site_settings WHERE key='maintenance_message'")
    conn.close()
    return jsonify({
        'maintenance': bool(mode_row and mode_row['value'] == '1'),
        'message':     msg_row['value'] if msg_row else '',
    })


@app.route('/admin/set_maintenance', methods=['POST'])
@admin_required
def admin_set_maintenance():
    data    = request.json
    enabled = '1' if data.get('enabled') else '0'
    message = (data.get('message') or '').strip()
    conn = get_db()
    execute(conn,
        "INSERT INTO site_settings (key, value) VALUES ('maintenance_mode', %s) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')",
        (enabled,)
    )
    execute(conn,
        "INSERT INTO site_settings (key, value) VALUES ('maintenance_message', %s) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')",
        (message,)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"maintenance_{'ON' if enabled=='1' else 'OFF'}: {message or 'no message'}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'maintenance': enabled == '1', 'message': message})


@app.route('/maintenance_preview')
@admin_required
def maintenance_preview():
    return render_template('maintenance.html', maintenance_message=get_maintenance_message())

# ── MANAGER ROTATION ──────────────────────────────────────────────────────────

@app.route('/manager/rotation')
@login_required('manager')
def manager_get_rotation():
    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    next_week  = (today - timedelta(days=today.weekday()) + timedelta(weeks=1)).isoformat()
    conn       = get_db()
    def fetch_week(ws):
        rows = query(conn, "SELECT * FROM manager_rotation WHERE week_start=%s ORDER BY slot", (ws,))
        return [dict(r) for r in rows]
    result = {
        'week_start': week_start, 'next_week_start': next_week,
        'this_week': fetch_week(week_start), 'next_week': fetch_week(next_week),
        'today': today.isoformat(), 'weekday': today.weekday(),
    }
    conn.close()
    return jsonify(result)


@app.route('/manager/rotation/save', methods=['POST'])
@login_required('manager')
def manager_save_rotation():
    data       = request.json
    week_start = data.get('week_start')
    slots      = data.get('slots', [])
    if not week_start or not slots:
        return jsonify({'ok': False, 'msg': 'week_start and slots are required.'})
    conn     = get_db()
    short_ws = week_start.replace('-', '')[2:]
    execute(conn, "DELETE FROM manager_rotation WHERE week_start=%s", (week_start,))
    for s in slots:
        execute(conn,
            "INSERT INTO manager_rotation (week_start, slot, student_id, student_name, roll_number, day_from, day_to, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (week_start, s.get('slot'), s.get('student_id'), s.get('student_name'),
             s.get('roll_number'), s.get('day_from', 1), s.get('day_to', 7), s.get('note', ''))
        )
    execute(conn, "DELETE FROM duty_invites WHERE week_start=%s AND status='pending'", (week_start,))
    first_duty_id = None
    for s in slots:
        sid          = s.get('student_id')
        student_name = (s.get('student_name') or '').strip()
        slot         = s.get('slot', 1)
        if sid:
            first_name   = student_name.split()[0] if student_name else f's{slot}'
            name_slug    = ''.join(c for c in first_name.lower() if c.isalnum())[:12] or f's{slot}'
            base_id      = f"DUTY-{short_ws}-{name_slug}"
            slot_duty_id = base_id
            suffix_n     = 2
            while queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s AND is_active=1", (slot_duty_id,)):
                slot_duty_id = f"{base_id}{suffix_n}"
                suffix_n    += 1
            slot_duty_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
            if first_duty_id is None:
                first_duty_id = slot_duty_id
            execute(conn,
                "INSERT INTO duty_invites (week_start, student_id, slot, duty_id, duty_password, status) VALUES (%s,%s,%s,%s,%s,'pending')",
                (week_start, sid, slot, slot_duty_id, slot_duty_pw)
            )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Rotation saved.', 'duty_id': first_duty_id or f"DUTY-{short_ws}-s1"})


@app.route('/manager/rotation/search_students')
@login_required('manager')
def rotation_search_students():
    q    = request.args.get('q', '').strip()
    conn = get_db()
    rows = query(conn,
        "SELECT id, name, roll_number, batch FROM students WHERE name LIKE %s OR roll_number LIKE %s LIMIT 12",
        (f'%{q}%', f'%{q}%')
    )
    conn.close()
    return jsonify({'students': [dict(r) for r in rows]})

# ── STUDENT DUTY INVITES ──────────────────────────────────────────────────────

@app.route('/student/duty_invites')
@login_required('student')
def student_duty_invites():
    sid  = session['user_id']
    conn = get_db()
    rows = query(conn,
        "SELECT * FROM duty_invites WHERE student_id=%s ORDER BY created_at DESC", (sid,)
    )
    conn.close()
    return jsonify({'invites': [dict(r) for r in rows]})


@app.route('/student/accept_duty', methods=['POST'])
@login_required('student')
def student_accept_duty():
    d         = request.json
    invite_id = d.get('invite_id')
    sid       = session['user_id']
    conn      = get_db()
    invite = queryOne(conn,
        "SELECT * FROM duty_invites WHERE id=%s AND student_id=%s AND status='pending'",
        (invite_id, sid)
    )
    if not invite:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Invite not found or already responded.'})
    duty_id  = invite['duty_id']
    duty_pw  = invite['duty_password']
    week_str = invite['week_start']
    student  = queryOne(conn, "SELECT * FROM students WHERE id=%s", (sid,))
    bkash    = student['bkash_number'] if student else '01000000000'
    already_exists = queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id=%s", (duty_id,))
    if already_exists:
        execute(conn,
            "UPDATE duty_invites SET status='accepted', accepted_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
            (invite_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'duty_id': duty_id, 'duty_password': duty_pw,
                        'week_start': week_str, 'slot': invite['slot']})
    week_accepted_count = queryOne(conn,
        "SELECT COUNT(*) as c FROM duty_invites WHERE week_start=%s AND status='accepted'", (week_str,)
    )['c']
    if week_accepted_count == 0:
        old_mgrs = query(conn, "SELECT * FROM meal_managers WHERE is_active=1")
        for mgr in old_mgrs:
            mgr_roll = None
            if mgr['student_id']:
                s_row = queryOne(conn, "SELECT roll_number FROM students WHERE id=%s", (mgr['student_id'],))
                if s_row:
                    mgr_roll = s_row['roll_number']
            execute(conn,
                "INSERT INTO manager_history (manager_id, student_name, roll_number, batch, floor, assigned_by, tenure_start, tenure_end) "
                "VALUES (%s,%s,%s,%s,%s,'duty_rotation',%s,to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))",
                (mgr['manager_id'], mgr['name'], mgr_roll, None, None, mgr['created_at'])
            )
            execute(conn, "UPDATE meal_managers SET is_active=0 WHERE id=%s", (mgr['id'],))
    execute(conn,
        "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, student_id, must_change_password) VALUES (%s,%s,%s,%s,1,%s,0)",
        (duty_id, student['name'] if student else 'Duty Manager', hash_pass(duty_pw), bkash, sid)
    )
    execute(conn,
        "UPDATE duty_invites SET status='accepted', accepted_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
        (invite_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'duty_id': duty_id, 'duty_password': duty_pw,
                    'week_start': week_str, 'slot': invite['slot']})


@app.route('/student/duty_credentials')
@login_required('student')
def student_duty_credentials():
    sid  = session['user_id']
    conn = get_db()
    rows = query(conn,
        "SELECT * FROM duty_invites WHERE student_id=%s AND status='accepted' ORDER BY created_at DESC", (sid,)
    )
    conn.close()
    return jsonify({'credentials': [dict(r) for r in rows]})

# ── BKASH PROPOSAL ────────────────────────────────────────────────────────────

@app.route('/manager/bkash_propose', methods=['POST'])
@login_required('manager')
def manager_bkash_propose():
    data           = request.json or {}
    proposed_bkash = (data.get('bkash_number') or '').strip()
    if not proposed_bkash or not re.match(r'^01[3-9]\d{8}$', proposed_bkash):
        return jsonify({'ok': False, 'msg': 'Valid bKash number required.'})
    conn       = get_db()
    my_id      = session['user_id']
    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    execute(conn,
        "UPDATE bkash_proposals SET status='cancelled', resolved_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') "
        "WHERE proposer_manager_id=%s AND status='pending'",
        (my_id,)
    )
    others = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_managers WHERE is_active=1 AND id!=%s", (my_id,)
    )['c']
    if others == 0:
        execute(conn, "UPDATE meal_managers SET bkash_number=%s WHERE is_active=1", (proposed_bkash,))
        cur2 = conn.cursor()
        cur2.execute(
            "INSERT INTO bkash_proposals (proposer_manager_id, proposed_bkash, status, week_start, resolved_at) "
            "VALUES (%s,%s,'approved',%s,to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')) RETURNING id",
            (my_id, proposed_bkash, week_start)
        )
        pid = cur2.fetchone()['id']
        execute(conn,
            "INSERT INTO weekly_bkash (week_start, bkash_number, proposal_id) VALUES (%s,%s,%s) "
            "ON CONFLICT(week_start) DO UPDATE SET bkash_number=EXCLUDED.bkash_number, proposal_id=EXCLUDED.proposal_id",
            (week_start, proposed_bkash, pid)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'msg': f'bKash updated to {proposed_bkash} (sole manager).', 'auto_approved': True})
    cur2 = conn.cursor()
    cur2.execute(
        "INSERT INTO bkash_proposals (proposer_manager_id, proposed_bkash, week_start) VALUES (%s,%s,%s) RETURNING id",
        (my_id, proposed_bkash, week_start)
    )
    proposal_id = cur2.fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'proposal_id': proposal_id,
                    'msg': 'Proposal sent! 2 approvals needed.', 'auto_approved': False})


@app.route('/manager/bkash_vote', methods=['POST'])
@login_required('manager')
def manager_bkash_vote():
    data        = request.json or {}
    proposal_id = data.get('proposal_id')
    vote        = data.get('vote')
    if vote not in ('approve', 'reject'):
        return jsonify({'ok': False, 'msg': "vote must be 'approve' or 'reject'."})
    conn  = get_db()
    my_id = session['user_id']
    proposal = queryOne(conn, "SELECT * FROM bkash_proposals WHERE id=%s AND status='pending'", (proposal_id,))
    if not proposal:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Proposal not found or already resolved.'})
    if proposal['proposer_manager_id'] == my_id:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You cannot vote on your own proposal.'})
    try:
        execute(conn,
            "INSERT INTO bkash_proposal_votes (proposal_id, voter_manager_id, vote) VALUES (%s,%s,%s)",
            (proposal_id, my_id, vote)
        )
    except PgIntegrityError:
        conn.rollback()
        execute(conn,
            "UPDATE bkash_proposal_votes SET vote=%s, voted_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') "
            "WHERE proposal_id=%s AND voter_manager_id=%s",
            (vote, proposal_id, my_id)
        )
    approve_count = queryOne(conn,
        "SELECT COUNT(*) as c FROM bkash_proposal_votes WHERE proposal_id=%s AND vote='approve'", (proposal_id,)
    )['c']
    reject_count = queryOne(conn,
        "SELECT COUNT(*) as c FROM bkash_proposal_votes WHERE proposal_id=%s AND vote='reject'", (proposal_id,)
    )['c']
    result_msg = f'Vote recorded. Approvals: {approve_count}/2'
    resolved   = False
    if approve_count >= 2:
        new_bkash = proposal['proposed_bkash']
        execute(conn, "UPDATE meal_managers SET bkash_number=%s WHERE is_active=1", (new_bkash,))
        execute(conn,
            "UPDATE bkash_proposals SET status='approved', resolved_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
            (proposal_id,)
        )
        week_start = proposal['week_start']
        if week_start:
            execute(conn,
                "INSERT INTO weekly_bkash (week_start, bkash_number, proposal_id) VALUES (%s,%s,%s) "
                "ON CONFLICT(week_start) DO UPDATE SET bkash_number=EXCLUDED.bkash_number, proposal_id=EXCLUDED.proposal_id",
                (week_start, new_bkash, proposal_id)
            )
        result_msg = f'✅ Approved! bKash updated to {new_bkash}.'
        resolved   = True
    elif reject_count >= 1:
        execute(conn,
            "UPDATE bkash_proposals SET status='rejected', resolved_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE id=%s",
            (proposal_id,)
        )
        result_msg = '❌ Proposal rejected.'
        resolved   = True
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': result_msg, 'resolved': resolved,
                    'approvals': approve_count, 'rejections': reject_count, 'needed': 2})


@app.route('/manager/bkash_proposals')
@login_required('manager')
def manager_bkash_proposals():
    conn  = get_db()
    my_id = session['user_id']
    proposals = query(conn, """
        SELECT bp.*,
               mm.name as proposer_name, mm.manager_id as proposer_mgr_id,
               (SELECT COUNT(*) FROM bkash_proposal_votes v WHERE v.proposal_id=bp.id AND v.vote='approve') as approve_count,
               (SELECT COUNT(*) FROM bkash_proposal_votes v WHERE v.proposal_id=bp.id AND v.vote='reject')  as reject_count,
               (SELECT v.vote FROM bkash_proposal_votes v WHERE v.proposal_id=bp.id AND v.voter_manager_id=%s) as my_vote,
               (SELECT COUNT(*) FROM meal_managers WHERE is_active=1) as total_managers
        FROM bkash_proposals bp JOIN meal_managers mm ON mm.id=bp.proposer_manager_id
        WHERE bp.status='pending' ORDER BY bp.created_at DESC
    """, (my_id,))
    recent = query(conn, """
        SELECT bp.*, mm.name as proposer_name, mm.manager_id as proposer_mgr_id
        FROM bkash_proposals bp JOIN meal_managers mm ON mm.id=bp.proposer_manager_id
        WHERE bp.status != 'pending' ORDER BY bp.resolved_at DESC LIMIT 10
    """)
    conn.close()
    return jsonify({'my_id': my_id, 'pending': [dict(r) for r in proposals],
                    'resolved': [dict(r) for r in recent], 'rule': '2_approvals'})

# ── EMERGENCY RESET ───────────────────────────────────────────────────────────

@app.route('/admin/debug_female_students')
@admin_required
def admin_debug_female_students():
    """Show raw DB values for all female students — use to diagnose hostel issues."""
    conn = get_db()
    rows = query(conn, "SELECT id, name, roll_number, batch, floor, gender FROM students WHERE gender='female' ORDER BY batch, roll_number")
    breakdown = query(conn, """
        SELECT s.floor, COUNT(*) as cnt
        FROM students s WHERE s.gender='female' GROUP BY s.floor ORDER BY s.floor
    """)
    conn.close()
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}
    students_out = [{'id': r['id'], 'name': r['name'], 'roll': r['roll_number'],
                     'floor_raw': r['floor'], 'floor_type': type(r['floor']).__name__,
                     'hostel': HOSTEL_NAMES.get(r['floor'], f'UNKNOWN({r["floor"]})')} for r in rows]
    breakdown_out = [{'floor': r['floor'], 'floor_type': type(r['floor']).__name__, 'count': r['cnt']} for r in breakdown]
    return jsonify({'students': students_out, 'breakdown': breakdown_out})

@app.route('/emergency_reset')
def emergency_reset():
    key      = os.environ.get('EMERGENCY_KEY', '')
    provided = request.args.get('key', '')
    if not key or provided != key:
        return 'Forbidden — set EMERGENCY_KEY env var and provide ?key=... to use this route.', 403
    new_pass  = request.args.get('new_pass', 'manager123').strip()
    new_bkash = request.args.get('new_bkash', '').strip()
    if len(new_pass) < 6:
        return 'Error: new_pass must be at least 6 characters.', 400
    conn     = get_db()
    existing = queryOne(conn, "SELECT id FROM meal_managers WHERE manager_id='MGR001'")
    if existing:
        execute(conn,
            "UPDATE meal_managers SET password=%s, must_change_password=0, temp_password_expires=NULL, is_active=1 WHERE manager_id='MGR001'",
            (hash_pass(new_pass),)
        )
        if new_bkash:
            execute(conn, "UPDATE meal_managers SET bkash_number=%s WHERE manager_id='MGR001'", (new_bkash,))
    else:
        mgr_bkash = new_bkash or os.environ.get('MANAGER_BKASH', '01712345678')
        execute(conn,
            "INSERT INTO meal_managers (manager_id, name, password, bkash_number, is_active, must_change_password) VALUES ('MGR001','Meal Manager',%s,%s,1,0)",
            (hash_pass(new_pass), mgr_bkash)
        )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES ('SYSTEM','emergency_reset: MGR001 password reset')")
    conn.commit()
    conn.close()
    return (
        f'<h2>✅ Done!</h2>'
        f'<p>MGR001 password reset to <strong>{new_pass}</strong>.</p>'
        f'<p><a href="/manager/login">Go to Manager Login →</a></p>'
        f'<p style="color:red"><strong>Important:</strong> Remove EMERGENCY_KEY from env now.</p>'
    ), 200

# ── ROTATION CLEAR ────────────────────────────────────────────────────────────

@app.route('/manager/rotation/clear', methods=['POST'])
@login_required('manager')
def manager_clear_rotation():
    data       = request.json or {}
    week_start = data.get('week_start', '').strip()
    if not week_start:
        return jsonify({'ok': False, 'msg': 'week_start is required.'})
    conn = get_db()
    execute(conn, "DELETE FROM manager_rotation WHERE week_start=%s", (week_start,))
    execute(conn, "DELETE FROM duty_invites WHERE week_start=%s AND status='pending'", (week_start,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Rotation for week {week_start} cleared.'})


@app.route('/admin/rotation/clear', methods=['POST'])
@admin_required
def admin_clear_rotation():
    data       = request.json or {}
    week_start = data.get('week_start', '').strip()
    conn       = get_db()
    if week_start:
        execute(conn, "DELETE FROM manager_rotation WHERE week_start=%s", (week_start,))
        execute(conn, "DELETE FROM duty_invites WHERE week_start=%s AND status='pending'", (week_start,))
        msg = f'Rotation for week {week_start} cleared.'
    else:
        execute(conn, "DELETE FROM manager_rotation")
        execute(conn, "DELETE FROM duty_invites WHERE status='pending'")
        msg = 'All rotation schedules cleared.'
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"clear_rotation: {msg}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': msg})

# ── OVERDUE / NON-ORDERERS ────────────────────────────────────────────────────

@app.route('/manager/overdue_students')
@login_required('manager')
def manager_overdue_students():
    conn  = get_db()
    # Show every student who currently has unpaid meal orders.
    # Removed the "paid in last 30 days" exclusion — it was hiding repeat non-payers.
    rows  = query(conn, """
        SELECT s.id, s.name, s.roll_number, s.batch, s.bkash_number, s.gender, s.floor,
               COUNT(mo.id) as meal_count,
               COALESCE(SUM(COALESCE(mo.amount, 50.0)), 0) as total_due,
               MIN(mo.meal_date) as oldest_unpaid,
               CAST(EXTRACT(DAY FROM (NOW() AT TIME ZONE 'UTC' - MIN(mo.meal_date)::timestamp)) AS INTEGER) as days_overdue,
               EXISTS(
                   SELECT 1 FROM payments p
                   WHERE p.student_id = s.id AND p.status = 'pending_verification'
               ) AS has_pending_proof,
               EXISTS(
                   SELECT 1 FROM cash_payment_requests cpr
                   WHERE cpr.student_id = s.id AND cpr.status = 'pending'
               ) AS has_pending_cash
        FROM students s
        JOIN meal_orders mo ON mo.student_id = s.id
        WHERE mo.payment_status IN ('pending', 'due')
          AND s.is_demo = 0
        GROUP BY s.id HAVING COUNT(mo.id) > 0
        ORDER BY has_pending_proof ASC, has_pending_cash ASC, days_overdue DESC, total_due DESC
    """)
    conn.close()
    return jsonify({'students': [dict(r) for r in rows]})


@app.route('/manager/non_orderers')
@login_required('manager')
def manager_non_orderers():
    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end   = (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat()
    conn       = get_db()
    rows = query(conn, """
        SELECT s.id, s.name, s.roll_number, s.batch, s.gender, s.bkash_number, s.ordering_locked,
               (SELECT COUNT(*) FROM meal_orders mo WHERE mo.student_id=s.id AND mo.meal_date>=%s AND mo.meal_date<=%s) as orders_this_week
        FROM students s ORDER BY s.batch, s.roll_number
    """, (week_start, week_end))
    conn.close()
    all_students = [dict(r) for r in rows]
    non_orderers = [r for r in all_students if r['orders_this_week'] == 0]
    all_locked   = [r for r in all_students if r['ordering_locked']]
    return jsonify({
        'week_start': week_start, 'week_end': week_end,
        'non_orderers': non_orderers, 'all_locked': all_locked,
        'all_count': len(all_students), 'non_order_count': len(non_orderers), 'locked_count': len(all_locked),
    })


@app.route('/manager/lock_ordering', methods=['POST'])
@login_required('manager')
def manager_lock_ordering():
    d    = request.json or {}
    roll = (d.get('roll') or '').strip()
    if not roll:
        return jsonify({'ok': False, 'msg': 'roll number required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Student {roll} not found.'})
    execute(conn, "UPDATE students SET ordering_locked=1 WHERE id=%s", (student['id'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Ordering locked for {student["name"]} ({roll}).'})


@app.route('/manager/unlock_ordering', methods=['POST'])
@login_required('manager')
def manager_unlock_ordering():
    d    = request.json or {}
    roll = (d.get('roll') or '').strip()
    if not roll:
        return jsonify({'ok': False, 'msg': 'roll number required.'})
    conn    = get_db()
    student = queryOne(conn, "SELECT id, name FROM students WHERE roll_number=%s", (roll,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Student {roll} not found.'})
    execute(conn, "UPDATE students SET ordering_locked=0 WHERE id=%s", (student['id'],))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Ordering unlocked for {student["name"]} ({roll}).'})


@app.route('/manager/auto_lock_non_orderers', methods=['POST'])
@login_required('manager')
def manager_auto_lock_non_orderers():
    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week_end   = (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat()
    conn       = get_db()
    rows = query(conn, """
        SELECT s.id FROM students s
        WHERE s.ordering_locked=0
          AND NOT EXISTS (
              SELECT 1 FROM meal_orders mo WHERE mo.student_id=s.id AND mo.meal_date>=%s AND mo.meal_date<=%s
          )
    """, (week_start, week_end))
    locked_count = 0
    for r in rows:
        execute(conn, "UPDATE students SET ordering_locked=1 WHERE id=%s", (r['id'],))
        locked_count += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'locked': locked_count,
                    'msg': f'Auto-locked {locked_count} student(s) who did not order this week.'})


@app.route('/manager/unlock_all_ordering', methods=['POST'])
@login_required('manager')
def manager_unlock_all_ordering():
    conn = get_db()
    execute(conn, "UPDATE students SET ordering_locked=0")
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Ordering lock cleared for all students.'})


@app.route('/api/non_orderers_summary')
def api_non_orderers_summary():
    role = session.get('role')
    if role not in ('manager', 'admin'):
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    today     = (datetime.utcnow() + timedelta(hours=6)).date()
    three_ago = (today - timedelta(days=3)).isoformat()
    conn      = get_db()
    rows = query(conn, """
        SELECT s.id, s.name, s.roll_number, s.batch, s.gender, s.ordering_locked,
               (SELECT COUNT(*) FROM meal_orders mo WHERE mo.student_id=s.id AND mo.meal_date>=%s) as recent_orders
        FROM students s ORDER BY s.batch, s.roll_number
    """, (three_ago,))
    total        = len(rows)
    non_orderers = [dict(r) for r in rows if r['recent_orders'] == 0]
    locked_count = sum(1 for r in non_orderers if r['ordering_locked'])
    conn.close()
    return jsonify({'ok': True, 'total': total, 'non_orderers': non_orderers,
                    'non_order_count': len(non_orderers), 'locked_count': locked_count,
                    'since_date': three_ago, 'today': today.isoformat()})

# ── INVITE CODES ──────────────────────────────────────────────────────────────

import string as _string

def _gen_invite_code(length=10):
    alphabet = _string.ascii_uppercase + _string.digits
    return ''.join(_secrets.choice(alphabet) for _ in range(length))


@app.route('/admin/generate_invite', methods=['POST'])
@admin_required
def admin_generate_invite():
    data  = request.json or {}
    count = min(int(data.get('count', 1)), 50)
    batch = (data.get('batch') or '').strip() or None
    note  = (data.get('note')  or '').strip()[:100]
    conn  = get_db()

    # ── One active code per batch — enforce uniqueness ────────────────────────
    # Codes with a batch lock must be unique per batch while still active.
    # "Any batch" (batch=None) codes are not subject to this rule.
    if batch:
        existing = queryOne(conn,
            "SELECT code FROM registration_codes WHERE batch=%s AND is_used=0",
            (batch,)
        )
        if existing:
            conn.close()
            return jsonify({
                'ok':  False,
                'msg': f'Batch {batch} already has an active unused code: {existing["code"]}. '
                       f'Revoke or use it before generating a new one.'
            })
        # Also cap at 1 when a batch is specified — one code, one batch
        count = 1

    codes = []
    for _ in range(count):
        for attempt in range(10):
            code = _gen_invite_code(10)
            try:
                execute(conn,
                    "INSERT INTO registration_codes (code, batch, created_by, note) VALUES (%s,%s,%s,%s)",
                    (code, batch, session['admin_id'], note)
                )
                codes.append(code)
                break
            except PgIntegrityError:
                conn.rollback()
                continue
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f"generate_invite: {len(codes)} code(s) batch={batch or 'any'}"))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'codes': codes, 'count': len(codes), 'batch': batch, 'note': note})


@app.route('/admin/list_invites')
@admin_required
def admin_list_invites():
    conn = get_db()
    rows = query(conn, "SELECT * FROM registration_codes ORDER BY is_used ASC, created_at DESC LIMIT 200")
    conn.close()
    return jsonify({'codes': [dict(r) for r in rows]})


@app.route('/admin/revoke_invite', methods=['POST'])
@admin_required
def admin_revoke_invite():
    data = request.json or {}
    code = (data.get('code') or '').strip().upper()
    if not code:
        return jsonify({'ok': False, 'msg': 'Code is required.'})
    conn = get_db()
    row  = queryOne(conn, "SELECT id, is_used FROM registration_codes WHERE code=%s", (code,))
    if not row:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Code not found.'})
    if row['is_used']:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Code is already used/revoked.'})
    execute(conn,
        "UPDATE registration_codes SET is_used=1, used_by='REVOKED', used_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') WHERE code=%s",
        (code,)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f'revoke_invite: Revoked {code}'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Code {code} revoked.'})


@app.route('/admin/delete_used_invites', methods=['POST'])
@admin_required
def admin_delete_used_invites():
    conn    = get_db()
    cur2    = conn.cursor()
    cur2.execute("DELETE FROM registration_codes WHERE is_used=1")
    deleted = cur2.rowcount
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f'delete_used_invites: Deleted {deleted} codes'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Deleted {deleted} used/revoked codes.'})


@app.route('/admin/validate_invite')
@admin_required
def admin_validate_invite():
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({'ok': False, 'msg': 'Code required.'})
    conn = get_db()
    row  = queryOne(conn, "SELECT * FROM registration_codes WHERE code=%s", (code,))
    conn.close()
    if not row:
        return jsonify({'ok': False, 'msg': 'Code not found.'})
    return jsonify({'ok': True, 'code': dict(row)})


@app.route('/admin/delete_all_invites', methods=['POST'])
@admin_required
def admin_delete_all_invites():
    conn    = get_db()
    cur2    = conn.cursor()
    cur2.execute("DELETE FROM registration_codes")
    deleted = cur2.rowcount
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'], f'delete_all_invites: Deleted {deleted} codes'))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Deleted all {deleted} invite codes.'})


# ── DEBT BLOCK ────────────────────────────────────────────────────────────────

def is_debt_blocked(student_id, conn=None):
    """Return True if the student is currently blocked due to old unpaid debt."""
    close = False
    if conn is None:
        conn  = get_db()
        close = True
    try:
        row = queryOne(conn, "SELECT debt_blocked FROM students WHERE id=%s", (student_id,))
        return bool(row and row['debt_blocked'])
    finally:
        if close:
            conn.close()


@app.route('/manager/set_debt_block', methods=['POST'])
@login_required('manager')
def manager_set_debt_block():
    """Block or unblock a student's meal ordering due to unpaid old debt."""
    d          = request.json or {}
    student_id = d.get('student_id')
    blocked    = bool(d.get('blocked'))
    if not student_id:
        return jsonify({'ok': False, 'msg': 'student_id required'})
    conn = get_db()
    execute(conn, "UPDATE students SET debt_blocked=%s WHERE id=%s",
            (1 if blocked else 0, student_id))
    conn.commit()
    conn.close()
    verb = 'blocked' if blocked else 'unblocked'
    return jsonify({'ok': True, 'msg': f'Student meal ordering {verb}.', 'blocked': blocked})


@app.route('/manager/debt_blocked_students')
@login_required('manager')
def manager_debt_blocked_students():
    """Return all currently debt-blocked students."""
    conn = get_db()
    rows = query(conn,
        "SELECT id, name, roll_number, batch, floor, bkash_number FROM students WHERE debt_blocked=1 ORDER BY batch, roll_number"
    )
    conn.close()
    return jsonify({'ok': True, 'students': [dict(r) for r in rows]})


# ── MANAGER DASHBOARD STATS ───────────────────────────────────────────────────

@app.route('/manager/dashboard_stats')
@login_required('manager')
def manager_dashboard_stats():
    """Return live pending_amount and total_received for the stat cards."""
    conn = get_db()
    pending_row = queryOne(conn,
        "SELECT COALESCE(SUM(amount), 0) as total FROM meal_orders WHERE payment_status IN ('pending','due')"
    )
    received_row = queryOne(conn,
        "SELECT COALESCE(SUM(amount), 0) as total FROM payments WHERE status='verified'"
    )
    conn.close()
    return jsonify({
        'ok':             True,
        'pending_amount': float(pending_row['total'] or 0),
        'total_received': float(received_row['total'] or 0),
    })


# ── COOK SHEET ────────────────────────────────────────────────────────────────

@app.route('/manager/cook_sheet')
@login_required('manager')
def manager_cook_sheet():
    req_date = request.args.get('date', date.today().isoformat())
    try:
        datetime.fromisoformat(req_date)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Invalid date.'})
    conn = get_db()
    def gc(meal_type, gender):
        return queryOne(conn,
            "SELECT COUNT(*) as c FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
            "WHERE mo.meal_date=%s AND mo.meal_type=%s AND s.gender=%s",
            (req_date, meal_type, gender)
        )['c']
    lunch_total   = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",  (req_date,))['c']
    dinner_total  = queryOne(conn, "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", (req_date,))['c']
    # Resolve gender counts BEFORE closing the connection
    lunch_female  = gc('lunch',  'female')
    lunch_male    = gc('lunch',  'male')
    dinner_female = gc('dinner', 'female')
    dinner_male   = gc('dinner', 'male')
    floor_lunch   = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch'  AND s.gender='male' GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    floor_dinner  = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='male' GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    hostel_lunch  = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch'  AND s.gender='female' GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    hostel_dinner = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='female' GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    HOSTEL_NAMES = {1: 'Campus', 2: 'Sentu House', 3: 'Chairman House'}
    conn.close()  # safe to close now — all queries done
    return jsonify({
        'ok': True, 'date': req_date,
        'lunch':  {'total': lunch_total,  'female': lunch_female,  'male': lunch_male},
        'dinner': {'total': dinner_total, 'female': dinner_female, 'male': dinner_male},
        'floor_lunch':  [{'floor': r['floor'], 'count': r['count']} for r in floor_lunch],
        'floor_dinner': [{'floor': r['floor'], 'count': r['count']} for r in floor_dinner],
        'hostel_lunch':  [{'floor': r['floor'], 'name': HOSTEL_NAMES.get(r['floor'], f"Hostel {r['floor']}"), 'count': r['count']} for r in hostel_lunch],
        'hostel_dinner': [{'floor': r['floor'], 'name': HOSTEL_NAMES.get(r['floor'], f"Hostel {r['floor']}"), 'count': r['count']} for r in hostel_dinner],
    })

# ── STARTUP ───────────────────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
