# ═══════════════════════════════════════════════════════════════════════════════
# bKASH PAYMENT GATEWAY (PGW) — AUTOMATIC CHECKOUT
# Replaces the manual "enter your TxnID" flow with a real redirect-to-bKash-
# and-back checkout, using bKash's Tokenized Checkout API (v1.2.0-beta).
#
# Paste these pieces into app.py at the marked locations.
# ═══════════════════════════════════════════════════════════════════════════════
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  IMPORTANT — READ BEFORE WIRING THIS UP                                  │
# │                                                                           │
# │  This will NOT work until you have:                                     │
# │    1. A bKash Merchant/PGW account (sandbox credentials to start)        │
# │    2. A public HTTPS domain (bKash cannot redirect back to localhost)   │
# │                                                                           │
# │  Until then, set BKASH_MOCK_MODE=1 (see below) so you can test the      │
# │  ENTIRE flow — redirect out, "pay", redirect back, order confirmed —    │
# │  without any real bKash account. Flip it to 0 once you have real        │
# │  sandbox credentials and a deployed HTTPS URL, then flip to production  │
# │  base URL once bKash approves you for live.                             │
# └─────────────────────────────────────────────────────────────────────────┘


# ── 0. ENVIRONMENT VARIABLES (set these in your .env / hosting dashboard) ────
#
#   BKASH_MOCK_MODE      = "1"                     ← "0" once you have real creds
#   BKASH_BASE_URL       = "https://tokenized.sandbox.bka.sh/v1.2.0-beta"
#                          (production: https://tokenized.pay.bka.sh/v1.2.0-beta)
#   BKASH_APP_KEY        = "..."   ← from bKash merchant portal
#   BKASH_APP_SECRET     = "..."
#   BKASH_USERNAME       = "..."
#   BKASH_PASSWORD       = "..."
#   BKASH_CALLBACK_URL   = "https://yourdomain.com/student/bkash/callback"
#                          (must be your real deployed HTTPS domain)


# ── 1. DB MIGRATION — add inside init_db(), after your other safe_migrate_* calls ──

def safe_migrate_bkash_gateway(conn):
    """
    Tracks every bKash gateway checkout session so we never lose track of
    a payment mid-flow, and so refreshing/reopening the callback URL is safe.
    """
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS bkash_gateway_sessions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id        INTEGER NOT NULL,
            payment_id        TEXT NOT NULL,          -- bKash's paymentID
            invoice_number    TEXT NOT NULL,           -- our own unique reference
            amount            REAL NOT NULL,
            status            TEXT DEFAULT 'initiated', -- initiated|completed|failed|cancelled
            trx_id            TEXT DEFAULT NULL,       -- bKash's final transaction ID
            created_at        TEXT DEFAULT (datetime('now')),
            completed_at      TEXT DEFAULT NULL,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )""")
        conn.commit()
    except Exception:
        pass


# ── 2. CONFIG + TOKEN HELPERS — paste near your other config/imports ─────────

import os, uuid, requests
from datetime import datetime as _dt, timedelta as _td

BKASH_MOCK_MODE    = os.environ.get('BKASH_MOCK_MODE', '1') == '1'
BKASH_BASE_URL     = os.environ.get('BKASH_BASE_URL', 'https://tokenized.sandbox.bka.sh/v1.2.0-beta')
BKASH_APP_KEY      = os.environ.get('BKASH_APP_KEY', '')
BKASH_APP_SECRET   = os.environ.get('BKASH_APP_SECRET', '')
BKASH_USERNAME     = os.environ.get('BKASH_USERNAME', '')
BKASH_PASSWORD     = os.environ.get('BKASH_PASSWORD', '')
BKASH_CALLBACK_URL = os.environ.get('BKASH_CALLBACK_URL', 'http://localhost:5000/student/bkash/callback')

# Simple in-memory token cache (fine for a single-process app; move to DB/Redis
# if you run multiple workers, since each worker would otherwise grant its own token).
_bkash_token_cache = {'token': None, 'refresh_token': None, 'expires_at': None}


def _bkash_grant_token():
    """Get a fresh bKash access token (grant or refresh as needed)."""
    now = _dt.utcnow()

    if _bkash_token_cache['token'] and _bkash_token_cache['expires_at'] and now < _bkash_token_cache['expires_at']:
        return _bkash_token_cache['token']

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'username': BKASH_USERNAME,
        'password': BKASH_PASSWORD,
    }

    if _bkash_token_cache['refresh_token']:
        body = {
            'app_key': BKASH_APP_KEY,
            'app_secret': BKASH_APP_SECRET,
            'refresh_token': _bkash_token_cache['refresh_token'],
        }
        url = f'{BKASH_BASE_URL}/tokenized/checkout/token/refresh'
    else:
        body = {'app_key': BKASH_APP_KEY, 'app_secret': BKASH_APP_SECRET}
        url = f'{BKASH_BASE_URL}/tokenized/checkout/token/grant'

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    data = resp.json()

    if 'id_token' not in data:
        raise RuntimeError(f'bKash token grant failed: {data}')

    _bkash_token_cache['token']         = data['id_token']
    _bkash_token_cache['refresh_token'] = data.get('refresh_token')
    # bKash tokens last ~1 hour; refresh a bit early to be safe
    _bkash_token_cache['expires_at']    = now + _td(seconds=int(data.get('expires_in', 3300)) - 120)

    return _bkash_token_cache['token']


def _bkash_headers():
    return {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': _bkash_grant_token(),
        'X-APP-Key': BKASH_APP_KEY,
    }


def bkash_create_payment(amount, invoice_number):
    """Create a bKash checkout session. Returns dict with 'bkashURL' and 'paymentID'."""
    if BKASH_MOCK_MODE:
        # No real bKash call — hand back a URL that points at our own
        # simulated checkout page (see route #4 below).
        fake_payment_id = 'MOCK' + uuid.uuid4().hex[:16].upper()
        return {
            'paymentID': fake_payment_id,
            'bkashURL': f'/student/bkash/mock_checkout?payment_id={fake_payment_id}&invoice={invoice_number}&amount={amount}',
        }

    body = {
        'mode': '0011',
        'payerReference': invoice_number,
        'callbackURL': BKASH_CALLBACK_URL,
        'amount': f'{amount:.2f}',
        'currency': 'BDT',
        'intent': 'sale',
        'merchantInvoiceNumber': invoice_number,
    }
    resp = requests.post(f'{BKASH_BASE_URL}/tokenized/checkout/create',
                          json=body, headers=_bkash_headers(), timeout=15)
    data = resp.json()
    if 'bkashURL' not in data:
        raise RuntimeError(f'bKash create payment failed: {data}')
    return data


def bkash_execute_payment(payment_id):
    """Finalize the payment after the user completes it on bKash's page."""
    if BKASH_MOCK_MODE:
        return {
            'statusCode': '0000',
            'statusMessage': 'Successful',
            'trxID': 'MOCKTRX' + uuid.uuid4().hex[:10].upper(),
            'paymentID': payment_id,
        }

    body = {'paymentID': payment_id}
    resp = requests.post(f'{BKASH_BASE_URL}/tokenized/checkout/execute',
                          json=body, headers=_bkash_headers(), timeout=15)
    return resp.json()


def bkash_query_payment(payment_id):
    """Optional: re-check a payment's status directly with bKash (useful for
    reconciliation if a callback was ever missed)."""
    if BKASH_MOCK_MODE:
        return {'statusCode': '0000', 'statusMessage': 'Successful', 'paymentID': payment_id}

    body = {'paymentID': payment_id}
    resp = requests.post(f'{BKASH_BASE_URL}/tokenized/checkout/payment/status',
                          json=body, headers=_bkash_headers(), timeout=15)
    return resp.json()


# ── 3. STUDENT ROUTE — initiate checkout ──────────────────────────────────────

@app.route('/student/bkash/pay', methods=['POST'])
@login_required('student')
def student_bkash_pay():
    """Student taps 'Pay with bKash' — we compute the real due amount server-side
    (never trust a client-supplied amount), open a bKash checkout session, and
    hand back the URL to redirect the browser to."""
    sid  = session['user_id']
    conn = get_db()

    # ── Compute live due amount server-side ──────────────────────────────────
    due_row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) as total FROM meal_orders "
        "WHERE student_id=? AND payment_status IN ('pending','due')",
        (sid,)
    ).fetchone()
    amount = float(due_row['total'] or 0)

    if amount <= 0:
        conn.close()
        return jsonify({'ok': False, 'msg': 'You have no unpaid meals right now.'})

    # bKash requires a unique invoice/reference per payment attempt
    invoice_number = f'NMMS-{sid}-{int(_dt.utcnow().timestamp())}'

    try:
        result = bkash_create_payment(amount, invoice_number)
    except Exception as e:
        conn.close()
        return jsonify({'ok': False, 'msg': f'Could not start bKash checkout: {e}'})

    conn.execute(
        "INSERT INTO bkash_gateway_sessions (student_id, payment_id, invoice_number, amount, status) "
        "VALUES (?,?,?,?, 'initiated')",
        (sid, result['paymentID'], invoice_number, amount)
    )
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'bkashURL': result['bkashURL']})


# ── 4. CALLBACK ROUTE — bKash redirects the browser back here ────────────────

@app.route('/student/bkash/callback')
@login_required('student')
def student_bkash_callback():
    """
    bKash sends the browser back here with ?paymentID=...&status=success|failure|cancel
    We execute the payment, mark meal orders as paid, and send the student back
    to their dashboard with a clear result message.
    """
    payment_id  = request.args.get('paymentID', '')
    bkash_status = request.args.get('status', '')
    sid = session['user_id']
    conn = get_db()

    sess_row = conn.execute(
        "SELECT * FROM bkash_gateway_sessions WHERE payment_id=? AND student_id=?",
        (payment_id, sid)
    ).fetchone()

    if not sess_row:
        conn.close()
        flash('Payment session not found. If money was deducted, contact your Meal Manager.', 'error')
        return redirect(url_for('student_dashboard'))

    if sess_row['status'] == 'completed':
        # Already processed (e.g. user hit back/refresh on this URL) — don't double-count.
        conn.close()
        return redirect(url_for('student_dashboard', paid='1'))

    if bkash_status != 'success':
        conn.execute(
            "UPDATE bkash_gateway_sessions SET status=? WHERE payment_id=?",
            ('cancelled' if bkash_status == 'cancel' else 'failed', payment_id)
        )
        conn.commit()
        conn.close()
        flash('Payment was not completed. You can try again anytime.', 'error')
        return redirect(url_for('student_dashboard'))

    # ── Finalize with bKash ───────────────────────────────────────────────────
    try:
        exec_result = bkash_execute_payment(payment_id)
    except Exception as e:
        conn.close()
        flash(f'Could not confirm payment with bKash: {e}. If money was deducted, contact your Meal Manager.', 'error')
        return redirect(url_for('student_dashboard'))

    if exec_result.get('statusCode') != '0000':
        conn.execute(
            "UPDATE bkash_gateway_sessions SET status='failed' WHERE payment_id=?",
            (payment_id,)
        )
        conn.commit()
        conn.close()
        flash(exec_result.get('statusMessage', 'Payment could not be confirmed.'), 'error')
        return redirect(url_for('student_dashboard'))

    trx_id = exec_result.get('trxID', '')
    now    = _dt.utcnow().isoformat(timespec='seconds')
    amount = sess_row['amount']

    # Record the payment — bKash has already confirmed it in real time, so it
    # doesn't need manual manager verification like the old bkash_txn flow did.
    conn.execute(
        "INSERT INTO payments (student_id, bkash_txn, amount, status, verified_at, verified_by, created_at) "
        "VALUES (?,?,?, 'verified', ?, 'bkash_gateway', ?)",
        (sid, trx_id, amount, now, now)
    )

    # Mark unpaid meal orders as paid, oldest first, up to the paid amount
    unpaid = conn.execute(
        "SELECT id, amount FROM meal_orders WHERE student_id=? AND payment_status IN ('pending','due') "
        "ORDER BY meal_date ASC",
        (sid,)
    ).fetchall()
    remaining = amount
    for row in unpaid:
        if remaining <= 0:
            break
        conn.execute("UPDATE meal_orders SET payment_status='paid' WHERE id=?", (row['id'],))
        remaining -= row['amount']

    conn.execute(
        "UPDATE bkash_gateway_sessions SET status='completed', trx_id=?, completed_at=? WHERE payment_id=?",
        (trx_id, now, payment_id)
    )
    conn.commit()
    conn.close()

    flash(f'✅ Payment of ৳{amount:.0f} confirmed! TxnID: {trx_id}', 'success')
    return redirect(url_for('student_dashboard', paid='1'))


# ── 5. MOCK CHECKOUT PAGE — only reachable when BKASH_MOCK_MODE=1 ────────────
# Simulates bKash's hosted payment page so you can test the full round trip
# (leave the site, "pay", come back, order confirmed) with no real account.

@app.route('/student/bkash/mock_checkout')
@login_required('student')
def student_bkash_mock_checkout():
    if not BKASH_MOCK_MODE:
        return "Mock checkout is disabled (BKASH_MOCK_MODE=0).", 404

    payment_id = request.args.get('payment_id', '')
    invoice    = request.args.get('invoice', '')
    amount     = request.args.get('amount', '0')

    return f"""
    <html><head><title>bKash (Mock Checkout)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
      body {{ font-family: system-ui, sans-serif; background:#e51c6d; min-height:100vh;
              display:flex; align-items:center; justify-content:center; margin:0; }}
      .card {{ background:#fff; border-radius:16px; padding:32px; max-width:360px; width:90%;
                text-align:center; box-shadow:0 20px 60px rgba(0,0,0,.3); }}
      .logo {{ font-size:28px; font-weight:800; color:#e51c6d; margin-bottom:4px; }}
      .tag  {{ font-size:12px; color:#999; margin-bottom:20px; }}
      .amt  {{ font-size:32px; font-weight:800; color:#222; margin-bottom:4px; }}
      .inv  {{ font-size:12px; color:#888; margin-bottom:24px; }}
      button {{ width:100%; padding:14px; border-radius:10px; border:none; font-size:15px;
                font-weight:700; margin-bottom:10px; cursor:pointer; }}
      .pay {{ background:#e51c6d; color:#fff; }}
      .cancel {{ background:#f1f1f1; color:#555; }}
    </style></head>
    <body>
      <div class="card">
        <div class="logo">bKash</div>
        <div class="tag">⚠️ MOCK CHECKOUT — for local testing only, no real money moves</div>
        <div class="amt">৳{amount}</div>
        <div class="inv">Invoice: {invoice}</div>
        <button class="pay" onclick="location.href='/student/bkash/callback?paymentID={payment_id}&status=success'">
          Simulate Successful Payment
        </button>
        <button class="cancel" onclick="location.href='/student/bkash/callback?paymentID={payment_id}&status=cancel'">
          Simulate Cancel
        </button>
      </div>
    </body></html>
    """
