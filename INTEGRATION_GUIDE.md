# Phone / bKash Change Request Feature — Integration Guide

## What this adds
- Student can **request** a bKash number change from their dashboard
- Only **one pending request** allowed at a time per student
- Admin sees all requests in a panel and can **Approve or Reject** with one click
- On **Approve** → the student's bKash number is updated immediately in the DB and logged
- On **Reject** → nothing changes; student can submit a new request

---

## Step-by-step integration

### 1. Add the DB table (app.py → init_db)

Find your `c.executescript('''...''')` block inside `init_db()` and add this table:

```sql
CREATE TABLE IF NOT EXISTS phone_change_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL,
    old_bkash   TEXT NOT NULL,
    new_bkash   TEXT NOT NULL,
    reason      TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    decided_by  TEXT DEFAULT NULL,
    decided_at  TEXT DEFAULT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(student_id) REFERENCES students(id)
);
```

Then after your existing safe-migration blocks, add:
```python
    # Safe migration: phone change requests
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS phone_change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            old_bkash TEXT NOT NULL,
            new_bkash TEXT NOT NULL,
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            decided_by TEXT DEFAULT NULL,
            decided_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(student_id) REFERENCES students(id)
        )''')
        conn.commit()
    except Exception:
        pass
```

### 2. Add the routes (app.py)

Copy everything from `app_additions.py` (the 4 route functions) into app.py.
Place them near the other student routes (after `student_update_bkash`) and
admin routes (after `admin_update_student_floor`).

Remove the import lines at the top of app_additions.py if you already have them.

### 3. Add the student UI (student_dashboard.html)

Paste the contents of `student_dashboard_snippet.html` inside the
student dashboard — a good place is inside the **Profile / Settings** tab
or below the existing bKash update section.

### 4. Add the admin UI (admin_dashboard.html)

Paste the contents of `admin_dashboard_snippet.html` inside
`admin_dashboard.html` — recommended position: after the existing
student management cards, before the reset tools.

---

## Other editable fields you could add (suggestions)

Here is a full list of student profile fields that are good candidates
for a request-based or direct-edit flow:

| Field         | Type              | Notes |
|---------------|-------------------|-------|
| `name`        | Request + approval | Allow fixing typos; needs admin OK to prevent abuse |
| `batch`       | Request + approval | Batch changes are rare; admin should verify |
| `roll_number` | Admin-only edit   | Never let students edit this directly |
| `floor`       | Request or direct | You already have `admin_update_student_floor`; add student request |
| `gender`      | Direct or request | Simple select; low-risk |
| `password`    | Direct (self)     | Already supported via student reset flow |
| `bkash_number`| Request + approval | Exactly what this feature implements |

**Recommended: Student Profile Edit Request form** — one combined form that
lets students submit name/floor/gender corrections in a single request,
admin approves the whole bundle.

### Quick-add: Floor change request (minimal extra code)
Because floor affects meal planning, add a similar request flow:
- Student submits desired floor (1–10 or whatever your building has)
- Admin approves → `UPDATE students SET floor=? WHERE id=?`

Same pattern as this phone change feature, just swap the field.

---

## Testing checklist

- [ ] Student can submit a phone change request
- [ ] Second submission while pending shows "already pending" error
- [ ] Wrong password returns error, does NOT create a request
- [ ] Same number as current returns error
- [ ] Admin panel shows pending requests highlighted in yellow
- [ ] Approve → student's bKash number updates, request shows "approved"
- [ ] Reject → student's bKash number unchanged, request shows "rejected"
- [ ] After reject, student can submit a new request
- [ ] Action logged in admin_reset_log
