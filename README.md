# Automated Examination Management and Scheduling System - Admin Portal

Admin Portal for exam staff: manage students, faculty, courses and rooms; set the university calendar
and scheduling constraints (with version history); generate an exam timetable, room/seat allocation and
invigilation duties with a **constraint-programming solver (Google OR-Tools CP-SAT)**; validate it; publish it.

This repository covers SRS sections **3.2.1 - 3.2.4** (Admin Portal) and **3.2.7 - 3.2.8** (Student Portal).
The Faculty Portal is built separately; portals read published data through `app/services/published_view.py`.

**Teammates: start with [HANDOFF.md](HANDOFF.md)** - what is done, how the pieces connect, and a starting point for each portal.

| SRS section | What it covers | Where the code is |
|---|---|---|
| 3.2.1 Data Management | Students, courses, registrations, faculty, rooms: add / edit / delete, CSV upload, validation | `app/routes/data.py`, `app/services/validation.py`, `app/services/csv_import.py` |
| 3.2.2 Calendar and Constraints | Exam period, sessions, holidays; constraint values with version history | `app/routes/calendar.py`, `app/routes/constraints.py`, `app/services/constraint_versions.py` |
| 3.2.3 Generation, Allocation, Publishing | Generate -> validate -> review -> publish; seats, invigilators, notifications | `app/routes/timetable.py`, `app/services/timetable_service.py` |
| 3.2.4 CSP Model | Variables, domains, hard and soft constraints, solver | `app/scheduler/` |

## Tech stack

Python 3 - Flask - Flask-SQLAlchemy - MySQL 8 (Docker) - Jinja2 templates + Bootstrap 5 - OR-Tools CP-SAT - pytest

## Run it (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env          # edit the passwords if you like
docker compose up -d            # starts MySQL on port 3306 (wait ~20 seconds the first time)
python seed.py --reset          # creates the tables and loads the sample data
python run.py                   # open http://localhost:5000
```

Log in with the `ADMIN_USERNAME` / `ADMIN_PASSWORD` from your `.env`.
Students log in with their enrollment number (for example `S001`) and the temporary `STUDENT_DEFAULT_PASSWORD` from `.env`
(default `student123`); they must choose their own password at the first login.

No Docker? Put `DATABASE_URL=sqlite:///aemss.db` in `.env` and skip the `docker compose` line - the same code runs on SQLite.

## Try the demo

1. **Dashboard** shows a checklist of what is needed before a timetable can be generated.
2. **Data > Students** (or Courses, Faculty, Rooms): add records by hand or upload a CSV. Use *Download template* to see the format.
   Upload a file with a mistake in it to see the row-by-row error messages.
3. **University Calendar**: change the exam period, add a holiday, and watch the slot preview change.
4. **Constraints**: change *Students per invigilator* and save. A new version is created; the old one is kept and can be re-activated.
5. **Timetable > Generate Timetable**. Review the grid, the room/seating lists and the duty list, then **Publish**.
6. To see conflict reporting, shorten the exam period to a single day and generate again.

## Student Portal (SRS 3.2.7 - 3.2.8)

Code: `app/routes/student.py`, `app/templates/student/`, `app/services/student_accounts.py`, tests in `tests/test_student_portal.py`.

| Page | What the student sees |
|---|---|
| Dashboard `/student/` | Their details, the next exam with room and seat, a summary, the exam instructions, the latest notifications |
| My Timetable `/student/timetable` | Every exam: date, day, session, time, course, duration, room, seat, regular/backlog. Table on a computer, cards on a phone, a *Print* button |
| Exam details `/student/exam/<course>` | Room (and building), seat, seat number within the room, instructions |
| Notifications `/student/notifications` | Newest first, unread ones marked; *Mark all as read*. The menu shows the unread count |
| Change Password `/student/password` | Forced at first login; at least 8 characters, not the enrollment number or the temporary password |

- **Login link:** a student's username is their enrollment number (`User.username == Student.student_id`). A login whose student
  record was deleted is refused.
- **How the admin gives students their logins:** *Data > Student Logins* (`/student-logins/`) lists every student with their login
  status (no login / temporary password / active). *Create logins for N students without one* gives each of them their **own random
  temporary password**, shown once on a list that can be printed or downloaded as CSV and handed out; only password hashes are saved.
  *Reset password* gives one student a new temporary password. Both are written to the audit log. After adding students on the
  Students page (by hand or CSV), come back to this page to create their logins.
- **Demo and tests:** `python seed.py` instead gives every sample student the same `STUDENT_DEFAULT_PASSWORD` from `.env`.
- **Own data only (SRS 3.6):** the enrollment number always comes from the login session, never from the address. Opening another
  course's exam page gives 404; admin pages send a student back to the login page, and student pages do the same for the admin.
- **Nothing before publishing:** until a timetable is `PUBLISHED` (or after it is archived) the pages say "not published yet".
- Exams before today are shown as *Over*; the sample exam week is 2-7 Nov 2026, so move the exam period forward on the
  University Calendar page (and generate and publish again) to see an upcoming "next exam" on the dashboard.
- **Look:** the login and student pages use `app/static/css/student.css` (pastel colours, rounded cards). Bootstrap 5.3.3 is
  kept in `app/static/vendor/bootstrap/` instead of loading from a CDN, so every page is styled even without internet access.
- **Existing databases:** `User` has a new column `must_change_password`. Run `python seed.py --reset` once (or add the column by hand).

## How the CSP model works (SRS 3.2.4)

A timetable is a set of choices. The solver is given the choices that exist (the **domains**) and the rules that must
hold (the **constraints**) and searches for a combination that satisfies all of them.

| Part of the model | In this project (`app/scheduler/cp_model_builder.py`) |
|---|---|
| Variables | For every exam *e*, slot *t* (date + session), room *r*, faculty *f*: `x[e,t]` exam is in slot; `use[e,r,t]` exam uses room; `seats[e,r,t]` students seated in the room; `inv[e,r,t]` invigilators the room needs; `duty[f,r,t]` faculty supervises the room |
| Domains | Slots come from the University Calendar (holidays and Sundays removed; an exam only gets sessions at least as long as the exam), rooms from the available rooms (capacity x fill %), faculty from the faculty list minus their unavailable sessions |
| Hard constraints | Every exam gets exactly one slot - every student gets a seat and a room holds one exam per session - courses that share students never meet in a session (or on one day, if that rule is on) - invigilators per room = ceil(students / ratio) - nobody supervises two rooms in one session - nobody is used while unavailable - each person's duties stay between their minimum and maximum |
| Soft constraints (objective) | Spread duties evenly (smallest gap between busiest and lightest faculty) - leave rest days between exams of students who take several - avoid needless extra rooms or invigilators |
| Solver | `ortools.sat.python.cp_model.CpSolver`, called by the **Generate Timetable** button |

Regular and backlog students sit the same exam; their type is stored on the seat and shown in the seating lists.
A course may use several rooms in its slot (for example 90 students in a 60-seat and a 30-seat room).

**When no timetable exists:** quick pre-checks give exact messages (for example "CS401 has 200 students; all rooms together
seat only 135"). If the solver proves the rules cannot all hold, the model is solved again with each group of rules on its own
switch, and CP-SAT reports which groups conflict, with advice on what to relax. The result is saved as a `CONFLICT` report; nothing is published.

**Safety net:** `app/scheduler/validator.py` re-checks every finished timetable without using the solver, and again when you
press Publish (against today's data). A timetable with any violation cannot be published.

The solver stops when it has proven the best timetable, when 3 seconds pass without a better one, or at the time limit
set on the Constraints page, whichever comes first.

## Folder guide

```
app/
  models.py              database tables
  auth.py                login, admin-only pages, CSRF token, audit-log helper
  routes/                one file per group of pages (data, calendar, constraints, timetable, audit, dashboard)
  services/              rules that are not about pages: validation, CSV import, constraint versions,
                         timetable_service (database <-> solver), published_view (for the other portals)
  scheduler/             the CSP part - plain Python, knows nothing about Flask or the database
    problem.py             small input/output classes and error types
    prechecks.py           friendly checks before solving
    cp_model_builder.py    the CP-SAT model
    solver.py              runs the solver, explains infeasibility
    seating.py             seat numbers
    validator.py           independent re-check
  templates/ static/     HTML pages and CSS
sample_data/             CSV files loaded by seed.py
tests/                   pytest tests (run: pytest)
```

## For the Faculty and Student portal developers

Call these functions (they raise `NotPublishedError` until the admin publishes):

```python
from app.services.published_view import (get_student_exams, get_faculty_duties,
                                         get_master_timetable, get_exam_instructions, get_notifications)
get_student_exams("S001")      # date, session, times, course, room, seat, regular/backlog
get_faculty_duties("F1")       # date, session, times, course, room, duty_id
```

## Tests

```powershell
pytest -v
```

Tests use an in-memory SQLite database, so they need neither Docker nor MySQL. Each `test_TCnn_*` test is one row of the
test-case document and writes its actual output to `tests/test_results.json`.

Changes from the first prototype's test cases: **TC08** now means "a course larger than *all rooms combined* is reported"
(a course bigger than one room is now split across rooms - see **TC08b**), and **TC20/TC21** were reworded (non-admin users
cannot publish; a conflict result cannot be published).
