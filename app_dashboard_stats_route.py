# ── ADD THIS ROUTE to app.py ──────────────────────────────────────────────────
# Used by the manager dashboard to refresh "Total Receivable" and
# "Total Received" stats without a full page reload.

@app.route('/manager/dashboard_stats')
@login_required('manager')
def manager_dashboard_stats():
    """Return live pending_amount and total_received for the stat cards."""
    mgr_id = session['manager_id']
    conn   = get_db()

    # pending = sum of all unpaid meal orders for this manager's students
    # (adjust the WHERE clause to match your existing logic)
    pending_row = conn.execute("""
        SELECT COALESCE(SUM(mo.amount), 0) as total
        FROM meal_orders mo
        JOIN students s ON s.id = mo.student_id
        WHERE s.floor_manager_id = ?          -- adjust to your FK column name
          AND mo.payment_status = 'pending'
    """, (mgr_id,)).fetchone()

    received_row = conn.execute("""
        SELECT COALESCE(SUM(p.amount), 0) as total
        FROM payments p
        JOIN students s ON s.id = p.student_id
        WHERE s.floor_manager_id = ?          -- adjust to your FK column name
          AND p.status = 'verified'
    """, (mgr_id,)).fetchone()

    conn.close()
    return jsonify({
        'ok':             True,
        'pending_amount': float(pending_row['total'] or 0),
        'total_received': float(received_row['total'] or 0),
    })
