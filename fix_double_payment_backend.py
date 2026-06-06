# ══════════════════════════════════════════════════════════════════════════════
#  app.py PATCH — Prevent double payments and duplicate TxnIDs
#  Apply these changes to your existing /student/submit_payment route
# ══════════════════════════════════════════════════════════════════════════════
#
#  TWO BACKEND FIXES:
#
#  FIX 1 — Reject if student already has a PENDING bKash payment
#           (guards against the student opening the modal twice or using
#           the API directly to bypass the JS guard)
#
#  FIX 2 — Reject if the submitted TxnID already exists in the database
#           (prevents re-use of the same Transaction ID, whether by accident
#           or because the student refreshed and re-submitted)
#
# ══════════════════════════════════════════════════════════════════════════════

# ── FIND your existing /student/submit_payment route and REPLACE it ──────────
# It will look something like:
#
#   @app.route('/student/submit_payment', methods=['POST'])
#   @login_required('student')
#   def submit_payment():
#       data    = request.json or {}
#       txn     = (data.get('bkash_txn') or '').strip().upper()
#       note    = (data.get('note') or '').strip()
#       ...
#
# REPLACE the entire route body with the version below.
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/student/submit_payment', methods=['POST'])
@login_required('student')
def submit_payment():
    data       = request.json or {}
    txn        = (data.get('bkash_txn') or '').strip().upper()
    note       = (data.get('note') or '').strip()
    student_id = session['student_id']

    # ── Basic validation ──────────────────────────────────────────────────────
    if not txn:
        return jsonify({'ok': False, 'msg': 'Transaction ID is required.'})
    if len(txn) < 6:
        return jsonify({'ok': False, 'msg': 'Transaction ID is too short. Please double-check.'})

    conn = get_db()

    # ── FIX 1: Block if student already has a pending bKash payment ───────────
    existing_bkash = conn.execute(
        """SELECT id, bkash_txn, amount FROM payments
           WHERE student_id = ? AND status = 'pending'
           ORDER BY submitted_at DESC LIMIT 1""",
        (student_id,)
    ).fetchone()

    if existing_bkash:
        conn.close()
        return jsonify({
            'ok':  False,
            'msg': (
                f'You already have a pending bKash payment (TxnID: {existing_bkash["bkash_txn"]}) '
                f'waiting for manager verification. '
                f'You cannot submit another payment until the current one is reviewed.'
            )
        })

    # ── FIX 2: Reject duplicate TxnID (anyone in the system, not just this student) ──
    # bKash TxnIDs are globally unique — the same ID cannot be used twice.
    duplicate_txn = conn.execute(
        "SELECT id, student_id FROM payments WHERE bkash_txn = ? LIMIT 1",
        (txn,)
    ).fetchone()

    if duplicate_txn:
        conn.close()
        return jsonify({
            'ok':  False,
            'msg': (
                f'Transaction ID "{txn}" has already been submitted. '
                f'Each bKash TxnID can only be used once. '
                f'Please check your bKash app for the correct ID.'
            )
        })

    # ── FIX 3: Block if student already has a pending CASH request ────────────
    # (Prevents bKash + cash both landing on the manager's desk simultaneously)
    existing_cash = conn.execute(
        """SELECT id FROM cash_payment_requests
           WHERE student_id = ? AND status = 'pending'
           LIMIT 1""",
        (student_id,)
    ).fetchone()

    if existing_cash:
        conn.close()
        return jsonify({
            'ok':  False,
            'msg': (
                'You have a pending cash payment request. '
                'Please cancel it (or wait for the manager to process it) '
                'before submitting a bKash payment.'
            )
        })

    # ── All clear — insert the payment ───────────────────────────────────────
    # (Keep the rest of your existing insert logic below this point, unchanged)
    #
    # Example (adjust column names to match YOUR schema):
    #
    #   amount = calculate_unpaid_amount(conn, student_id)
    #   conn.execute(
    #       """INSERT INTO payments (student_id, bkash_txn, amount, note, status, submitted_at)
    #          VALUES (?, ?, ?, ?, 'pending', datetime('now'))""",
    #       (student_id, txn, amount, note)
    #   )
    #   conn.commit()
    #   conn.close()
    #   return jsonify({'ok': True, 'msg': 'Payment submitted! Waiting for manager verification.'})

    conn.close()
    # ↑ Remove the two lines above and replace with your actual insert + commit + return


# ══════════════════════════════════════════════════════════════════════════════
#  ALSO ADD — Unique index on payments.bkash_txn (run once in DB setup)
# ══════════════════════════════════════════════════════════════════════════════
#
#  This makes the DB itself reject duplicate TxnIDs even if the app logic
#  somehow misses it (a hard safety net):
#
#  SQLite:
#    CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_bkash_txn
#    ON payments (bkash_txn);
#
#  MySQL / MariaDB:
#    ALTER TABLE payments
#    ADD UNIQUE INDEX uq_payments_bkash_txn (bkash_txn);
#
#  Add this to your database migration / init script.
# ══════════════════════════════════════════════════════════════════════════════
