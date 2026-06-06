# ══════════════════════════════════════════════════════════════════════════════
# FIX: manager_cook_sheet route in app.py
#
# BUG: gc() (the gender-count helper) was called AFTER conn.close(), so every
#      request crashed with "connection already closed" and the cook sheet
#      never loaded.
#
# HOW TO APPLY:
#   In app.py find the @app.route('/manager/cook_sheet') block and replace
#   the entire function with this version.
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/manager/cook_sheet')
@login_required('manager')
def manager_cook_sheet():
    req_date = request.args.get('date', date.today().isoformat())
    try:
        datetime.fromisoformat(req_date)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Invalid date.'})

    conn = get_db()

    # ── Helper: gender count (must be called BEFORE conn.close()) ────────────
    def gc(meal_type, gender):
        return queryOne(conn,
            "SELECT COUNT(*) as c FROM meal_orders mo "
            "JOIN students s ON s.id=mo.student_id "
            "WHERE mo.meal_date=%s AND mo.meal_type=%s AND s.gender=%s",
            (req_date, meal_type, gender)
        )['c']

    lunch_total  = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",
        (req_date,))['c']
    dinner_total = queryOne(conn,
        "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'",
        (req_date,))['c']

    # Gender counts — BEFORE close
    lunch_female  = gc('lunch',  'female')
    lunch_male    = gc('lunch',  'male')
    dinner_female = gc('dinner', 'female')
    dinner_male   = gc('dinner', 'male')

    floor_lunch  = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch' AND s.gender='male' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    floor_dinner = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='male' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )

    conn.close()   # ← safe to close NOW, after all queries are done

    return jsonify({
        'ok':   True,
        'date': req_date,
        'lunch':  {'total': lunch_total,  'female': lunch_female,  'male': lunch_male},
        'dinner': {'total': dinner_total, 'female': dinner_female, 'male': dinner_male},
        'floor_lunch':  [{'floor': r['floor'], 'count': r['count']} for r in floor_lunch],
        'floor_dinner': [{'floor': r['floor'], 'count': r['count']} for r in floor_dinner],
    })
