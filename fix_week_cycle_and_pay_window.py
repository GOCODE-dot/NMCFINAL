"""
fix_week_cycle_and_pay_window.py
=================================
Patch for app.py — Sunday-start week + 3-day ordering/payment window.

RULE
────
  • Week always starts on SUNDAY.
  • Students may order (and must pay) only in the first 3 days: Sun, Mon, Tue.
  • From Wednesday onwards, new orders are refused (ordering_locked_by_deadline).
  • The manager can still manually override with ordering_locked / unlock.

APPLY
─────
Make the two replacements below in app.py (search for the exact OLD strings).

══════════════════════════════════════════════════════════════════════════════
REPLACEMENT 1 — student_dashboard() week-dates calculation
══════════════════════════════════════════════════════════════════════════════

FIND (in student_dashboard()):
    week_dates  = [(today + timedelta(days=i)).isoformat() for i in range(7)]
    week_start  = today.isoformat()
    week_end    = (today + timedelta(days=6)).isoformat()

REPLACE WITH:
    # ── Fixed week: always starts on Sunday (weekday 6 in Python = Sunday)
    # Python weekday(): Mon=0 … Sun=6
    WEEK_START_DAY   = 6                          # 6 = Sunday
    days_since_start = (today.weekday() - WEEK_START_DAY) % 7
    week_start_date  = today - timedelta(days=days_since_start)
    week_end_date    = week_start_date + timedelta(days=6)   # Saturday

    week_start  = week_start_date.isoformat()
    week_end    = week_end_date.isoformat()
    week_dates  = [(week_start_date + timedelta(days=i)).isoformat() for i in range(7)]

    # ── 3-day payment window: Sun/Mon/Tue only (days 0-2 of the week)
    # ordering_locked_by_deadline = True on Wed–Sat
    days_into_week = days_since_start          # 0=Sun, 1=Mon, 2=Tue, 3=Wed …
    pay_window_open = days_into_week <= 2      # True on Sun/Mon/Tue


══════════════════════════════════════════════════════════════════════════════
REPLACEMENT 2 — student_order() / toggle_meal route: enforce pay window
══════════════════════════════════════════════════════════════════════════════

FIND this block near the top of your /student/order route (around the
max_date validation):

    max_date = (today_bd + timedelta(days=6)).isoformat()
    if d.get('meal_date', '') > max_date:
        conn.close()
        return jsonify({'ok': False, 'msg': '📅 You can only order meals up to 7 days ahead.'})

REPLACE WITH:

    # ── Fixed Sunday-start week
    WEEK_START_DAY   = 6                        # 6 = Sunday in Python
    days_since_start = (today_bd.weekday() - WEEK_START_DAY) % 7
    week_start_bd    = today_bd - timedelta(days=days_since_start)
    week_end_bd      = week_start_bd + timedelta(days=6)

    # Date must be within the current week
    if d.get('meal_date', '') > week_end_bd.isoformat():
        conn.close()
        return jsonify({'ok': False, 'msg': '📅 You can only order meals within the current week (Sun–Sat).'})

    # ── 3-day payment window: ordering only allowed on Sun / Mon / Tue
    days_into_week = days_since_start           # 0=Sun … 6=Sat
    if days_into_week > 2:                      # Wed(3) … Sat(6)
        conn.close()
        return jsonify({
            'ok':    False,
            'msg':   '🔒 Ordering window closed. You can only place new meal orders '
                     'on Sunday, Monday, and Tuesday. The window reopens next Sunday.'
        })


══════════════════════════════════════════════════════════════════════════════
REPLACEMENT 3 — Pass pay_window_open to student_dashboard template
══════════════════════════════════════════════════════════════════════════════

In your student_dashboard() view, find the render_template(...) call and
add pay_window_open to the kwargs:

    return render_template('student_dashboard.html',
        ...
        pay_window_open  = pay_window_open,    # ← ADD THIS
        days_into_week   = days_into_week,     # ← ADD THIS (optional, for debug)
        ...
    )


══════════════════════════════════════════════════════════════════════════════
HOW THE CYCLE WORKS
══════════════════════════════════════════════════════════════════════════════

  Python's .weekday():  Mon=0  Tue=1  Wed=2  Thu=3  Fri=4  Sat=5  Sun=6

  today.weekday() = 6 (Sunday)  → days_since_start = (6-6)%7 = 0  → week_start = today
  today.weekday() = 0 (Monday)  → days_since_start = (0-6)%7 = 1  → week_start = yesterday
  today.weekday() = 1 (Tuesday) → days_since_start = (1-6)%7 = 2  → week_start = 2 days ago
  today.weekday() = 2 (Wednesday)→days_since_start = (2-6)%7 = 3  → pay_window_open = False
  ...

  On Sunday the new week starts and pay_window_open flips back to True.


══════════════════════════════════════════════════════════════════════════════
OPTIONAL: Jinja2 template guard (belt-and-suspenders)
══════════════════════════════════════════════════════════════════════════════

In student_dashboard.html, inside the ORDER tab, you can add a server-side
banner for when the window is closed (in addition to the client-side JS one):

    {% if not pay_window_open and not ordering_locked %}
    <div style="display:flex;align-items:flex-start;gap:12px;background:#fff5f5;
                border:1.5px solid #feb2b2;border-radius:10px;padding:14px 18px;margin-bottom:16px">
      <span style="font-size:22px;flex-shrink:0">⏳</span>
      <div>
        <div style="font-weight:700;color:#e53e3e;font-size:14px;margin-bottom:3px">
          Ordering Window Closed (Wed–Sat)
        </div>
        <div style="font-size:13px;color:#c53030;line-height:1.5">
          New meal orders are accepted only on <strong>Sunday, Monday, and Tuesday</strong>.
          The ordering window reopens next Sunday.
        </div>
      </div>
    </div>
    {% endif %}

Place this just BEFORE the <div class="week-grid"> block.
"""
