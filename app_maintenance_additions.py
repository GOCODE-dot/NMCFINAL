"""
app_maintenance_additions.py
════════════════════════════
Copy-paste these additions into your existing app.py.
Three places to edit — marked with ① ② ③
"""

# ─────────────────────────────────────────────────────────────────────────────
# ① INSIDE init_db() → inside the c.executescript('''  ''') block
#    Add this CREATE TABLE right before the closing  '''  )
# ─────────────────────────────────────────────────────────────────────────────
INIT_DB_ADDITION = """
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
"""


# ─────────────────────────────────────────────────────────────────────────────
# ② AFTER init_db(), BEFORE def login_required(role):
#    Paste the entire block below
# ─────────────────────────────────────────────────────────────────────────────

def is_maintenance_mode():
    """Return True if the site is in maintenance mode."""
    try:
        conn = get_db()
        row  = conn.execute(
            "SELECT value FROM site_settings WHERE key='maintenance_mode'"
        ).fetchone()
        conn.close()
        return row and row['value'] == '1'
    except Exception:
        return False


def get_maintenance_message():
    """Return the custom maintenance message (empty string if unset)."""
    try:
        conn = get_db()
        row  = conn.execute(
            "SELECT value FROM site_settings WHERE key='maintenance_message'"
        ).fetchone()
        conn.close()
        return row['value'] if row else ''
    except Exception:
        return ''


# Runs before EVERY request — blocks non-admins when maintenance is on
from flask import request as _req   # already imported; just for clarity

@app.before_request
def check_maintenance():
    # Admin panel and static assets always pass through
    allowed_prefixes = ['/admin/', '/static/', '/favicon']
    if any(_req.path.startswith(p) for p in allowed_prefixes):
        return None
    if is_maintenance_mode():
        msg = get_maintenance_message()
        return render_template('maintenance.html', maintenance_message=msg), 503


# ─────────────────────────────────────────────────────────────────────────────
# ③ BEFORE the # ── STARTUP ─── section at the bottom of app.py
#    Paste these three new admin routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/maintenance_status')
@admin_required
def admin_maintenance_status():
    """Return current maintenance mode state (used by the dashboard widget)."""
    conn     = get_db()
    mode_row = conn.execute(
        "SELECT value FROM site_settings WHERE key='maintenance_mode'"
    ).fetchone()
    msg_row  = conn.execute(
        "SELECT value FROM site_settings WHERE key='maintenance_message'"
    ).fetchone()
    conn.close()
    return jsonify({
        'maintenance': bool(mode_row and mode_row['value'] == '1'),
        'message':     msg_row['value'] if msg_row else '',
    })


@app.route('/admin/set_maintenance', methods=['POST'])
@admin_required
def admin_set_maintenance():
    """Toggle maintenance mode and save an optional custom message."""
    data    = request.json
    enabled = '1' if data.get('enabled') else '0'
    message = (data.get('message') or '').strip()

    conn = get_db()
    # Upsert maintenance_mode flag
    conn.execute(
        "INSERT INTO site_settings (key, value, updated_at) "
        "VALUES ('maintenance_mode', ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (enabled,)
    )
    # Upsert maintenance_message
    conn.execute(
        "INSERT INTO site_settings (key, value, updated_at) "
        "VALUES ('maintenance_message', ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (message,)
    )
    conn.execute(
        "INSERT INTO admin_reset_log (admin_id, action) VALUES (?,?)",
        (session['admin_id'],
         f"maintenance_{'ON' if enabled == '1' else 'OFF'}: {message or 'no message'}")
    )
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'maintenance': enabled == '1', 'message': message})


@app.route('/maintenance_preview')
@admin_required
def maintenance_preview():
    """Let admins preview the maintenance page without enabling it."""
    msg = get_maintenance_message()
    return render_template('maintenance.html', maintenance_message=msg)
