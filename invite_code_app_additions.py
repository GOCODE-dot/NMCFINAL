"""
invite_code_app_additions.py
════════════════════════════
Invite-only registration system.

THREE places to edit in your app.py — marked ① ② ③
"""

# ─────────────────────────────────────────────────────────────────────────────
# ① INSIDE init_db() → inside the c.executescript('''  ''') block
#    Add this CREATE TABLE right before the closing  '''  )
# ─────────────────────────────────────────────────────────────────────────────

INIT_DB_ADDITION = """
        CREATE TABLE IF NOT EXISTS registration_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            batch       TEXT,           -- NULL means valid for any batch
            created_by  TEXT NOT NULL,
            note        TEXT DEFAULT '',
            is_used     INTEGER DEFAULT 0,
            used_by     TEXT,           -- roll_number of student who used it
            used_at     TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
"""

# ─────────────────────────────────────────────────────────────────────────────
# ② REPLACE the existing student_register route entirely with this version
#    (adds invite code validation before inserting the student)
# ─────────────────────────────────────────────────────────────────────────────

STUDENT_REGISTER_ROUTE = """
@app.route('/student/register', methods=['GET', 'POST'])
def student_register():
    if request.method == 'POST':
        d    = request.form
        conn = get_db()
        try:
            # ── Invite code validation ─────────────────────────────────────
            invite_code = d.get('invite_code', '').strip().upper()
            if not invite_code:
                flash('An invite code is required to register.', 'error')
                return render_template('student_register.html')

            code_row = conn.execute(
                \"SELECT * FROM registration_codes WHERE code=? AND is_used=0\",
                (invite_code,)
            ).fetchone()

            if not code_row:
                flash('Invalid or already used invite code. Please contact the admin.', 'error')
                return render_template('student_register.html')

            # ── Name validation ────────────────────────────────────────────
            name = d.get('name', '').strip()[:150]
            if not name:
                flash('Full name is required.', 'error')
                return render_template('student_register.html')

            # ── bKash validation ───────────────────────────────────────────
            bkash = d.get('bkash_number', '').strip()
            if not re.match(r'^01[3-9]\\d{8}$', bkash):
                flash('Invalid bKash number. Must be 11 digits starting with 013–019.', 'error')
                return render_template('student_register.html')

            # ── Password validation ────────────────────────────────────────
            password = d.get('password', '')
            if len(password) < 6:
                flash('Password must be at least 6 characters.', 'error')
                return render_template('student_register.html')

            gender    = d.get('gender', 'male')
            floor_val = None if gender == 'female' else int(d.get('floor') or 1)

            # ── Batch validation ───────────────────────────────────────────
            try:
                batch_num = int(d['batch'])
            except (ValueError, KeyError):
                flash('Please select a valid batch.', 'error')
                return render_template('student_register.html')

            current_year = datetime.now().year
            max_batch = min(8 + max(0, current_year - 2026), 20)

            if batch_num < 2 or batch_num > max_batch:
                if batch_num > max_batch:
                    flash(f'Batch {batch_num} is not yet available.', 'error')
                else:
                    flash('Batch number must be 2 or higher.', 'error')
                return render_template('student_register.html')

            # ── If code is batch-locked, enforce it ────────────────────────
            if code_row['batch'] and str(code_row['batch']) != str(batch_num):
                flash(
                    f'This invite code is only valid for Batch {code_row[\"batch\"]}. '
                    f'You selected Batch {batch_num}.',
                    'error'
                )
                return render_template('student_register.html')

            # ── Roll number validation (1–75) ──────────────────────────────
            try:
                roll_raw = int(d['roll_number'])
            except (ValueError, KeyError):
                flash('Roll number must be a number between 1 and 75.', 'error')
                return render_template('student_register.html')

            if roll_raw < 1 or roll_raw > 75:
                flash('Roll number must be between 1 and 75.', 'error')
                return render_template('student_register.html')

            roll_number = f'{batch_num}-{roll_raw}'

            # ── Insert student ─────────────────────────────────────────────
            conn.execute(
                \"INSERT INTO students (name,batch,roll_number,bkash_number,password,gender,floor) VALUES (?,?,?,?,?,?,?)\",
                (name, str(batch_num), roll_number, bkash,
                 hash_pass(password), gender, floor_val)
            )

            # ── Mark invite code as used ───────────────────────────────────
            conn.execute(
                \"UPDATE registration_codes SET is_used=1, used_by=?, used_at=datetime('now') WHERE code=?\",
                (roll_number, invite_code)
            )

            conn.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('student_login'))

        except sqlite3.IntegrityError:
            flash('This roll number for this batch is already registered.', 'error')
        finally:
            conn.close()
    return render_template('student_register.html')
"""


# ─────────────────────────────────────────────────────────────────────────────
# ③ ADD these new admin routes BEFORE the # ── STARTUP ─── section
# ─────────────────────────────────────────────────────────────────────────────

ADMIN_INVITE_ROUTES = """
import string as _string   # already available; just for clarity

def _gen_invite_code(length=10):
    \"\"\"Generate a random uppercase alphanumeric invite code.\"\"\"
    alphabet = _string.ascii_uppercase + _string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@app.route('/admin/generate_invite', methods=['POST'])
@admin_required
def admin_generate_invite():
    \"\"\"
    Generate one or more invite codes.
    Body: { count: 1-50, batch: '5' or null, note: '' }
    \"\"\"
    data  = request.json or {}
    count = min(int(data.get('count', 1)), 50)   # max 50 at once
    batch = (data.get('batch') or '').strip() or None
    note  = (data.get('note')  or '').strip()[:100]

    conn  = get_db()
    codes = []
    for _ in range(count):
        # Regenerate on collision (extremely rare)
        for attempt in range(10):
            code = _gen_invite_code(10)
            try:
                conn.execute(
                    \"INSERT INTO registration_codes (code, batch, created_by, note) VALUES (?,?,?,?)\",
                    (code, batch, session['admin_id'], note)
                )
                codes.append(code)
                break
            except sqlite3.IntegrityError:
                continue   # collision — try again

    conn.execute(
        \"INSERT INTO admin_reset_log (admin_id, action) VALUES (?,?)\",
        (session['admin_id'],
         f\"generate_invite: {count} code(s) batch={batch or 'any'} note={note or 'none'}\")
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'codes': codes, 'count': len(codes),
                    'batch': batch, 'note': note})


@app.route('/admin/list_invites')
@admin_required
def admin_list_invites():
    \"\"\"Return all invite codes (unused first, then used), newest first.\"\"\"
    conn = get_db()
    rows = conn.execute(
        \"\"\"SELECT * FROM registration_codes
           ORDER BY is_used ASC, created_at DESC
           LIMIT 200\"\"\"
    ).fetchall()
    conn.close()
    return jsonify({'codes': [dict(r) for r in rows]})


@app.route('/admin/revoke_invite', methods=['POST'])
@admin_required
def admin_revoke_invite():
    \"\"\"Mark an unused code as used (effectively revoke it).\"\"\"
    data = request.json or {}
    code = (data.get('code') or '').strip().upper()
    if not code:
        return jsonify({'ok': False, 'msg': 'Code is required.'})
    conn = get_db()
    row  = conn.execute(
        \"SELECT id, is_used FROM registration_codes WHERE code=?\", (code,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Code not found.'})
    if row['is_used']:
        conn.close()
        return jsonify({'ok': False, 'msg': 'Code is already used/revoked.'})
    conn.execute(
        \"UPDATE registration_codes SET is_used=1, used_by='REVOKED', used_at=datetime('now') WHERE code=?\",
        (code,)
    )
    conn.execute(
        \"INSERT INTO admin_reset_log (admin_id, action) VALUES (?,?)\",
        (session['admin_id'], f'revoke_invite: Revoked code {code}')
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Code {code} has been revoked.'})


@app.route('/admin/delete_used_invites', methods=['POST'])
@admin_required
def admin_delete_used_invites():
    \"\"\"Delete all used/revoked codes to keep the table clean.\"\"\"
    conn = get_db()
    result = conn.execute(\"DELETE FROM registration_codes WHERE is_used=1\")
    deleted = result.rowcount
    conn.execute(
        \"INSERT INTO admin_reset_log (admin_id, action) VALUES (?,?)\",
        (session['admin_id'], f'delete_used_invites: Deleted {deleted} used/revoked codes')
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'msg': f'Deleted {deleted} used/revoked code(s).'})


@app.route('/admin/validate_invite')
@admin_required
def admin_validate_invite():
    \"\"\"Check if a specific code is valid (for admin lookups).\"\"\"
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({'ok': False, 'msg': 'Code required.'})
    conn = get_db()
    row  = conn.execute(
        \"SELECT * FROM registration_codes WHERE code=?\", (code,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'ok': False, 'msg': 'Code not found.'})
    return jsonify({'ok': True, 'code': dict(row)})
"""
