# ══════════════════════════════════════════════════════════════════════════════
# FIX: Student dashboard — fixed weekly cycle
#
# BUG: week_start = today, so the 7-day window rolled forward every single day.
#      Students saw a new "week" each morning, making old days fall off the grid.
#
# FIX: Snap to a fixed weekly cycle. The week always starts on SATURDAY and
#      ends on FRIDAY (7 days). Change WEEK_START_DAY below if you prefer
#      a different day (0=Mon, 1=Tue, ..., 5=Sat, 6=Sun).
#
# HOW TO APPLY:
#   In app.py, inside the student_dashboard() function, REPLACE the three lines:
#
#       week_dates  = [(today + timedelta(days=i)).isoformat() for i in range(7)]
#       week_start  = today.isoformat()
#       week_end    = (today + timedelta(days=6)).isoformat()
#
#   with the block below (everything between the ── CUT HERE ── markers).
# ══════════════════════════════════════════════════════════════════════════════

# ── CUT HERE ──────────────────────────────────────────────────────────────────

    # Fixed 7-day cycle: week always starts on Saturday (weekday 5).
    # Change WEEK_START_DAY to 0 for Monday, 6 for Sunday, etc.
    WEEK_START_DAY = 5   # 5 = Saturday

    days_since_start = (today.weekday() - WEEK_START_DAY) % 7
    week_start_date  = today - timedelta(days=days_since_start)
    week_end_date    = week_start_date + timedelta(days=6)

    week_start  = week_start_date.isoformat()
    week_end    = week_end_date.isoformat()
    week_dates  = [(week_start_date + timedelta(days=i)).isoformat() for i in range(7)]

# ── CUT HERE ──────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# HOW THE CYCLE WORKS
# ══════════════════════════════════════════════════════════════════════════════
#
#  • If today is any day between Sat May 17 and Fri May 23 → all students see
#    the SAME grid: Sat 17 · Sun 18 · Mon 19 · Tue 20 · Wed 21 · Thu 22 · Fri 23
#
#  • On Saturday May 24 the grid flips automatically to the next 7 days:
#    Sat 24 · Sun 25 · … · Fri 30
#
#  • Students can order/cancel any day that hasn't passed yet (your existing
#    "can't order in the past" guard in /student/order is unchanged).
#
#  • The week_deadline variable (passed to the template) is week_end, so the
#    "ordering closes on …" message stays correct.
#
# ══════════════════════════════════════════════════════════════════════════════
# ALSO UPDATE /student/order guard (optional but recommended)
# ══════════════════════════════════════════════════════════════════════════════
#
# The toggle_meal route has:
#     max_date = (today_bd + timedelta(days=6)).isoformat()
#
# Change it to use the same fixed cycle so students can't order beyond week_end:
#
#     WEEK_START_DAY   = 5
#     days_since_start = (today_bd.weekday() - WEEK_START_DAY) % 7
#     week_start_bd    = today_bd - timedelta(days=days_since_start)
#     max_date         = (week_start_bd + timedelta(days=6)).isoformat()
#
# ══════════════════════════════════════════════════════════════════════════════
