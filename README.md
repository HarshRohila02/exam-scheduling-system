# Automated Examination Management and Scheduling System - Admin Portal

Admin Portal for exam staff: manage students, faculty, courses and rooms; set the university calendar
and scheduling constraints (with version history); generate an exam timetable, room/seat allocation and
invigilation duties with a **constraint-programming solver (Google OR-Tools CP-SAT)**; validate it; publish it.

This repository covers SRS sections **3.2.1 - 3.2.4**. The Faculty Portal and Student Portal are built
separately; they read published data through `app/services/published_view.py`.

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

No Docker? Put `DATABASE_URL=sqlite:///aemss.db` in `.env` and skip the `docker compose` line - the same code runs on SQLite.

## Try the demo

1. **Dashboard** shows a checklist of what is needed before a timetable can be generated.
2. **Data > Students** (or Courses, Faculty, Rooms): add records by hand or upload a CSV. Use *Download template* to see the format.
   Upload a file with a mistake in it to see the row-by-row error messages.
3. **University Calendar**: change the exam period, add a holiday, and watch the slot preview change.
4. **Constraints**: change *Students per invigilator* and save. A new version is created; the old one is kept and can be re-activated.
5. **Timetable > Generate Timetable**. Review the grid, the room/seating lists and the duty list, then **Publish**.
6. To see conflict reporting, shorten the exam period to a single day and generate again.

## How the CSP model works (SRS 3.2.4)

A timetable is a set of choices. The solver is given the choices that exist (the **domains**) and the rules that must
hold (the **constraints**) and searches for a combination that satisfies all of them.

| Part of the model | In this project (`app/scheduler/cp_model_builder.py`) |
|---|---|
| Variables | For every exam *e*, slot *t* (date + session), room *r*, faculty *f*: `x[e,t]` exam is in slot; `use[e,r,t]` exam uses room; `seats[e,r,t]` students seated in the room; `inv[e,r,t]` invigilators the room needs; `duty[f,r,t]` faculty supervises the room |
| Domains | Slots come from the University Calendar (holidays and Sundays removed), rooms from the available rooms (capacity x fill %), faculty from the faculty list minus their unavailable sessions |
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
get_faculty_duties("F1")       # date, session, times, course, room
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
