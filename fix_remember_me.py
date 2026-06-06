# ══════════════════════════════════════════════════════════════════════════════
# FIX: "Remember Me" checkbox — student & manager login
#
# BUG: Both routes always set  session.permanent = True  regardless of whether
#      the checkbox was ticked. The checkbox existed in the HTML but was never
#      read in Python.
#
# HOW TO APPLY:
#   Make the two replacements described below in app.py.
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# REPLACEMENT 1 — student_login route
# ══════════════════════════════════════════════════════════════════════════════
#
# FIND this block (around line 619):
#
#             session.permanent = True
#             session['user_id'] = row['id']
#             session['role']    = 'student'
#             session['name']    = row['name']
#             session['roll']    = row['roll_number']
#             return redirect(url_for('student_dashboard'))
#
# REPLACE WITH:
#
#             remember_me        = request.form.get('remember_me') == '1'
#             session.permanent  = remember_me       # True = 30-day cookie, False = browser-session only
#             session['user_id'] = row['id']
#             session['role']    = 'student'
#             session['name']    = row['name']
#             session['roll']    = row['roll_number']
#             return redirect(url_for('student_dashboard'))


# ══════════════════════════════════════════════════════════════════════════════
# REPLACEMENT 2 — manager_login route
# ══════════════════════════════════════════════════════════════════════════════
#
# FIND this block (around line 1182):
#
#             session.permanent = True
#             session['user_id']          = row['id']
#             session['role']             = 'manager'
#             session['name']             = display_name
#             session['mgr_bkash']        = row['bkash_number']
#             session['must_change_pass'] = bool(row['must_change_password'])
#
# REPLACE WITH:
#
#             remember_me        = request.form.get('remember_me') == '1'
#             session.permanent  = remember_me       # True = 30-day cookie, False = browser-session only
#             session['user_id']          = row['id']
#             session['role']             = 'manager'
#             session['name']             = display_name
#             session['mgr_bkash']        = row['bkash_number']
#             session['must_change_pass'] = bool(row['must_change_password'])


# ══════════════════════════════════════════════════════════════════════════════
# HOW IT WORKS
# ══════════════════════════════════════════════════════════════════════════════
#
#  Checkbox ticked  →  remember_me = True  →  session.permanent = True
#                      Flask keeps the cookie for PERMANENT_SESSION_LIFETIME
#                      (currently 30 days — set at top of app.py)
#
#  Checkbox NOT ticked  →  remember_me = False  →  session.permanent = False
#                          Flask creates a browser-session cookie that expires
#                          when the tab/browser is closed
#
# The checkbox value sent by the browser is '1' when checked and absent (None)
# when unchecked, so  request.form.get('remember_me') == '1'  is the correct
# check regardless of whether the field exists in the form data.
#
# No changes needed in the HTML — the checkbox already sends name="remember_me"
# with value="1" (default HTML checkbox behaviour).
# ══════════════════════════════════════════════════════════════════════════════
