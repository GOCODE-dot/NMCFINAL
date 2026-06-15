# ═══════════════════════════════════════════════════════════════════════════════
# MIDNIGHT CUTOFF — ADMIN OVERRIDE TOGGLE
# Paste these pieces into app.py at the marked locations.
# Uses the existing `site_settings` key/value table (same one maintenance mode
# uses), so NO new table / migration is needed.
# ═══════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# ① Paste near is_maintenance_mode() / get_maintenance_message()
#    (e.g. right after the "── MAINTENANCE MODE ──" section header)
# ─────────────────────────────────────────────────────────────────────────────

def is_cutoff_override_active():
    """
    Return True if the admin has turned OFF the midnight ordering deadline.
    When True, students can order ANY day's meal at ANY time
    (the per-meal midnight cutoff is ignored, server- and client-side).
    """
    try:
        conn = get_db()
        row  = queryOne(conn, "SELECT value FROM site_settings WHERE key='cutoff_override'")
        conn.close()
        return bool(row and row['value'] == '1')
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ② PATCH /student/order — wrap the existing "MIDNIGHT DEADLINE CHECK" block
#    Find this in app.py (around line 776-793):
#
#        # ── MIDNIGHT DEADLINE CHECK ──────────────────────────────────────
#        try:
#            order_date  = datetime.strptime(meal_date_str, '%Y-%m-%d').date()
#            now_bd      = datetime.utcnow() + timedelta(hours=6)
#            deadline_dt = datetime(order_date.year, order_date.month, order_date.day, 0, 0, 0)
#            if now_bd >= deadline_dt:
#                conn.close()
#                return jsonify({
#                    'ok':    False,
#                    'msg':   f'⏰ Ordering deadline passed — ...',
#                    'locked': True
#                })
#        except ValueError:
#            conn.close()
#            return jsonify({'ok': False, 'msg': '⛔ Invalid date format.'})
#
#    REPLACE that whole block with the version below (only addition is the
#    `if not is_cutoff_override_active():` guard around the deadline check —
#    the date-format validation still always runs).
# ─────────────────────────────────────────────────────────────────────────────

PATCHED_STUDENT_ORDER_DEADLINE_BLOCK = '''
    # ── MIDNIGHT DEADLINE CHECK ──────────────────────────────────────────────
    # To book a meal for Day X, the student must order before 00:00 AM (midnight)
    # of Day X — i.e. right now (BD time) must be before midnight of meal_date.
    # Today's meals are always open until midnight tonight.
    #
    # ADMIN OVERRIDE: if the admin has disabled the cutoff (cutoff_override='1'
    # in site_settings), skip the deadline check entirely.
    try:
        order_date = datetime.strptime(meal_date_str, '%Y-%m-%d').date()
    except ValueError:
        conn.close()
        return jsonify({'ok': False, 'msg': '⛔ Invalid date format.'})

    if not is_cutoff_override_active():
        now_bd      = datetime.utcnow() + timedelta(hours=6)  # Bangladesh time (UTC+6)
        deadline_dt = datetime(order_date.year, order_date.month, order_date.day, 0, 0, 0)
        if now_bd >= deadline_dt:
            conn.close()
            return jsonify({
                'ok':    False,
                'msg':   f'⏰ Ordering deadline passed — meals for {order_date.strftime("%d %b")} had to be booked before midnight. You can still order upcoming days.',
                'locked': True
            })
'''


# ─────────────────────────────────────────────────────────────────────────────
# ③ PATCH /api/ordering_lock_status — let the student dashboard know whether
#    the cutoff is currently overridden, so the client-side JS can stop
#    greying out / disabling meal buttons too.
#
#    FIND (around line 1973-1980):
#
#        @app.route('/api/ordering_lock_status')
#        @login_required('student')
#        def ordering_lock_status():
#            sid  = session['user_id']
#            conn = get_db()
#            row  = queryOne(conn, "SELECT ordering_locked FROM students WHERE id=%s", (sid,))
#            conn.close()
#            return jsonify({'ordering_locked': bool(row and row['ordering_locked'])})
#
#    REPLACE its return line with:
# ─────────────────────────────────────────────────────────────────────────────

PATCHED_ORDERING_LOCK_STATUS = '''
@app.route('/api/ordering_lock_status')
@login_required('student')
def ordering_lock_status():
    sid  = session['user_id']
    conn = get_db()
    row  = queryOne(conn, "SELECT ordering_locked FROM students WHERE id=%s", (sid,))
    conn.close()
    return jsonify({
        'ordering_locked': bool(row and row['ordering_locked']),
        'cutoff_disabled': is_cutoff_override_active(),
    })
'''


# ─────────────────────────────────────────────────────────────────────────────
# ④ NEW ADMIN ROUTES — paste these next to /admin/maintenance_status and
#    /admin/set_maintenance (same "── MAINTENANCE MODE ──" section is a good
#    home, or anywhere with the other /admin/ routes).
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/cutoff_status')
@admin_required
def admin_cutoff_status():
    """Return whether the midnight ordering cutoff is currently overridden (disabled)."""
    return jsonify({'ok': True, 'cutoff_disabled': is_cutoff_override_active()})


@app.route('/admin/set_cutoff_override', methods=['POST'])
@admin_required
def admin_set_cutoff_override():
    """
    Turn the midnight ordering-deadline ON or OFF for everyone.
    Body JSON: { enabled: bool }   -- enabled=true means the CUTOFF IS DISABLED
    (i.e. students can order any day's meal at any time of day).
    """
    data    = request.json or {}
    enabled = '1' if data.get('enabled') else '0'

    conn = get_db()
    execute(conn,
        "INSERT INTO site_settings (key, value) VALUES ('cutoff_override', %s) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS')",
        (enabled,)
    )
    execute(conn, "INSERT INTO admin_reset_log (admin_id, action) VALUES (%s,%s)",
            (session['admin_id'],
             f"midnight_cutoff_override_{'ON' if enabled == '1' else 'OFF'}"))
    conn.commit()
    conn.close()

    return jsonify({
        'ok': True,
        'cutoff_disabled': enabled == '1',
        'msg': ('🔓 Midnight ordering cutoff is now DISABLED — students can order '
                'any day at any time.') if enabled == '1'
               else ('⏰ Midnight ordering cutoff is back ON — normal per-meal '
                     'deadlines apply.')
    })
