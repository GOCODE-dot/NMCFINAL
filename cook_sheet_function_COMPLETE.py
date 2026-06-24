# ═══════════════════════════════════════════════════════════════════════════════
# COPY THIS ENTIRE FUNCTION AND REPLACE LINES 3914-3966 IN YOUR app.py
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/manager/cook_sheet')
@login_required('manager')
def manager_cook_sheet():
    """
    Return meal counts (lunch/dinner) broken down by:
    - Gender (male/female)
    - Male students by floor (1-7)
    - Female students by hostel (Campus, Sentu House, Chairman House)
    
    Example response:
    {
        "ok": true,
        "date": "2026-06-19",
        "lunch": {"total": 85, "female": 20, "male": 65},
        "dinner": {"total": 92, "female": 22, "male": 70},
        "floor_lunch": [
            {"floor": 1, "count": 12},
            {"floor": 2, "count": 14},
            ...
        ],
        "floor_dinner": [...],
        "hostel_lunch": [
            {"floor": 1, "name": "Campus", "count": 8},
            {"floor": 2, "name": "Sentu House", "count": 7},
            {"floor": 3, "name": "Chairman House", "count": 5}
        ],
        "hostel_dinner": [...]
    }
    """
    req_date = request.args.get('date', date.today().isoformat())
    try:
        datetime.fromisoformat(req_date)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Invalid date.'})
    
    conn = get_db()
    
    # ── Helper function: Count meals by gender ──────────────────────────────
    def gc(meal_type, gender):
        """Get count of meals for a specific meal_type and gender."""
        return queryOne(conn,
            "SELECT COUNT(*) as c FROM meal_orders mo "
            "JOIN students s ON s.id=mo.student_id "
            "WHERE mo.meal_date=%s AND mo.meal_type=%s AND s.gender=%s",
            (req_date, meal_type, gender)
        )['c']
    
    # ── Total meal counts ──────────────────────────────────────────────────
    lunch_total   = queryOne(conn, 
        "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='lunch'",  
        (req_date,)
    )['c']
    
    dinner_total  = queryOne(conn, 
        "SELECT COUNT(*) as c FROM meal_orders WHERE meal_date=%s AND meal_type='dinner'", 
        (req_date,)
    )['c']
    
    # ── Gender breakdown ──────────────────────────────────────────────────
    # MUST do this before closing connection
    lunch_female  = gc('lunch',  'female')
    lunch_male    = gc('lunch',  'male')
    dinner_female = gc('dinner', 'female')
    dinner_male   = gc('dinner', 'male')
    
    # ── MALE STUDENTS: Breakdown by floor ──────────────────────────────────
    # Males live in numbered floors (1-7)
    floor_lunch   = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch' AND s.gender='male' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    
    floor_dinner  = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='male' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    
    # ── FEMALE STUDENTS: Breakdown by hostel ──────────────────────────────
    # Females live in named hostels (Campus=1, Sentu House=2, Chairman House=3)
    hostel_lunch  = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='lunch' AND s.gender='female' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    
    hostel_dinner = query(conn,
        "SELECT s.floor, COUNT(*) as count FROM meal_orders mo "
        "JOIN students s ON s.id=mo.student_id "
        "WHERE mo.meal_date=%s AND mo.meal_type='dinner' AND s.gender='female' "
        "GROUP BY s.floor ORDER BY s.floor",
        (req_date,)
    )
    
    # ── Hostel name mapping ───────────────────────────────────────────────
    HOSTEL_NAMES = {
        1: 'Campus', 
        2: 'Sentu House', 
        3: 'Chairman House'
    }
    
    # ── Close connection (all queries done) ────────────────────────────────
    conn.close()
    
    # ── Build and return JSON response ────────────────────────────────────
    return jsonify({
        'ok': True, 
        'date': req_date,
        
        # Total lunch counts by gender
        'lunch': {
            'total': lunch_total,  
            'female': lunch_female,  
            'male': lunch_male
        },
        
        # Total dinner counts by gender
        'dinner': {
            'total': dinner_total, 
            'female': dinner_female, 
            'male': dinner_male
        },
        
        # Male students: Lunch by floor
        'floor_lunch': [
            {'floor': r['floor'], 'count': r['count']} 
            for r in floor_lunch
        ],
        
        # Male students: Dinner by floor
        'floor_dinner': [
            {'floor': r['floor'], 'count': r['count']} 
            for r in floor_dinner
        ],
        
        # Female students: Lunch by hostel (with names)
        'hostel_lunch': [
            {
                'floor': r['floor'], 
                'name': HOSTEL_NAMES.get(r['floor'], f"Hostel {r['floor']}"), 
                'count': r['count']
            } 
            for r in hostel_lunch
        ],
        
        # Female students: Dinner by hostel (with names)
        'hostel_dinner': [
            {
                'floor': r['floor'], 
                'name': HOSTEL_NAMES.get(r['floor'], f"Hostel {r['floor']}"), 
                'count': r['count']
            } 
            for r in hostel_dinner
        ],
    })
