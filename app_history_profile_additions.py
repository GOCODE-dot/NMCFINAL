# ═══════════════════════════════════════════════════════════════════════════════
# STUDENT MEAL/PAYMENT HISTORY  +  MANAGER STUDENT PROFILE
# Paste these two routes into app.py. No DB migration needed — both routes
# only SELECT from tables that already exist (students, meal_orders, payments,
# cash_payment_requests, and phone_change_requests if you've applied
# app_additions.py).
#
# Suggested location: right after /student/phone_change_status (student route)
# and right after /manager/students (manager route) — but anywhere in the
# file works since they're standalone.
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import date, datetime, timedelta   # already imported in app.py — safe to skip if duplicate


# ── 1. STUDENT — last 30 days of their own history ─────────────────────────────

@app.route('/student/meal_history')
@login_required('student')
def student_meal_history():
    """Return the logged-in student's meal orders + payments + cash requests
    for the last 30 days (used by the 'Meal History' tab on the dashboard)."""
    sid   = session['user_id']
    conn  = get_db()
    today = (datetime.utcnow() + timedelta(hours=6)).date()
    since = (today - timedelta(days=30)).isoformat()

    orders = query(conn,
        "SELECT meal_date, meal_type, payment_status, amount, ordered_at "
        "FROM meal_orders WHERE student_id=%s AND meal_date>=%s "
        "ORDER BY meal_date DESC, meal_type",
        (sid, since)
    )

    payments = query(conn,
        "SELECT amount, bkash_txn, payment_date, status, created_at, verified_at "
        "FROM payments WHERE student_id=%s AND created_at>=%s "
        "ORDER BY created_at DESC",
        (sid, since)
    )

    cash_requests = query(conn,
        "SELECT amount, note, status, requested_at, reviewed_at "
        "FROM cash_payment_requests WHERE student_id=%s AND requested_at>=%s "
        "ORDER BY requested_at DESC",
        (sid, since)
    )

    orders_list = [dict(o) for o in orders]
    total_meals  = len(orders_list)
    total_amount = sum(o['amount'] or 0 for o in orders_list)
    paid_amount  = sum(o['amount'] or 0 for o in orders_list if o['payment_status'] == 'paid')
    due_amount   = sum(o['amount'] or 0 for o in orders_list if o['payment_status'] in ('pending', 'due'))

    conn.close()
    return jsonify({
        'ok':    True,
        'since': since,
        'until': today.isoformat(),
        'orders':        orders_list,
        'payments':      [dict(p) for p in payments],
        'cash_requests': [dict(c) for c in cash_requests],
        'summary': {
            'total_meals':  total_meals,
            'total_amount': total_amount,
            'paid_amount':  paid_amount,
            'due_amount':   due_amount,
        }
    })


# ── 2. MANAGER — full profile / complete history for one student ───────────────

@app.route('/manager/student_profile/<int:student_id>')
@login_required('manager')
def manager_student_profile(student_id):
    """Return everything about one student: profile info + FULL meal order
    history, payment history, cash request history, and phone-change history
    (all-time, not just 30 days). Used by the profile modal on the
    'All Students' page — click a student row to open it."""
    conn = get_db()

    student = queryOne(conn, "SELECT * FROM students WHERE id=%s", (student_id,))
    if not student:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Student not found.'})

    orders = query(conn,
        "SELECT meal_date, meal_type, payment_status, amount, ordered_at "
        "FROM meal_orders WHERE student_id=%s "
        "ORDER BY meal_date DESC, meal_type",
        (student_id,)
    )

    payments = query(conn,
        "SELECT amount, bkash_txn, payment_date, status, manager_bkash, "
        "       verified_at, verified_by, created_at "
        "FROM payments WHERE student_id=%s ORDER BY created_at DESC",
        (student_id,)
    )

    cash_requests = query(conn,
        "SELECT amount, note, status, requested_at, reviewed_at, reviewed_by "
        "FROM cash_payment_requests WHERE student_id=%s ORDER BY requested_at DESC",
        (student_id,)
    )

    # phone_change_requests only exists if app_additions.py has been applied —
    # guard against a missing table so this route never 500s on an older DB.
    try:
        phone_requests = query(conn,
            "SELECT old_bkash, new_bkash, reason, status, created_at, decided_at "
            "FROM phone_change_requests WHERE student_id=%s ORDER BY created_at DESC",
            (student_id,)
        )
        phone_requests = [dict(p) for p in phone_requests]
    except Exception:
        phone_requests = []

    orders_list  = [dict(o) for o in orders]
    total_meals  = len(orders_list)
    total_amount = sum(o['amount'] or 0 for o in orders_list)
    paid_amount  = sum(o['amount'] or 0 for o in orders_list if o['payment_status'] == 'paid')
    due_amount   = sum(o['amount'] or 0 for o in orders_list if o['payment_status'] in ('pending', 'due'))

    conn.close()
    return jsonify({
        'ok':      True,
        'student': dict(student),
        'orders':        orders_list,
        'payments':      [dict(p) for p in payments],
        'cash_requests': [dict(c) for c in cash_requests],
        'phone_requests': phone_requests,
        'summary': {
            'total_meals':  total_meals,
            'total_amount': total_amount,
            'paid_amount':  paid_amount,
            'due_amount':   due_amount,
        }
    })
