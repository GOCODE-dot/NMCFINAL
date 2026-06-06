# ══════════════════════════════════════════════════════════════════════════════
# FIX 1: Meal toggle intermittent failure
# FIX 2: Due banner doesn't clear after student submits bKash payment
#
# HOW TO APPLY:
#   Make the replacements described in each section below.
#   Sections marked [app.py] edit app.py.
#   Sections marked [student_dashboard.html] edit student_dashboard.html.
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# [app.py]  FIX 1A — Fixed weekly cycle in student_dashboard()
#
# ROOT CAUSE: week_start = today, so the 7-day grid shifts forward every day.
# A student who loaded the page yesterday sees stale dates — clicking a meal
# for "yesterday" is now a past date on the server → returns ok:False silently
# (ON CONFLICT DO NOTHING means no error, but no insert either and the
# toggle still returns ok:True from the try block). This makes ordering
# appear to succeed but nothing is saved.
#
# FIND this block (inside student_dashboard(), around line 676):
#
#     week_dates  = [(today + timedelta(days=i)).isoformat() for i in range(7)]
#     week_start  = today.isoformat()
#     week_end    = (today + timedelta(days=6)).isoformat()
#
# REPLACE WITH:
#
#     # Fixed weekly cycle — always anchored to Saturday.
#     # The grid shows the same 7 days for everyone until the week flips.
#     WEEK_START_DAY   = 5   # 5=Saturday. Change to 0 for Monday, 6 for Sunday.
#     days_since_start = (today.weekday() - WEEK_START_DAY) % 7
#     week_start_date  = today - timedelta(days=days_since_start)
#     week_end_date    = week_start_date + timedelta(days=6)
#     week_start  = week_start_date.isoformat()
#     week_end    = week_end_date.isoformat()
#     week_dates  = [(week_start_date + timedelta(days=i)).isoformat() for i in range(7)]
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# [app.py]  FIX 1B — Fixed max_date in /student/order route
#
# The toggle_meal route uses its own 7-day window that also rolls daily.
# It must use the same fixed cycle so the server and client agree on valid dates.
#
# FIND this block (inside student_order(), around line 763):
#
#     max_date = (today_bd + timedelta(days=6)).isoformat()
#     if d.get('meal_date', '') > max_date:
#         conn.close()
#         return jsonify({'ok': False, 'msg': '📅 You can only order meals up to 7 days ahead.'})
#
# REPLACE WITH:
#
#     WEEK_START_DAY   = 5   # must match the value in student_dashboard()
#     days_since_start = (today_bd.weekday() - WEEK_START_DAY) % 7
#     week_start_bd    = today_bd - timedelta(days=days_since_start)
#     week_end_bd      = week_start_bd + timedelta(days=6)
#     if d.get('meal_date', '') > week_end_bd.isoformat():
#         conn.close()
#         return jsonify({'ok': False, 'msg': '📅 You can only order meals within the current week.'})
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# [app.py]  FIX 2A — Pass has_pending_payment flag to student_dashboard template
#
# ROOT CAUSE: After a student submits a bKash payment, the meal_orders rows
# stay as payment_status='pending' until the manager verifies. So due > 0
# stays true and the red banner keeps showing, even though the student already
# paid. We need to detect the pending_verification payment and show a
# "payment submitted, awaiting verification" banner instead.
#
# FIND the student_dashboard() section that queries pending_due (around line 706):
#
#     pending_due = queryOne(conn,
#         "SELECT SUM(amount) as total FROM meal_orders WHERE student_id=%s AND payment_status IN ('pending','due')", (sid,)
#     )
#     due_only = queryOne(conn, ...
#     ...
#     student_row     = queryOne(conn, "SELECT * FROM students WHERE id=%s", (sid,))
#     ordering_locked = bool(student_row and student_row['ordering_locked'])
#     student_bkash   = student_row['bkash_number'] if student_row else ''
#
#     conn.close()
#     return render_template('student_dashboard.html',
#         ...
#         meal_locked     = overdue_old > 0,
#         ...
#     )
#
# ADD these two queries right before conn.close():
#
#     pending_payment_row = queryOne(conn,
#         "SELECT bkash_txn, amount, created_at FROM payments WHERE student_id=%s AND status='pending_verification' ORDER BY created_at DESC LIMIT 1",
#         (sid,)
#     )
#     has_pending_payment = pending_payment_row is not None
#     pending_payment_txn = pending_payment_row['bkash_txn'] if pending_payment_row else ''
#
# Then ADD these two variables to the render_template(...) call:
#
#         has_pending_payment = has_pending_payment,
#         pending_payment_txn = pending_payment_txn,
# ══════════════════════════════════════════════════════════════════════════════
