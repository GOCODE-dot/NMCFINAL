"""
══════════════════════════════════════════════════════════════════════
  APP.PY PATCH — Manager: Unlock Ordering Requests
══════════════════════════════════════════════════════════════════════

  WHAT THIS ADDS:
  ─────────────────
  1. A new DB table  `ordering_unlock_requests`  so students can submit
     a one-tap "Please unlock my ordering" request from their dashboard.
  2. GET  /manager/ordering_unlock_requests   → list pending requests
  3. POST /manager/decide_ordering_unlock     → approve (sets ordering_locked=0)
                                                or reject
  4. POST /student/request_ordering_unlock    → student submits request
     (no duplicate request allowed while one is already pending)

  HOW TO INSTALL:
  ─────────────────
  Paste each block into app.py at the indicated locations.

══════════════════════════════════════════════════════════════════════
"""

# ══════════════════════════════════════════════════════════════════════
# BLOCK 1 — Add inside init_db() BEFORE the conn.commit() at the end
#           of the table-creation section (around line 382 in your file).
# ══════════════════════════════════════════════════════════════════════

BLOCK_1_MIGRATION = """
    cur.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS ordering_unlock_requests (
            id          SERIAL PRIMARY KEY,
            student_id  INTEGER NOT NULL,
            reason      TEXT DEFAULT '',
            status      TEXT DEFAULT 'pending',
            decided_by  TEXT DEFAULT NULL,
            decided_at  TEXT DEFAULT NULL,
            created_at  TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
        )
    \"\"\")
"""

# Also add this safe-migration block AFTER the existing ALTER TABLE blocks
# (around line 389 of app.py) to guard against re-running on an existing DB:
BLOCK_1_SAFE_MIGRATION = """
    try:
        cur.execute("ALTER TABLE ordering_unlock_requests ADD COLUMN IF NOT EXISTS reason TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        conn.rollback()
"""


# ══════════════════════════════════════════════════════════════════════
# BLOCK 2 — STUDENT ROUTE: submit an unlock request
#           Paste anywhere in the student-routes section of app.py
#           (e.g. after the /student/cancel_order route ~line 872).
# ══════════════════════════════════════════════════════════════════════

BLOCK_2_STUDENT_ROUTE = """
@app.route('/student/request_ordering_unlock', methods=['POST'])
@login_required('student')
def student_request_ordering_unlock():
    \"\"\"Student asks manager to unlock their meal ordering.\"\"\"
    sid    = session['user_id']
    reason = (request.json or {}).get('reason', '').strip()[:200]
    conn   = get_db()
    # Check if already unlocked
    row = queryOne(conn, "SELECT ordering_locked FROM students WHERE id=%s", (sid,))
    if row and not row['ordering_locked']:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Your ordering is not locked — no request needed.'})
    # Prevent duplicate pending requests
    existing = queryOne(conn,
        "SELECT id FROM ordering_unlock_requests WHERE student_id=%s AND status='pending'",
        (sid,)
    )
    if existing:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You already have a pending unlock request. Please wait for the manager to review it.'})
    execute(conn,
        "INSERT INTO ordering_unlock_requests (student_id, reason) VALUES (%s, %s)",
        (sid, reason)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': '✅ Unlock request sent to the Meal Manager. You will be notified when it is reviewed.'})
"""


# ══════════════════════════════════════════════════════════════════════
# BLOCK 3 — MANAGER ROUTES: list + decide ordering unlock requests
#           Paste anywhere in the manager-routes section of app.py
#           (e.g. after /manager/unlock_ordering ~line 2640).
# ══════════════════════════════════════════════════════════════════════

BLOCK_3_MANAGER_ROUTES = """
@app.route('/manager/ordering_unlock_requests')
@login_required('manager')
def manager_ordering_unlock_requests():
    \"\"\"Return all pending ordering-unlock requests with student info.\"\"\"
    conn = get_db()
    rows = query(conn, \"\"\"
        SELECT ur.id, ur.student_id, ur.reason, ur.status, ur.created_at,
               s.name, s.roll_number, s.batch, s.floor, s.gender,
               s.ordering_locked
        FROM ordering_unlock_requests ur
        JOIN students s ON s.id = ur.student_id
        WHERE ur.status = 'pending'
        ORDER BY ur.created_at DESC
    \"\"\")
    conn.close()
    return jsonify({'ok': True, 'requests': [dict(r) for r in rows]})


@app.route('/manager/decide_ordering_unlock', methods=['POST'])
@login_required('manager')
def manager_decide_ordering_unlock():
    \"\"\"Approve or reject a student ordering-unlock request.\"\"\"
    d       = request.json or {}
    req_id  = d.get('request_id')
    verdict = d.get('verdict')   # 'approved' or 'rejected'
    if not req_id or verdict not in ('approved', 'rejected'):
        return jsonify({'ok': False, 'msg': 'Invalid request.'})

    conn    = get_db()
    mgr_id  = session.get('user_id')
    req_row = queryOne(conn,
        "SELECT id, student_id, status FROM ordering_unlock_requests WHERE id=%s",
        (req_id,)
    )
    if not req_row:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request not found.'})
    if req_row['status'] != 'pending':
        conn.close()
        return jsonify({'ok': False, 'msg': 'Request already decided.'})

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    execute(conn,
        "UPDATE ordering_unlock_requests SET status=%s, decided_by=%s, decided_at=%s WHERE id=%s",
        (verdict, str(mgr_id), now_str, req_id)
    )
    if verdict == 'approved':
        execute(conn,
            "UPDATE students SET ordering_locked=0 WHERE id=%s",
            (req_row['student_id'],)
        )
    conn.commit()
    conn.close()
    verb = 'unlocked' if verdict == 'approved' else 'kept locked (request rejected)'
    return jsonify({'ok': True, 'msg': f'Student ordering {verb}.'})
"""
