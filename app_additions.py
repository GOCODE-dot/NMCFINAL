# ═══════════════════════════════════════════════════════════════════════════════
# PHONE / bKASH CHANGE REQUEST FEATURE
# Add these pieces to your app.py in the appropriate sections.
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. ADD TO init_db() inside executescript (after students table) ───────────
MIGRATION_SQL = """
    CREATE TABLE IF NOT EXISTS phone_change_requests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  INTEGER NOT NULL,
        old_bkash   TEXT NOT NULL,
        new_bkash   TEXT NOT NULL,
        reason      TEXT DEFAULT '',
        status      TEXT DEFAULT 'pending',   -- pending | approved | rejected
        decided_by  TEXT DEFAULT NULL,
        decided_at  TEXT DEFAULT NULL,
        created_at  TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(student_id) REFERENCES students(id)
    );
"""
# Paste MIGRATION_SQL into your c.executescript(...) block inside init_db().
# Also add this safe migration block just below init_db()'s executescript call:


def safe_migrate_phone_change_table(conn):
    """Call this inside init_db() after executescript, like the other migrations."""
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS phone_change_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  INTEGER NOT NULL,
            old_bkash   TEXT NOT NULL,
            new_bkash   TEXT NOT NULL,
            reason      TEXT DEFAULT '',
            status      TEXT DEFAULT 'pending',
            decided_by  TEXT DEFAULT NULL,
            decided_at  TEXT DEFAULT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(student_id) REFERENCES students(id)
        )''')
        conn.commit()
    except Exception:
        pass


# ── 2. STUDENT ROUTES ─────────────────────────────────────────────────────────

import re as _re
from flask import request, session, jsonify
from datetime import datetime as _dt

@app.route('/student/request_phone_change', methods=['POST'])
@login_required('student')
def student_request_phone_change():
    """Student submits a request to change their bKash/phone number."""
    d         = request.json or {}
    new_bkash = d.get('new_bkash', '').strip()
    password  = d.get('password', '').strip()
    reason    = d.get('reason', '').strip()[:300]

    if not new_bkash or not password:
        return jsonify({'ok': False, 'msg': 'New bKash number and password are required.'})
    if not _re.match(r'^01[3-9]\d{8}$', new_bkash):
        return jsonify({'ok': False, 'msg': 'Invalid bKash number. Must be 11 digits starting with 013–019.'})

    sid  = session['user_id']
    conn = get_db()

    # Verify password
    row = conn.execute("SELECT id, password, bkash_number FROM students WHERE id=?", (sid,)).fetchone()
    if not row or not verify_pass(row['password'], password):
        conn.close()
        return jsonify({'ok': False, 'msg': 'Incorrect password.'})

    if row['bkash_number'] == new_bkash:
        conn.close()
        return jsonify({'ok': False, 'msg': 'New number is the same as current bKash number.'})

    # Only one pending request at a time
    existing = conn.execute(
        "SELECT id FROM phone_change_requests WHERE student_id=? AND status='pending'", (sid,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending phone change request. Please wait for admin decision.'})

    conn.execute(
        "INSERT INTO phone_change_requests (student_id, old_bkash, new_bkash, reason) VALUES (?,?,?,?)",
        (sid, row['bkash_number'], new_bkash, reason)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': 'Request submitted! Admin will review and approve/reject it.'})


@app.route('/student/phone_change_status')
@login_required('student')
def student_phone_change_status():
    """Return all phone change requests for this student (last 10)."""
    sid  = session['user_id']
    conn = get_db()
    rows = conn.execute(
        """SELECT id, old_bkash, new_bkash, reason, status, decided_at, created_at
           FROM phone_change_requests WHERE student_id=? ORDER BY created_at DESC LIMIT 10""",
        (sid,)
    ).fetchall()
    conn.close()
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})


# ── 3. ADMIN ROUTES ───────────────────────────────────────────────────────────

@app.route('/admin/phone_change_requests')
@admin_required
def admin_phone_change_requests():
    """Admin sees all pending + recent phone change requests."""
    conn = get_db()
    rows = conn.execute(
        """SELECT pcr.*, s.name, s.roll_number, s.batch, s.floor
           FROM phone_change_requests pcr
           JOIN students s ON s.id = pcr.student_id
           ORDER BY CASE pcr.status WHEN 'pending' THEN 0 ELSE 1 END, pcr.created_at DESC
           LIMIT 100"""
    ).fetchall()
    conn.close()
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})


@app.route('/admin/decide_phone_change', methods=['POST'])
@admin_required
def admin_decide_phone_change():
    """Admin approves or rejects a phone change request."""
    d       = request.json or {}
    req_id  = d.get('request_id')
    verdict = d.get('verdict')  # 'approved' or 'rejected'

    if verdict not in ('approved', 'rejected'):
        return jsonify({'ok': False, 'msg': 'Invalid verdict.'})

    conn = get_db()
    req  = conn.execute(
        "SELECT * FROM phone_change_requests WHERE id=? AND status='pending'", (req_id,)
    ).fetchone()
    if not req:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request not found or already decided.'})

    now = _dt.now().isoformat(timespec='seconds')
    admin_id = session.get('admin_id', 'admin')

    if verdict == 'approved':
        # Apply the new bKash number to the student record
        conn.execute("UPDATE students SET bkash_number=? WHERE id=?", (req['new_bkash'], req['student_id']))
        conn.execute(
            "UPDATE phone_change_requests SET status='approved', decided_by=?, decided_at=? WHERE id=?",
            (admin_id, now, req_id)
        )
        # Log it
        conn.execute(
            "INSERT INTO admin_reset_log (admin_id, action) VALUES (?,?)",
            (admin_id, f"phone_change_approved: student_id={req['student_id']} {req['old_bkash']}→{req['new_bkash']}")
        )
        msg = f"Approved. Student bKash updated to {req['new_bkash']}."
    else:
        conn.execute(
            "UPDATE phone_change_requests SET status='rejected', decided_by=?, decided_at=? WHERE id=?",
            (admin_id, now, req_id)
        )
        msg = 'Request rejected.'

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': msg})
