# Handoff: Admin Portal (timetable generation, allocation, publishing, CSP model)

Written by Harsh Rohila (240466) for Himanshi (240475), Harsha Anvitha (240469) and Harshit Kumar (240470).
Read this first, then `README.md` (setup and the CSP explanation) and the SRS (`SE_SRS_Document`).

## 1. Where things stand

| SRS section | Owner | State in this repo |
|---|---|---|
| 3.2.3 Timetable generation, allocation, publishing | **Harsh** | Done and tested |
| 3.2.4 CSP model (OR-Tools CP-SAT) | **Harsh** | Done and tested |
| 3.2.1 Data management, 3.2.2 Calendar and constraints | Harshit | A working first version is included, because generation cannot run without data, a calendar and constraints. Harshit: review it, extend it, or replace it (see section 7) |
| 3.2.5 - 3.2.6 Faculty Portal | Himanshi | **Not started in this repo.** The read-only data interface is ready (section 5) |
| 3.2.7 - 3.2.8 Student Portal | Harsha | **Not started in this repo.** The read-only data interface is ready (section 5) |

What the Admin Portal does today, end to end: an admin logs in, loads or edits the data, sets the exam period and rules,
presses **Generate Timetable**, reviews the draft (date/session grid, seat lists, invigilation duties, validation result),
and presses **Publish**. A published timetable is what the Faculty and Student portals are allowed to show.

## 2. Run it

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env            # then edit the values (the file is git-ignored)
docker compose up -d              # MySQL 8 on port 3306; wait ~20 s the first time
python seed.py --reset            # creates the tables and loads the sample data
python run.py                     # http://localhost:5000
```

No Docker? Set `DATABASE_URL=sqlite:///aemss.db` in `.env` and skip the `docker compose` line; the same code runs on SQLite
(this is also the default when `DATABASE_URL` is not set).

Sample data (`sample_data/*.csv`): 165 students, 5 courses (CS301, CS302, CS303, MA201, EE210), 205 registrations
(5 of them backlog), 6 faculty F1-F6 (2 Professors, 2 Associate Professors, 2 Assistant Professors), 3 rooms
(R101 holds 60, R102 holds 45, R201 holds 30), exam period 2-7 Nov 2026 with sessions FN 09:30-12:30 and AN 14:00-17:00.
Default rules: 1 invigilator per 30 students, rooms 100% usable, no two exams on one day for a student, duty limits
Professor 0-2, Associate Professor 1-3, Assistant Professor 1-4.

**Python 3.14 and Windows note:** the packages in `requirements.txt` include compiled parts (OR-Tools, pandas, SQLAlchemy).
If Windows Smart App Control blocks one of them ("An Application Control policy has blocked this file"), run the command again.
The block can be intermittent while Windows checks the file. Do not disable Windows security features for this project.

## 3. How a timetable is made

```
Generate Timetable (POST /timetable/generate)
  -> timetable_service.build_problem()      read students, courses, rooms, faculty, calendar, active rules
  -> scheduler.solver.solve()               prechecks -> CP-SAT model -> solution -> seat numbers
  -> scheduler.validator.validate_timetable()   independent re-check, no solver involved
  -> saved as DRAFT (rooms, seats, duties)  or as CONFLICT (a report explaining which rules clash)
Review (GET /timetable/<id>)                grid, seats, duties, validation
Publish (POST /timetable/<id>/publish)      re-validates against today's data, archives the old one,
                                            writes notifications, status = PUBLISHED
```

Status values (`Timetable.status`): `DRAFT -> PUBLISHED -> ARCHIVED`, or `CONFLICT` when no valid timetable exists.
Only one timetable is `PUBLISHED` at a time. Publishing a new one archives the previous one.

How this maps to the team's state-transition diagram: *Generating*, *Generated* and *Validated* are not stored as separate
statuses. Generating is a spinner on the button while the request runs, and generated plus validated together are `DRAFT`
(a result that fails validation is stored as `CONFLICT`). *Conflict Detected* is `CONFLICT`. *Published* and *Archived* are as in the diagram.
*Duty-Swap Requested / Approved / Rejected / Under Revision* belong to the Faculty Portal and are not built yet.

### The CSP model (SRS 3.2.4), in short
Full explanation with the variable and constraint list is in `README.md` and in the comments of `app/scheduler/cp_model_builder.py`.
- Variables: for each exam and (date, session) slot: is it held there; which rooms does it use; how many students sit in each;
  how many invigilators each room needs; which faculty member supervises which room.
- Hard rules: every exam gets exactly one slot, in a session at least as long as the exam; every student gets a seat; a room holds
  one exam per session; room capacity (capacity x fill %); courses that share a student never meet in a session (or on one day, if that rule is on);
  invigilators per room = ceil(students / ratio); nobody supervises two rooms in one session or while unavailable;
  each person's duties stay within the min and max for their designation (or their personal override).
- Soft rules (the objective): even spread of duties, rest days between a student's exams, no needless extra rooms or invigilators.
- If no timetable exists, quick prechecks give exact messages; otherwise the model is solved again with each rule group on its own
  switch, CP-SAT reports which groups conflict, and the list is narrowed to the groups that are really needed for the conflict,
  each with advice on what to relax.
- The validator (`validator.py`) re-checks the same rules without the solver: slots, exam length against session length, rooms and capacity,
  every student seated exactly once, invigilators (known people, exactly ceil(students / ratio) per room, none unavailable, none double-booked),
  duty limits, student clashes. A timetable with any violation cannot be published.

## 4. Code map

```
app/
  __init__.py            create_app(): registers the page groups (blueprints), creates tables
  models.py              all database tables
  auth.py                login (admins only for now), admin_required, CSRF check, log_action (audit log)
  routes/                one file per group of pages: dashboard, data, calendar, constraints, timetable, audit
  services/
    timetable_service.py   database <-> solver: generate, publish, archive, notifications, readiness checklist
    published_view.py      READ-ONLY functions for the Faculty and Student portals (section 5)
    validation.py, csv_import.py, constraint_versions.py   used by the data / constraints pages
  scheduler/             the CSP part: plain Python, knows nothing about Flask or the database
    problem.py             small input/output classes and the three error types
    prechecks.py           friendly checks before solving
    cp_model_builder.py    the CP-SAT model
    solver.py              runs the solver, explains infeasibility
    seating.py             seat numbers (R101-S01 ...)
    validator.py           independent re-check of a finished timetable
  templates/ static/     Jinja2 pages and CSS (Bootstrap 5)
sample_data/  seed.py    sample CSVs and the loader
tests/                   pytest (in-memory SQLite, no Docker needed)
```

## 5. Interface for the Faculty and Student portals

Rule: **portals must not query the timetable tables directly.** They call `app/services/published_view.py`, so the rule
"nothing is visible until the admin publishes" lives in one place. Every function raises `NotPublishedError`
(from `app.scheduler.problem`) while no timetable has status `PUBLISHED`; catch it and show an "not published yet" page.
These functions need an app context (a Flask request is enough).

```python
from app.scheduler.problem import NotPublishedError
from app.services.published_view import (get_student_exams, get_faculty_duties, get_master_timetable,
                                         get_exam_instructions, get_notifications)
```

Real output from the sample data (published with the instructions "Bring your ID card. Reach 30 minutes early."):

```python
get_student_exams("S001")      # sorted by date, then start time; one row per exam
[{"course": "MA201", "course_name": "Probability and Statistics", "duration_minutes": 180,
  "date": "2026-11-04", "session": "AN", "start_time": "14:00", "end_time": "17:00",
  "room": "R101", "seat": "R101-S01", "exam_type": "regular"}, ...]      # exam_type: "regular" or "backlog"

get_faculty_duties("F1")       # one row per room the person supervises
[{"course": "CS302", "course_name": "Operating Systems", "duration_minutes": 180,
  "date": "2026-11-03", "session": "FN", "start_time": "09:30", "end_time": "12:30",
  "room": "R102", "duty_id": 1}, ...]          # duty_id and the exact slots change from run to run (see section 9)

get_master_timetable()         # every exam, with all its rooms (Faculty Portal: Timetable View)
[{"course": "CS302", ..., "rooms": ["R102"]}, ...]

get_exam_instructions()        # the admin's text from the Publish dialog: "Bring your ID card. ..."  ("" if none)

get_notifications("FACULTY", "F1")   # newest first; returns Notification rows (message, created_at, is_read)
get_notifications("STUDENT", "S001")
```

Notifications written at publishing time: every faculty member gets "The examination timetable has been published. You have N invigilation
duties." (or "...no invigilation duties."); every student who has an exam gets "Your examination timetable, rooms and seats have been
published." Marking a notification as read (`Notification.is_read`) is not provided; add it in the Faculty/Student portal code.

Adding a portal: create `app/routes/faculty.py` (or `student.py`) with a `Blueprint`, add it to the `for module in (...)` list in
`app/__init__.py`, and use the same patterns as the admin pages (see `app/routes/timetable.py`): a decorator for the role check,
`log_action(...)` for anything that changes data, and `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` in every POST form.

### Login: what exists and what you have to add
- `User` has `username`, `password_hash` and `role` (`ADMIN` / `FACULTY` / `STUDENT`). Only the admin account is created (by
  `ensure_admin_user`, from `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`).
- `auth.login` currently accepts **only** `role == "ADMIN"`, and `admin_required` checks `session["role"] == "ADMIN"`.
- `User` has no link to a student or faculty record. **Team decision needed:** the simplest option is `username == student_id / faculty_id`
  (for example `S001`, `F1`); the alternative is a `ref_id` column on `User`. Pick one and use it in both portals.
- You will need: a `faculty_required` / `student_required` decorator next to `admin_required`, role-aware login, and a way to create
  accounts for existing students and faculty (for example a script in `seed.py`). SRS 3.6: a student or faculty member may only see their own data.

## 6. Starting points for each teammate

**Himanshi - Faculty Portal (SRS 3.2.5, 3.2.6)**
- Dashboard / My Duties: `get_faculty_duties(faculty_id)`. Each row has a `duty_id` (the `InvigilationDuty.id`) so a swap request can point at it.
- Timetable View: `get_master_timetable()`.
- Notifications: `get_notifications("FACULTY", faculty_id)`.
- Availability: the admin page for this already exists (`/data/faculty/<id>/unavailability`, table `FacultyUnavailability`: date plus optional session,
  empty session = whole day). Reuse its validation for the faculty-side form. Unavailability entered after publishing does not change the published timetable.
- Duty Swap needs a new table, for example `DutySwapRequest(id, duty_id, from_faculty_id, to_faculty_id, reason, status PENDING/APPROVED/REJECTED, created_at, decided_by, decided_at)`.
  Eligibility of the colleague (SRS 3.2.6): not unavailable in that slot, not already supervising in that slot, and still within their maximum duties
  (`get_active_version().limit_for(designation).max_duties`, or `Faculty.max_duties_override`). On approval change `InvigilationDuty.faculty_id`,
  re-check the timetable with `timetable_service.validate_saved_timetable(timetable)` (returns a list of violations, empty = valid), write a `Notification`
  for both people, and call `log_action`. Who approves (admin screen or automatic) is an open question, see section 9.

**Harsha - Student Portal (SRS 3.2.7, 3.2.8)**
- Dashboard / Exam Timetable: `get_student_exams(student_id)`, already sorted by date. Rows include date, session, start and end time, course, duration, and `exam_type`.
- Room, seat and instructions: the same rows give `room` and `seat` (format `R101-S01`; the number after `S` runs from 01 within a room);
  `get_exam_instructions()` gives the admin's text. Room capacity is in the `Room` table if you want a seat-position picture.
- Notifications: `get_notifications("STUDENT", student_id)`.
- Backlog students sit the same exam as regular students; `exam_type` only labels them.

**Harshit - Data management, calendar and constraints (SRS 3.2.1, 3.2.2)**
The code that exists (written as the foundation so that generation can run): `app/routes/data.py`, `calendar.py`, `constraints.py`,
`app/services/validation.py`, `csv_import.py`, `constraint_versions.py`, the templates `data_*.html`, `calendar.html`, `constraints.html`, and the tests
`tests/test_data_management.py`, `tests/test_calendar_constraints.py`. It covers: add / edit / delete and CSV upload (with row-by-row error messages and
nothing saved if any row is wrong) for students, courses, registrations, faculty and rooms; exam period, holidays and sessions; constraint values with
version history (saving creates version N+1, old versions can be re-activated).
Things worth checking against the SRS and the lab brief: the field lists in SRS 3.2.1, the messages for wrong data, whether CSV only is enough
(the project description also mentions extracting data from existing documents, which is **not** implemented), and the wording and layout of the pages.
If you replace a module, keep the table and column names that the timetable code reads (`app/models.py`) or update `timetable_service.build_problem()` with it.

## 7. Decisions already taken (please do not change silently)
- Regular and backlog students sit the **same** exam; backlog is only a label on the seat.
- A course may be split across several rooms (90 students in a 60-seat and a 30-seat room); each room gets its own invigilators.
- Duty limits are **per designation**, with an optional personal maximum on the faculty record; limits live in the versioned constraints.
- Data is loaded by CSV upload or by hand; no PDF or Word extraction.
- The generate button runs the solver inside the request (a few seconds for the sample data, at most the time limit set on the Constraints page).
- Publishing re-validates against today's data, so a timetable made before a data change cannot be published unless it is still valid.
- Records used by a draft or published timetable (a course, room or faculty member) cannot be deleted from the data pages.

## 8. Tests
```powershell
pytest -v          # about a minute; uses in-memory SQLite, no Docker or MySQL
```
`tests/conftest.py` gives you `app`, `client` (already logged in as admin) and `sample` (sample data loaded). `CSRF_ENABLED` is off in tests.
Each `test_TCnn_*` test is one row of the test-case document for this module and its actual output is written to `tests/test_results.json` (git-ignored).
There are 86 tests at the time of writing. Test files: `test_scheduler.py` (TC02-TC13 and conflict explanations), `test_validator.py` (TC14-TC17 and hand-edited timetables),
`test_publishing.py` (TC01, TC18-TC22, the publish / discard / archive workflow and the Faculty/Student hand-off functions), `test_csrf.py` (the form token check,
which the other tests switch off), plus the data and calendar tests.
Changes from the first prototype's test cases: TC08 now means "a course larger than all rooms together is reported" (a course bigger than one room is split, see TC08b),
and TC20 / TC21 were reworded (non-admin users cannot publish; a conflict result cannot be published).

## 9. Known limitations and open questions

Missing features (the team has to decide or build):
- **No faculty or student login yet** (section 5), and no swap workflow or admin approval screen. The state diagram says an admin approves swaps: the team has to
  decide whether that screen belongs to the Faculty Portal work or to the Admin Portal.
- **No reports or exports** (PDF / Excel): Stage 2 in the SRS, "Won't Have" in the MoSCoW table.
- **No backup / standby faculty allocation**, no attendance tracking (optional features in the project description).
- Only the Admin Portal pages are styled (plain Bootstrap 5). Faculty and Student pages should be mobile-friendly (SRS 3.1).

Behaviour to know about in the Admin Portal / solver:
- **Not deterministic:** generating twice from the same data can give two different, equally valid timetables (the solver uses 4 workers and stops when
  no better solution appears for 3 seconds). The validator still checks every result. Do not write tests that expect a fixed timetable.
- **Generation runs inside the web request.** It takes a few seconds on the sample data, but the time limit on the Constraints page can be up to 600 seconds, which a web
  server or proxy timeout may cut off. There is no cancel button and no background job. Keep the limit modest while demonstrating.
- **A time-out is reported as "no timetable within N seconds"** even if the real cause is a hard clash of rules; if a large problem times out, check the clashes first.
- **Spacing between a student's exams** is rewarded per pair of courses, not per shared student. It is a soft rule, so it never makes a timetable invalid.
- **A published timetable is not re-checked when master data changes afterwards** (for example a room's capacity is edited). Deleting a used record is blocked, but edits are
  allowed; archive and regenerate if the data changes after publishing. Publishing does re-validate against the data of that moment.
- A published timetable keeps the constraint version that was active when it was created, even though publishing validated it against the version active at that moment.
- Notifications are written when the timetable is published (one per faculty member, one per student), not when a duty is assigned in a draft. Publishing again adds
  new notifications with the same text, and old ones stay readable after a timetable is archived; show only the ones from the current publication if that matters in your portal.
- The draft detail page is slow for very large timetables (it was measured at about a second for 300 exams and 36,000 seats). Fine for course-project sizes.
- A failed generate caused by missing data is reported on screen but is not written to the audit log; a blocked publish is (action `PUBLISH_BLOCKED`).
- `get_exam_instructions()` returns the admin's text as typed. Keep Jinja autoescaping on when you show it (the admin pages do).

## 10. Working agreements
- Keep code simple and commented in plain language; the evaluation is a walk-through of the code.
- Work on your own branch (`git checkout -b feature/<your-part>`), open a pull request to `main`, and run `pytest` first.
- Commit with your own name and email (`git config user.name`, `git config user.email`). Never commit `.env` or database files (both are git-ignored).
- Update `README.md` and the tests together with the code.
