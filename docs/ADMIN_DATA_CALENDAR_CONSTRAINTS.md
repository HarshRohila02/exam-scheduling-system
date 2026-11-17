# Admin Portal - Data Management, University Calendar and Constraints

**Module owner:** Harshit Kumar (240470)
**SRS:** 3.2.1 Data Management, 3.2.2 University Calendar and Constraints
**MoSCoW:** R2 manage records, R3 exam calendar, R4 versioned constraints, R18 usable without programming
**Use cases:** Manage Student / Faculty / Course / Room Records, Import Records from CSV / Excel File,
Validate Record Data (uniqueness / mandatory), Configure Examination Calendar, Configure Scheduling Constraints,
Maintain Constraint Version History
**DFD Level 1:** 2.0 *Manage Master Data* (D2 Students & Registrations, D3 Faculty, D4 Courses, D5 Rooms) and
3.0 *Configure Calendar & Constraints* (D6 University Calendar, D7 Constraint Configurations, versioned)

## Where my module sits in the system

```
Examination staff --> [2.0 Manage Master Data]  --> Students, Registrations, Faculty, Courses, Rooms --+
                  --> [3.0 Calendar & Constraints] --> Calendar, Sessions, Holidays, Constraint versions -+--> [4.0 Generate timetable (CSP)]
```

My module is the data entry and set-up part of the Admin Portal. The timetable generator (Harsh Rohila's module)
reads what my module stores: the master data, the exam slots made from the calendar (the CSP domain) and the
active constraint version (the CSP constraints). I used the same tables as the rest of the team (`app/models.py`),
so the generator, the Student Portal and the Faculty Portal work with my module without any change on their side.

## Files

| File | What it does |
|---|---|
| `app/services/validation.py` | Describes every record type (fields and rules) and checks one record: mandatory fields, unique ID, numbers in range, email, yes/no, regular/repeat, designation has duty limits, registration points to a real student and course |
| `app/services/csv_import.py` | CSV and Excel (.xlsx) upload. Both become header + rows and go through the same checks; all-or-nothing |
| `app/services/calendar_service.py` | Calendar checks (period, holiday inside the period, session times, no overlapping sessions), the day-by-day preview, and the calendar version history (`CalendarVersion` table) |
| `app/services/constraint_versions.py` | Constraint form checks, saving a new version, making an old one active, default version 1, and "what changed" between versions |
| `app/routes/data.py` | Data pages: list + search, add, edit, delete, upload, template download, faculty unavailable dates |
| `app/routes/calendar.py` | University Calendar page |
| `app/routes/constraints.py` | Constraints page |
| `app/templates/data_list.html`, `data_form.html`, `data_unavailability.html`, `calendar.html`, `constraints.html` | The pages (Bootstrap, like the rest of the Admin Portal) |
| `tests/test_admin_data_calendar.py` | 38 tests: TC01 - TC10 of my test-case document and 28 more |

## How each SRS point is met

**SRS 3.2.1 Data Management**
- *Inputs* - students (ID, name, program, section, semester, email), their courses and regular / repeat type
  (Registrations), faculty (ID, designation, department, personal duty limit, unavailable dates), rooms (ID,
  capacity, availability), courses (code, name, semester, exam length). Entered in forms or uploaded as CSV / Excel.
- *Processing* - `check_record()` checks mandatory fields and ID uniqueness and every value, then the record is
  stored. Records can be created, edited (the ID cannot change) and deleted. A record used by a draft or published
  timetable cannot be deleted, so a timetable never points to something that no longer exists.
- *Outputs* - updated lists (with search) and a clear message for every wrong value; on upload, one message per
  wrong row ("Row 4: Capacity must be a whole number") and nothing saved until the file is correct.

**SRS 3.2.2 University Calendar and Constraints**
- *Inputs* - first and last exam day, Sundays allowed or not, holidays, session times; students per invigilator,
  room fill %, same-day rule, minimum / maximum duties per designation, soft-rule weights, solver time limit.
- *Processing* - the calendar and the constraints are stored; **every change is kept as a version** (calendar
  history and constraint versions); the generator gets the current calendar and the active constraint version.
- *Outputs* - the calendar with a preview of exactly which days and sessions the generator may use, and the active
  constraint set with its full history ("what changed" in plain words).

**Extra checks I added because the SRS says the data must be validated before it is used (SRS 3.5)**
- A holiday outside the exam period is refused (it would change nothing).
- Sessions may not overlap (FN 09:30-15:00 with AN 14:00-17:00 would let one student have two exams at once).
- A session must be at least 30 minutes; at least one session must remain.
- The exam period can be at most 90 days.
- A designation used by faculty must keep its duty limits; minimum duties cannot be above maximum.
- Every change is written to the audit log, and only examination staff (ADMIN) can open these pages.

## Test cases

Run: `pytest -v tests/test_admin_data_calendar.py` (actual outputs are written to `tests/test_results.json`
under the keys `Harshit-TC01` ... `Harshit-TC10`).

| TC | Scenario | Expected output | Actual output | Status |
|---|---|---|---|---|
| TC01 | Add a student with valid data | Saved and in the student list | S900 saved with section, semester, course CS301 Regular; shown in the list | Pass |
| TC02 | Duplicate ID | "ID already exists", not saved | "Student ID S900 already exists" / "Faculty ID F50 already exists"; not saved | Pass |
| TC03 | Room ID or capacity blank | Validation message, not saved | "Room ID is required" / "Capacity is required"; not saved | Pass |
| TC04 | Room capacity 60 -> 80 | Saved and shown | R301 capacity 80, shown in the room list | Pass |
| TC05 | Delete a faculty record | Removed from the list | F6 deleted, not in the list | Pass |
| TC06 | Valid period + holidays + sessions | Saved, available to the generator | Saved; generator readiness shows 22 exam slots | Pass |
| TC07 | Start 20 Dec, end 10 Dec | Error, not saved | "The end date cannot be before the start date."; not saved | Pass |
| TC08 | Ratio 30, max duties 5 | Saved as active configuration | Version 2 active with ratio 30, max 5 | Pass |
| TC09 | Ratio 30 -> 25 | New version, old one visible | v2 (25) active, v1 (30) in the history, "30 -> 25" shown | Pass |
| TC10 | Ratio -5 or abc | Validation message, not saved | "must be between 1 and 200" / "must be a whole number"; not saved | Pass |

## Run the module

It runs inside the team project:

```powershell
pip install -r requirements.txt
pip install openpyxl          # only needed for Excel (.xlsx) upload; CSV works without it
python seed.py --reset
python run.py                 # http://localhost:5000, log in as the admin
```

Try: **Data > Rooms** (add with capacity empty, edit 60 -> 80, upload a CSV or .xlsx), **University Calendar**
(20 Dec - 10 Dec, a holiday outside the period, a session 12:00-15:00, then scroll to *Calendar history*),
**Constraints** (30 -> 25, then look at *What changed*; try -5 and abc).
