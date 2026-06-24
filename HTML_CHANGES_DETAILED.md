# HTML CHANGES FOR manager_dashboard.html

## ❌ WHAT'S BROKEN

The `mealBlock()` function doesn't know about hostel data. Even though the backend now sends it, the frontend can't display it.

---

## ✅ CHANGE 1: Update Function Signature

### Location: Line ~3449
### Current:
```javascript
function mealBlock(label, emoji, mealData, floorData) {
```

### New:
```javascript
function mealBlock(label, emoji, mealData, floorData, hostelData) {
  //                                                      ^
  //                                          Add this parameter
```

---

## ✅ CHANGE 2: Add Hostel Rows Section

### Location: After line ~3456 (right after floorRows definition)

### Current:
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

### New:
```javascript
  const floorRows = (floorData || []).filter(f => f.floor != null).map(f => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-radius:8px;margin-bottom:5px;background:#e6f0ff;border:1px solid #b3d1f5">
        <span style="display:flex;align-items:center;gap:8px;color:#3b5fe2;font-size:13px;font-weight:600">
          <i class="fas fa-layer-group" style="font-size:11px"></i> Floor ${f.floor}
        </span>
        <span style="font-size:18px;font-weight:800;color:#3b5fe2">${f.count}</span>
      </div>`).join('');

  // ── NEW: HOSTEL ROWS FOR FEMALE STUDENTS ────────────────────────────────
  const hostelRows = (hostelData || []).filter(h => h.floor != null).map(h => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-radius:8px;margin-bottom:5px;background:#f3e8ff;border:1px solid #ddd6fe">
        <span style="display:flex;align-items:center;gap:8px;color:#7e22ce;font-size:13px;font-weight:600">
          <i class="fas fa-home" style="font-size:11px"></i> ${h.name}
        </span>
        <span style="font-size:18px;font-weight:800;color:#7e22ce">${h.count}</span>
      </div>`).join('');
  // ────────────────────────────────────────────────────────────────────────

  return `
```

---

## ✅ CHANGE 3: Include Hostel Rows in Return Value

### Location: After floorRows in the return statement (around line 3483)

### Current:
```javascript
  return `
      <div style="margin-bottom:22px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:10px;border-bottom:2px solid var(--border)">
          <span style="font-size:22px">${emoji}</span>
          <span style="font-size:15px;font-weight:700;color:var(--text)">${label}</span>
          <span style="margin-left:auto;font-size:24px;font-weight:800;color:var(--primary)">${mealData.total}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:var(--primary-pale);border:1.5px solid #b7e4c7">
          <span style="display:flex;align-items:center;gap:8px;color:var(--primary);font-size:14px;font-weight:700">
            <i class="fas fa-users"></i> Total ${label}
          </span>
          <span style="font-size:22px;font-weight:800;color:var(--primary)">${mealData.total}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:#fce4ec;border:1.5px solid #f48fb1">
          <span style="display:flex;align-items:center;gap:8px;color:#c2185b;font-size:13px;font-weight:600">
            <i class="fas fa-venus"></i> Female
          </span>
          <span style="font-size:20px;font-weight:800;color:#c2185b">${mealData.female}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:#e6f0ff;border:1px solid #b3d1f5">
          <span style="display:flex;align-items:center;gap:8px;color:#3b5fe2;font-size:13px;font-weight:600">
            <i class="fas fa-mars"></i> Male (all floors)
          </span>
          <span style="font-size:20px;font-weight:800;color:#3b5fe2">${mealData.male}</span>
        </div>
        ${floorRows}
      </div>`;
  //    ^
  //    Only includes floor rows, not hostel rows!
```

### New:
```javascript
  return `
      <div style="margin-bottom:22px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:10px;border-bottom:2px solid var(--border)">
          <span style="font-size:22px">${emoji}</span>
          <span style="font-size:15px;font-weight:700;color:var(--text)">${label}</span>
          <span style="margin-left:auto;font-size:24px;font-weight:800;color:var(--primary)">${mealData.total}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:var(--primary-pale);border:1.5px solid #b7e4c7">
          <span style="display:flex;align-items:center;gap:8px;color:var(--primary);font-size:14px;font-weight:700">
            <i class="fas fa-users"></i> Total ${label}
          </span>
          <span style="font-size:22px;font-weight:800;color:var(--primary)">${mealData.total}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:#fce4ec;border:1.5px solid #f48fb1">
          <span style="display:flex;align-items:center;gap:8px;color:#c2185b;font-size:13px;font-weight:600">
            <i class="fas fa-venus"></i> Female
          </span>
          <span style="font-size:20px;font-weight:800;color:#c2185b">${mealData.female}</span>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-radius:8px;margin-bottom:5px;background:#e6f0ff;border:1px solid #b3d1f5">
          <span style="display:flex;align-items:center;gap:8px;color:#3b5fe2;font-size:13px;font-weight:600">
            <i class="fas fa-mars"></i> Male (all floors)
          </span>
          <span style="font-size:20px;font-weight:800;color:#3b5fe2">${mealData.male}</span>
        </div>
        ${floorRows}${hostelRows}
      </div>`;
  //    ^          ^
  //    Added hostel rows here!
```

---

## ✅ CHANGE 4: Pass Hostel Data to Functions

### Location: Around line 3487-3489 (where mealBlock is called)

### Current:
```javascript
  body.innerHTML = `
      ${mealBlock('Lunch', '🌤️', lunch, floor_lunch)}
      ${mealBlock('Dinner', '🌙', dinner, floor_dinner)}
      ...
```

### New:
```javascript
  body.innerHTML = `
      ${mealBlock('Lunch', '🌤️', lunch, floor_lunch, hostel_lunch)}
                                                       ^^^^^^^^^^^^
                                                       Add this parameter
      ${mealBlock('Dinner', '🌙', dinner, floor_dinner, hostel_dinner)}
                                                        ^^^^^^^^^^^^^^^
                                                        Add this parameter
      ...
```

---

## 🎯 Summary

| Change # | What | Where | Why |
|----------|------|-------|-----|
| 1 | Add `hostelData` parameter | Line 3449 | So function accepts hostel info |
| 2 | Add hostel rows loop | After 3456 | To build hostel HTML |
| 3 | Include `${hostelRows}` | Around 3483 | Actually show hostel data |
| 4 | Pass `hostel_lunch/dinner` | Lines 3488-3489 | Send data to function |

---

## ✔️ How to Verify Changes

After making all 4 changes:

1. Save `manager_dashboard.html`
2. Hard-refresh browser (Ctrl+Shift+R or Cmd+Shift+R)
3. Open manager dashboard
4. Click "Cook's Daily Sheet" tab
5. **Should now show:**
   - Lunch/Dinner totals ✓
   - Male students by floor ✓
   - Female students by hostel ✓ ← NEW!
   - Grand total ✓

---

## 🚨 Common Mistakes

❌ **DON'T:** Delete the entire `mealBlock()` function
✅ **DO:** Just modify the function signature and add the hostel rows section

❌ **DON'T:** Change `floorRows` to `hostelRows`
✅ **DO:** Include both: `${floorRows}${hostelRows}`

❌ **DON'T:** Change the hostel box styling to match floor styling
✅ **DO:** Keep the different colors (purple for hostels, blue for floors) so they're visually distinct

---

## 📝 Example Before/After

### BEFORE (doesn't display hostels):
```
Lunch: 85 total (Female: 20, Male: 65)
  └─ Floor 1: 12
  └─ Floor 2: 14
  └─ Floor 3: 13
  ...
Dinner: 92 total (Female: 22, Male: 70)
  └─ Floor 1: 14
  └─ Floor 2: 15
  ...
Grand Total: 177
```

### AFTER (displays both floors AND hostels):
```
Lunch: 85 total (Female: 20, Male: 65)
  └─ Floor 1: 12      (Male students)
  └─ Floor 2: 14      (Male students)
  └─ Floor 3: 13      (Male students)
  ...
  └─ Campus: 8        (Female students)
  └─ Sentu House: 7   (Female students)
  └─ Chairman House: 5 (Female students)
Dinner: 92 total (Female: 22, Male: 70)
  └─ Floor 1: 14      (Male students)
  └─ Floor 2: 15      (Male students)
  ...
  └─ Campus: 9        (Female students)
  └─ Sentu House: 8   (Female students)
  └─ Chairman House: 5 (Female students)
Grand Total: 177
```
