# ═══════════════════════════════════════════════════════════════════════════════
# OLD-DEBT BLOCK FEATURE  +  COOK SHEET FIX
# Paste these snippets into your app.py at the marked locations.
# ═══════════════════════════════════════════════════════════════════════════════


# ── 1. DB MIGRATION ─────────────────────────────────────────────────────────
# Add this call inside init_db(), just after your other safe_migrate_* calls:

def safe_migrate_debt_block(conn):
    """
    Adds a 'debt_blocked' column to students.
    When True the student sees a 'Pay old bill first' banner and cannot
    place NEW meal orders (already-ordered meals are unaffected).
    Call this inside init_db() after executescript.
    """
    try:
        conn.execute("ALTER TABLE students ADD COLUMN debt_blocked INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass          # column already exists — safe to ignore


# ── 2. HELPER ───────────────────────────────────────────────────────────────
def is_debt_blocked(student_id, conn=None):
    """Return True if the student is currently blocked due to old unpaid debt."""
    close = False
    if conn is None:
        conn  = get_db()
        close = True
    try:
        row = conn.execute(
            "SELECT debt_blocked FROM students WHERE id=?", (student_id,)
        ).fetchone()
        return bool(row and row['debt_blocked'])
    finally:
        if close:
            conn.close()


# ── 3. PATCH student_dashboard ROUTE ────────────────────────────────────────
# In your existing student_dashboard() view, pass debt_blocked to the template:
#
#   student = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
#   ...
#   debt_blocked = bool(student['debt_blocked'])
#   return render_template('student_dashboard.html',
#       ...
#       debt_blocked=debt_blocked,        # ← ADD THIS
#       ...
#   )


# ── 4. PATCH toggleMeal / order route ───────────────────────────────────────
# At the TOP of your /student/toggle_meal (or however you call it) route,
# add this guard so a blocked student cannot place new orders:

@app.route('/student/toggle_meal', methods=['POST'])
@login_required('student')
def student_toggle_meal():
    sid  = session['user_id']
    conn = get_db()

    # ── DEBT BLOCK GUARD ────────────────────────────────────────────────────
    student = conn.execute(
        "SELECT debt_blocked FROM students WHERE id=?", (sid,)
    ).fetchone()
    if student and student['debt_blocked']:
        conn.close()
        return jsonify({
            'ok':  False,
            'msg': '🚫 Your meal ordering is blocked because you have an unpaid bill '
                   'from a previous period. Please clear your old balance first, then '
                   'ask the Meal Manager to unblock you.'
        })
    # ── REST OF YOUR EXISTING TOGGLE_MEAL LOGIC ────────────────────────────
    # ... (keep everything else exactly as it was)
    conn.close()
    return jsonify({'ok': True})    # placeholder — keep your real logic


# ── 5. MANAGER ROUTES — block / unblock ─────────────────────────────────────

@app.route('/manager/set_debt_block', methods=['POST'])
@login_required('manager')
def manager_set_debt_block():
    """
    Block or unblock a student's meal ordering due to unpaid old debt.
    Body JSON: { student_id: int, blocked: bool }
    """
    d          = request.json or {}
    student_id = d.get('student_id')
    blocked    = bool(d.get('blocked'))

    if not student_id:
        return jsonify({'ok': False, 'msg': 'student_id required'})

    conn = get_db()
    conn.execute(
        "UPDATE students SET debt_blocked=? WHERE id=?",
        (1 if blocked else 0, student_id)
    )
    # Log it
    mgr_id = session.get('manager_id', 'manager')
    action = f"debt_block_{'ON' if blocked else 'OFF'}: student_id={student_id}"
    try:
        conn.execute(
            "INSERT INTO manager_history (manager_id, action) VALUES (?,?)",
            (mgr_id, action)
        )
    except Exception:
        pass
    conn.commit()
    conn.close()

    verb = 'blocked' if blocked else 'unblocked'
    return jsonify({'ok': True, 'msg': f'Student meal ordering {verb}.', 'blocked': blocked})


@app.route('/manager/debt_blocked_students')
@login_required('manager')
def manager_debt_blocked_students():
    """Return all currently debt-blocked students (for manager's panel)."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, name, roll_number, batch, floor, bkash_number
           FROM students WHERE debt_blocked=1 ORDER BY batch, roll_number"""
    ).fetchall()
    conn.close()
    return jsonify({'ok': True, 'students': [dict(r) for r in rows]})


# ── 6. COOK SHEET ROUTE FIX ─────────────────────────────────────────────────
# If your /manager/cook_sheet route is missing or broken, here is a clean
# version that matches exactly what the dashboard JS expects:

@app.route('/manager/cook_sheet')
@login_required('manager')
def manager_cook_sheet():
    """
    Returns meal counts for a given date, broken down by meal type,
    gender, and floor (for male students).

    Query param: ?date=YYYY-MM-DD  (defaults to today)
    """
    from datetime import date as _date

    date_str = request.args.get('date', str(_date.today()))

    # Basic validation
    try:
        _date.fromisoformat(date_str)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Invalid date format. Use YYYY-MM-DD.'})

    conn = get_db()

    def meal_counts(meal_type):
        rows = conn.execute(
            """SELECT s.gender, s.floor, COUNT(*) as cnt
               FROM meal_orders mo
               JOIN students s ON s.id = mo.student_id
               WHERE mo.date=? AND mo.meal_type=?
                 AND mo.payment_status != 'cancelled'
               GROUP BY s.gender, s.floor""",
            (date_str, meal_type)
        ).fetchall()

        total  = 0
        female = 0
        male   = 0
        floors = {}  # floor -> count

        for r in rows:
            cnt = r['cnt']
            total += cnt
            if r['gender'] == 'female':
                female += cnt
            else:
                male += cnt
                fl = r['floor']
                if fl:
                    floors[fl] = floors.get(fl, 0) + cnt

        floor_list = sorted(
            [{'floor': fl, 'count': c} for fl, c in floors.items()],
            key=lambda x: x['floor']
        )
        return {'total': total, 'female': female, 'male': male}, floor_list

    lunch,  floor_lunch  = meal_counts('lunch')
    dinner, floor_dinner = meal_counts('dinner')

    conn.close()
    return jsonify({
        'ok':          True,
        'date':        date_str,
        'lunch':       lunch,
        'dinner':      dinner,
        'floor_lunch':  floor_lunch,
        'floor_dinner': floor_dinner,
    })
