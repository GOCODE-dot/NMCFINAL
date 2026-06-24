# 🔧 NMMS Cook Sheet — Complete Fix Guide

## TL;DR — What's Wrong?
Your Cook's Daily Sheet shows "Pick a date above" but doesn't load because:
1. **Backend bug:** The API doesn't return hostel data for female students
2. **Frontend issue:** JavaScript might not auto-trigger on page load

---

## ✅ STEP 1: Fix the Python Backend

### **File:** `app.py`
### **Lines:** 3914-3966

The current code compiles `hostel_lunch` and `hostel_dinner` but **never includes them in the JSON response**.

**ACTION:** Copy the entire function from `cook_sheet_fixed.py` and replace lines 3914-3966 in `app.py`.

**What changed:**
- Added `hostel_lunch` and `hostel_dinner` to the JSON response ✓
- Properly mapped hostel floor numbers to names (Campus, Sentu House, Chairman House) ✓
- Added comments to make the code clearer ✓

**Before:**
```python
return jsonify({
    'ok': True, 'date': req_date,
    'lunch':  {'total': lunch_total,  'female': lunch_female,  'male': lunch_male},
    'dinner': {'total': dinner_total, 'female': dinner_female, 'male': dinner_male},
    'floor_lunch':  [...],
    'floor_dinner': [...],
    'hostel_lunch':  [...],  # ← Compiled but NEVER SENT!
    'hostel_dinner': [...],  # ← Compiled but NEVER SENT!
})
```

**After:**
```python
return jsonify({
    'ok': True, 
    'date': req_date,
    'lunch': {'total': lunch_total, 'female': lunch_female, 'male': lunch_male},
    'dinner': {'total': dinner_total, 'female': dinner_female, 'male': dinner_male},
    'floor_lunch': [...],
    'floor_dinner': [...],
    'hostel_lunch': [...],  # ← NOW ACTUALLY SENT!
    'hostel_dinner': [...], # ← NOW ACTUALLY SENT!
})
```

---

## ✅ STEP 2: (Optional but Recommended) Update the Frontend HTML

### **File:** `manager_dashboard.html`
### **Lines:** 3449-3497

The frontend currently expects hostel data but doesn't display it properly.

**ACTION A:** Update the `mealBlock()` function to handle hostel data:

Find around line 3449:
```javascript
function mealBlock(label, emoji, mealData, floorData) {
```

Change to:
```javascript
function mealBlock(label, emoji, mealData, floorData, hostelData) {
```

**ACTION B:** Add hostel rows after floor rows (around line 3456):

Find this section:
```javascript
const floorRows = (floorData || []).filter(f => f.floor != null).map(f => `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-radius:8px;margin-bottom:5px;background:#e6f0ff;border:1px solid #b3d1f5">
      <span style="display:flex;align-items:center;gap:8px;color:#3b5fe2;font-size:13px;font-weight:600">
        <i class="fas fa-layer-group" style="font-size:11px"></i> Floor ${f.floor}
      </span>
      <span style="font-size:18px;font-weight:800;color:#3b5fe2">${f.count}</span>
    </div>`).join('');

return `
```

Add the hostel rows BEFORE the `return` statement:
```javascript
const floorRows = (floorData || []).filter(f => f.floor != null).map(f => `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-radius:8px;margin-bottom:5px;background:#e6f0ff;border:1px solid #b3d1f5">
      <span style="display:flex;align-items:center;gap:8px;color:#3b5fe2;font-size:13px;font-weight:600">
        <i class="fas fa-layer-group" style="font-size:11px"></i> Floor ${f.floor}
      </span>
      <span style="font-size:18px;font-weight:800;color:#3b5fe2">${f.count}</span>
    </div>`).join('');

    // NEW: Hostel rows for female students
    const hostelRows = (hostelData || []).filter(h => h.floor != null).map(h => `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-radius:8px;margin-bottom:5px;background:#f3e8ff;border:1px solid #ddd6fe">
      <span style="display:flex;align-items:center;gap:8px;color:#7e22ce;font-size:13px;font-weight:600">
        <i class="fas fa-home" style="font-size:11px"></i> ${h.name}
      </span>
      <span style="font-size:18px;font-weight:800;color:#7e22ce">${h.count}</span>
    </div>`).join('');

return `
```

**ACTION C:** Update the function's return statement (around line 3458):

Find:
```javascript
    return `
        <div style="margin-bottom:22px">
          <!-- ... all the styles ... -->
          ${floorRows}
        </div>`;
```

Change to:
```javascript
    return `
        <div style="margin-bottom:22px">
          <!-- ... all the styles ... -->
          ${floorRows}${hostelRows}
        </div>`;
```

**ACTION D:** Update the calls to `mealBlock()` (around line 3488):

Find:
```javascript
body.innerHTML = `
    ${mealBlock('Lunch', '🌤️', lunch, floor_lunch)}
    ${mealBlock('Dinner', '🌙', dinner, floor_dinner)}
```

Change to:
```javascript
body.innerHTML = `
    ${mealBlock('Lunch', '🌤️', lunch, floor_lunch, hostel_lunch)}
    ${mealBlock('Dinner', '🌙', dinner, floor_dinner, hostel_dinner)}
```

---

## ✅ STEP 3: Test Everything

### **Browser Testing:**

1. **Clear cache** (Ctrl+Shift+Delete or Cmd+Shift+Delete)
2. **Open** the manager dashboard
3. **Click** "Cook's Daily Sheet" tab
4. **Wait** for the sheet to load (should show meal counts within 2 seconds)
5. **Verify:**
   - ✓ Lunch total, female, male counts appear
   - ✓ Dinner total, female, male counts appear
   - ✓ Male students listed by floor
   - ✓ Female students listed by hostel (if any exist)
   - ✓ Grand total shown at bottom
6. **Test refresh:** Click "Refresh" button — should reload
7. **Test date picker:** Change date and click Refresh — should load that date's data
8. **Test print:** Click "Print for Cook" — should open print dialog

### **Browser Developer Console Check:**

Press **F12** and go to **Console** tab:
- Should see NO error messages
- If you see errors, send them to me

Press **F12** and go to **Network** tab:
- Click **XHR** filter
- Click Cook Sheet tab
- Look for `/manager/cook_sheet?date=...` request
- Click it and check **Response** tab
- Should see valid JSON with all fields including `hostel_lunch` and `hostel_dinner`

---

## 🚨 If Still Not Working

Send me a screenshot of:

1. **Browser Console** (F12 → Console tab) showing any errors
2. **Network Response** (F12 → Network → click `/manager/cook_sheet` request → Response tab)
3. **Full server logs** if running locally

---

## 📝 Summary of Changes

| File | Lines | Change | Impact |
|------|-------|--------|--------|
| `app.py` | 3914-3966 | Return hostel data in JSON | Chef can see female student counts |
| `manager_dashboard.html` | 3449-3497 | Display hostel rows in UI | Cook sheet fully visible |
| `manager_dashboard.html` | 3487-3489 | Pass hostel data to functions | Hostel data actually rendered |

---

## ❓ FAQ

**Q: Why did this break?**
A: The code compiled hostel data but forgot to send it. Classic backend oversight!

**Q: Will this affect other features?**
A: No, only the cook sheet endpoint is changed. Everything else stays the same.

**Q: Why do female students show as "hostel" vs male as "floor"?**
A: Because female students live in named hostels (Campus, Sentu House, Chairman House) while male students live in numbered floors (1-7).

**Q: What if there are no female students?**
A: The hostel section will be empty but won't break anything.

**Q: Can I just delete the hostel code?**
A: No, if you have female students, you'll lose their meal count data!
