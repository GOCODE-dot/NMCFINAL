# 🍽️ Netrokona Medical Meal Management System (NMMS)
**Prototype v1.0**

## Features
- **Student Portal**: Register with name, batch, roll number, bKash number & password. Order Lunch/Dinner per day. View due amounts and manager's bKash number to send payment.
- **Manager Portal**: Single bKash number for all student payments. View daily/weekly meal counts, revenue, pending payments. Mark students as paid. Clear 7-day old data.
- 2 meals/day: Lunch & Dinner — ৳50 each (auto-calculated)
- Supports up to 2,000 students
- Mobile & PC responsive, light theme
- 7-day rolling data (clear weekly via manager panel)

## Setup & Run

```bash
# 1. Install dependencies
pip install flask

# 2. Run the app
python app.py

# 3. Open browser at:
http://localhost:5000
```

## Demo Credentials
**Manager Login:**
- ID: `MGR001`
- Password: `manager123`

**Student:** Register at `/student/register`

## File Structure
```
nmms/
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── nmms.db             # SQLite database (auto-created)
└── templates/
    ├── base.html
    ├── index.html
    ├── student_login.html
    ├── student_register.html
    ├── student_dashboard.html
    ├── manager_login.html
    ├── manager_dashboard.html
    └── manager_students.html
```

## Meal Rules
- Order meals before **11 PM** the previous night
- Pay exact amount to manager's bKash number
- Use your roll number as payment reference
- Manager marks payment as verified in dashboard
- Data older than 7 days is cleared by manager
