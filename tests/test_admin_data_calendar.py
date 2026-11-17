"""Harshit Kumar - Admin Portal: Data Management (SRS 3.2.1), University Calendar and Constraints (SRS 3.2.2).

The first ten tests are the ten rows of my test-case document (TC01 - TC10). Each one writes what actually
happened into tests/test_results.json (key "Harshit-TCnn") for the Actual Output column of the document.
The tests after them check the rest of the module: CSV / Excel upload, search, delete protection, calendar
history, holiday and session rules, constraint history and re-activation, and that only the admin gets in.
"""
import io
from datetime import date

import pytest

from app.extensions import db
from app.models import (AuditLog, ConstraintVersion, Course, ExamCalendar, ExamSession, Faculty,
                        FacultyUnavailability, Holiday, Registration, Room, Student)
from app.services.calendar_service import CalendarVersion
from app.services.constraint_versions import get_active_version, version_history
from app.services.timetable_service import readiness_checklist
from tests.conftest import record


# ---------------------------------------------------------------- helpers
def add_student(client, **changes):
    form = {"student_id": "S900", "name": "Riya Verma", "program": "B.Tech CSE", "section": "A",
            "semester": "5", "email": "riya@university.edu"}
    form.update(changes)
    return client.post("/data/students/add", data=form, follow_redirects=True)


def add_room(client, **changes):
    form = {"room_id": "R301", "building": "Main", "capacity": "60", "is_available": "yes"}
    form.update(changes)
    return client.post("/data/rooms/add", data=form, follow_redirects=True)


def add_faculty(client, **changes):
    form = {"faculty_id": "F50", "name": "Dr. Kavita Mehta", "designation": "Professor",
            "department": "Computer Science", "email": "", "max_duties_override": ""}
    form.update(changes)
    return client.post("/data/faculty/add", data=form, follow_redirects=True)


def constraint_form(**changes):
    form = {"students_per_invigilator": "30", "room_fill_percent": "100", "no_same_day_exams": "yes",
            "duty_balance_weight": "10", "exam_spacing_weight": "1", "solver_time_limit_s": "30", "note": "",
            "designation": ["Professor", "Associate Professor", "Assistant Professor", ""],
            "min_duties": ["0", "1", "1", ""], "max_duties": ["2", "3", "4", ""]}
    form.update(changes)
    return form


def set_period(client, start, end, skip_sundays="yes"):
    return client.post("/calendar/period", follow_redirects=True,
                       data={"start_date": start, "end_date": end, "skip_sundays": skip_sundays})


def messages(response):
    """The page text, for checking messages."""
    return response.data.decode()


# ================================================================ TC01 - TC10
def test_TC01_add_student_with_valid_data(client):
    """Unique ID, course, section, semester, type = Regular -> saved and shown in the student list."""
    page = messages(add_student(client))
    assert "Student S900 added" in page
    # the course and the type (Regular / Repeat) of the student are kept as a registration
    client.post("/data/courses/add", data={"course_code": "CS301", "name": "Software Engineering", "semester": "5",
                                           "exam_duration_minutes": "180"})
    client.post("/data/registrations/add", data={"student_id": "S900", "course_code": "CS301", "exam_type": "regular"})
    student = db.session.get(Student, "S900")
    registration = Registration.query.filter_by(student_id="S900").one()
    listing = messages(client.get("/data/students/"))
    assert student.section == "A" and student.semester == 5
    assert registration.course_code == "CS301" and registration.exam_type == "regular"
    assert "S900" in listing and "Riya Verma" in listing
    record("Harshit-TC01", "Saved; S900 Riya Verma, section A, semester 5, CS301 Regular; shown in the student list")


def test_TC02_duplicate_id_is_rejected(client):
    """A student or faculty ID that already exists -> "ID already exists", record not saved."""
    add_student(client)
    student_page = messages(add_student(client, name="Somebody Else"))
    add_faculty(client)
    faculty_page = messages(add_faculty(client, name="Dr. Other"))
    assert "Student ID S900 already exists" in student_page
    assert "Faculty ID F50 already exists" in faculty_page
    assert db.session.get(Student, "S900").name == "Riya Verma" and Student.query.count() == 1
    assert Faculty.query.count() == 1
    record("Harshit-TC02", "Messages 'Student ID S900 already exists' and 'Faculty ID F50 already exists'; "
                           "nothing saved")


def test_TC03_mandatory_fields_empty(client):
    """Room form with room ID or capacity blank -> validation message, record not saved."""
    no_id = messages(add_room(client, room_id=""))
    no_capacity = messages(add_room(client, capacity=""))
    assert "Room ID is required" in no_id
    assert "Capacity is required" in no_capacity
    assert Room.query.count() == 0
    record("Harshit-TC03", "Messages 'Room ID is required' and 'Capacity is required'; nothing saved")


def test_TC04_update_room_capacity(client):
    """Change capacity from 60 to 80 and save -> new capacity saved and shown in the room list."""
    add_room(client, capacity="60")
    page = messages(client.post("/data/rooms/R301/edit", follow_redirects=True,
                                data={"room_id": "R301", "building": "Main", "capacity": "80",
                                      "is_available": "yes"}))
    assert "Room R301 updated" in page
    assert db.session.get(Room, "R301").capacity == 80
    assert ">80<" in "".join(page.split())                       # the list cell shows 80 (spaces removed)
    record("Harshit-TC04", "Room R301 capacity 60 -> 80, saved and shown in the room list")


def test_TC05_delete_faculty(client, sample):
    """Select a faculty member and confirm delete -> removed from the faculty list."""
    assert db.session.get(Faculty, "F6") is not None
    page = messages(client.post("/data/faculty/F6/delete", follow_redirects=True))
    assert "Faculty member F6 deleted" in page
    assert db.session.get(Faculty, "F6") is None
    assert "/data/faculty/F6/edit" not in page
    record("Harshit-TC05", "F6 deleted; no longer in the faculty list")


def test_TC06_valid_exam_period_with_holidays_and_sessions(client):
    """Start date, a later end date, holidays and session timings -> calendar saved and available to the
    schedule generator."""
    set_period(client, "2026-12-07", "2026-12-19")
    client.post("/calendar/holidays", data={"date": "2026-12-11", "reason": "Foundation Day"})
    client.post("/calendar/sessions", data={"name_FN": "Forenoon", "start_FN": "09:00", "end_FN": "12:00",
                                            "name_AN": "Afternoon", "start_AN": "14:00", "end_AN": "17:00"})
    calendar = ExamCalendar.query.one()
    assert (calendar.start_date, calendar.end_date) == (date(2026, 12, 7), date(2026, 12, 19))
    assert Holiday.query.one().date == date(2026, 12, 11)
    assert db.session.get(ExamSession, "FN").start_time == "09:00"
    # what the generator gets: 13 days - Sunday 13 Dec - holiday 11 Dec = 11 days x 2 sessions
    row = [r for r in readiness_checklist() if r[0] == "Exam period and sessions"][0]
    assert row[1] is True and row[2] == "22 exam slots"
    record("Harshit-TC06", "Calendar 2026-12-07 to 2026-12-19 saved, holiday 2026-12-11, FN 09:00-12:00, "
                           "AN 14:00-17:00; generator readiness: 22 exam slots")


def test_TC07_end_date_before_start_date(client):
    """Start = 20 Dec, End = 10 Dec -> error message, calendar not saved."""
    page = messages(set_period(client, "2026-12-20", "2026-12-10"))
    assert "The end date cannot be before the start date." in page
    assert ExamCalendar.query.count() == 0 and CalendarVersion.query.count() == 0
    record("Harshit-TC07", "Message 'The end date cannot be before the start date.'; calendar not saved")


def test_TC08_save_constraint_values(client):
    """Ratio = 30, max duties = 5 -> constraints saved as the active configuration."""
    page = messages(client.post("/constraints/save", follow_redirects=True,
                                data=constraint_form(max_duties=["5", "5", "5", ""])))
    active = get_active_version()
    assert "Saved as version 2" in page and "active configuration" in page
    assert active.version_no == 2 and active.students_per_invigilator == 30
    assert [l.max_duties for l in active.limits] == [5, 5, 5]
    record("Harshit-TC08", "Saved as version 2 (active): 30 students per invigilator, max 5 duties")


def test_TC09_constraint_version_history(client):
    """Edit the ratio from 30 to 25 and save -> new version stored, old version still visible in the history."""
    client.post("/constraints/save", data=constraint_form(students_per_invigilator="25", note="University rule"))
    old = ConstraintVersion.query.filter_by(version_no=1).one()
    new = ConstraintVersion.query.filter_by(version_no=2).one()
    page = messages(client.get("/constraints/"))
    assert (old.students_per_invigilator, old.is_active) == (30, False)
    assert (new.students_per_invigilator, new.is_active) == (25, True)
    assert "v1" in page and "v2" in page and "1:30" in page and "1:25" in page
    assert "Students per invigilator: 30 -&gt; 25" in page
    record("Harshit-TC09", "Version 2 (ratio 25) active; version 1 (ratio 30) still in the history; "
                           "history says 'Students per invigilator: 30 -> 25'")


def test_TC10_invalid_constraint_value(client):
    """Negative or non-numeric ratio -> validation message, value not saved."""
    negative = messages(client.post("/constraints/save", follow_redirects=True,
                                    data=constraint_form(students_per_invigilator="-5")))
    text = messages(client.post("/constraints/save", follow_redirects=True,
                                data=constraint_form(students_per_invigilator="abc")))
    assert "Students per invigilator must be between 1 and 200" in negative
    assert "Students per invigilator must be a whole number" in text
    assert ConstraintVersion.query.count() == 1 and get_active_version().students_per_invigilator == 30
    record("Harshit-TC10", "'-5': must be between 1 and 200; 'abc': must be a whole number; nothing saved")


# ================================================================ more checks of the module
# ---- access (SRS 3.6: only examination staff)
def test_pages_need_an_admin_login(app):
    visitor = app.test_client()
    for url in ("/data/students/", "/calendar/", "/constraints/"):
        response = visitor.get(url)
        assert response.status_code == 302 and "/login" in response.headers["Location"]


# ---- data management
def test_wrong_values_get_clear_messages(client):
    assert "Semester must be between 1 and 8" in messages(add_student(client, semester="9"))
    assert "Semester must be a whole number" in messages(add_student(client, semester="five"))
    assert "is not a valid email address" in messages(add_student(client, email="riya@"))
    assert "may contain only letters" in messages(add_student(client, student_id="S 9!"))
    assert Student.query.count() == 0


def test_form_keeps_what_was_typed_after_a_mistake(client):
    page = messages(add_student(client, semester="9", name="Typed Name"))
    assert 'value="Typed Name"' in page


def test_edit_cannot_change_the_id(client):
    add_student(client)
    client.post("/data/students/S900/edit", data={"student_id": "OTHER", "name": "Riya V", "program": "B.Tech CSE",
                                                  "section": "B", "semester": "6", "email": ""})
    student = db.session.get(Student, "S900")
    assert (student.name, student.section, student.semester) == ("Riya V", "B", 6)
    assert db.session.get(Student, "OTHER") is None


def test_repeat_is_stored_as_backlog(client):
    """The SRS says "regular or repeat"; the timetable code calls a repeat exam "backlog"."""
    add_student(client)
    client.post("/data/courses/add", data={"course_code": "MA201", "name": "Probability", "semester": "3",
                                           "exam_duration_minutes": ""})            # empty -> 180 minutes
    client.post("/data/registrations/add", data={"student_id": "S900", "course_code": "MA201", "exam_type": "repeat"})
    assert Registration.query.filter_by(student_id="S900", course_code="MA201").one().exam_type == "backlog"
    assert db.session.get(Course, "MA201").exam_duration_minutes == 180


def test_registration_needs_existing_student_and_course(client, sample):
    page = messages(client.post("/data/registrations/add", follow_redirects=True,
                                data={"student_id": "NOBODY", "course_code": "XX999", "exam_type": "regular"}))
    assert "Student NOBODY does not exist" in page and "Course XX999 does not exist" in page


def test_faculty_designation_must_have_duty_limits(client):
    page = messages(add_faculty(client, designation="Lecturer"))
    assert "has no duty limits in the active constraints" in page
    assert Faculty.query.count() == 0


def test_search(client, sample):
    page = messages(client.get("/data/students/?q=Aarav"))
    assert "Aarav Sharma" in page and "Diya Sharma" not in page


def test_deleting_a_student_also_deletes_their_registrations(client, sample):
    assert Registration.query.filter_by(student_id="S001").count() == 2
    client.post("/data/students/S001/delete")
    assert db.session.get(Student, "S001") is None
    assert Registration.query.filter_by(student_id="S001").count() == 0


def test_every_change_is_in_the_audit_log(client):
    add_room(client)
    client.post("/data/rooms/R301/edit", data={"room_id": "R301", "building": "Main", "capacity": "70",
                                               "is_available": "no"})
    client.post("/data/rooms/R301/delete")
    actions = [a.action for a in AuditLog.query.order_by(AuditLog.id)]
    assert ["CREATE", "UPDATE", "DELETE"] == [a for a in actions if a in ("CREATE", "UPDATE", "DELETE")]


def test_faculty_unavailable_dates(client, sample):
    client.post("/data/faculty/F1/unavailability", data={"date": "2026-11-03", "session_code": "FN"})
    client.post("/data/faculty/F1/unavailability", data={"date": "2026-11-04", "session_code": ""})
    again = messages(client.post("/data/faculty/F1/unavailability", follow_redirects=True,
                                 data={"date": "2026-11-04", "session_code": ""}))
    assert "already in the list" in again
    entries = FacultyUnavailability.query.filter_by(faculty_id="F1").all()
    assert sorted((str(e.date), e.session_code) for e in entries) == [("2026-11-03", "FN"), ("2026-11-04", None)]


# ---- CSV and Excel upload
def upload(client, entity, content, filename):
    return messages(client.post(f"/data/{entity}/upload", follow_redirects=True, content_type="multipart/form-data",
                                data={"file": (io.BytesIO(content), filename)}))


def test_csv_upload(client):
    page = upload(client, "rooms", b"room_id,building,capacity,is_available\nA1,North,40,yes\nA2,North,25,no\n",
                  "rooms.csv")
    assert "2 rooms imported" in page
    assert db.session.get(Room, "A2").is_available is False


def test_csv_upload_with_mistakes_saves_nothing(client):
    page = upload(client, "rooms", b"room_id,building,capacity,is_available\n"
                                   b"A1,North,40,yes\n"
                                   b"A1,North,30,yes\n"          # same ID twice in the file
                                   b"A3,North,lots,yes\n"        # capacity is not a number
                                   b",North,30,yes\n",           # no ID
                  "rooms.csv")
    assert "Nothing was saved" in page
    assert "Row 3: Room ID A1 already exists" in page
    assert "Row 4: Capacity must be a whole number" in page
    assert "Row 5: Room ID is required" in page
    assert Room.query.count() == 0


def test_upload_checks_columns_and_file_type(client):
    assert "Missing column(s): capacity" in upload(client, "rooms", b"room_id,building\nA1,North\n", "rooms.csv")
    assert "Only .csv and .xlsx files" in upload(client, "rooms", b"x", "rooms.xls")
    assert "no data rows" in upload(client, "rooms", b"room_id,building,capacity,is_available\n", "rooms.csv")


def excel_bytes(rows):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    data = io.BytesIO()
    book.save(data)
    return data.getvalue()


def test_excel_upload(client):
    content = excel_bytes([["room_id", "building", "capacity", "is_available"],
                           ["X1", "North", 40, "yes"],           # a number cell
                           ["X2", "North", 25, False]])          # a TRUE/FALSE cell
    assert "2 rooms imported" in upload(client, "rooms", content, "rooms.xlsx")
    assert db.session.get(Room, "X1").capacity == 40 and db.session.get(Room, "X2").is_available is False


def test_excel_upload_uses_the_same_checks(client):
    content = excel_bytes([["room_id", "building", "capacity", "is_available"],
                           ["X1", "North", 40, "yes"], ["X1", "North", 30, "yes"]])
    page = upload(client, "rooms", content, "rooms.xlsx")
    assert "Row 3: Room ID X1 already exists" in page and Room.query.count() == 0


def test_template_download(client):
    response = client.get("/data/students/template.csv")
    assert response.data.decode().splitlines()[0] == "student_id,name,program,section,semester,email"


# ---- calendar
def test_every_calendar_change_is_kept_as_a_version(client):
    set_period(client, "2026-12-01", "2026-12-10")
    client.post("/calendar/holidays", data={"date": "2026-12-04", "reason": "Festival"})
    client.post("/calendar/sessions/new", data={"code": "EV", "name": "Evening", "start": "17:30", "end": "19:30"})
    client.post(f"/calendar/holidays/{Holiday.query.one().id}/delete")
    versions = CalendarVersion.query.order_by(CalendarVersion.version_no).all()
    assert [v.version_no for v in versions] == [1, 2, 3, 4]
    assert versions[1].holidays == "2026-12-04 (Festival)"
    assert "EV 17:30-19:30" in versions[2].sessions
    assert versions[3].holidays == "" and versions[3].created_by == "admin"
    assert "Holiday removed: 2026-12-04" in messages(client.get("/calendar/"))


def test_holiday_outside_the_exam_period_is_refused(client):
    set_period(client, "2026-12-01", "2026-12-10")
    page = messages(client.post("/calendar/holidays", data={"date": "2026-12-25"}, follow_redirects=True))
    assert "outside the exam period" in page and Holiday.query.count() == 0


def test_same_holiday_twice_is_refused(client):
    client.post("/calendar/holidays", data={"date": "2026-12-04"})
    page = messages(client.post("/calendar/holidays", data={"date": "2026-12-04"}, follow_redirects=True))
    assert "already a holiday" in page and Holiday.query.count() == 1


def test_overlapping_sessions_are_refused(client):
    # the default sessions are FN 09:30-12:30 and AN 14:00-17:00
    page = messages(client.post("/calendar/sessions/new", follow_redirects=True,
                                data={"code": "MID", "name": "Midday", "start": "12:00", "end": "15:00"}))
    assert "overlap" in page and db.session.get(ExamSession, "MID") is None
    changed = messages(client.post("/calendar/sessions", follow_redirects=True,
                                   data={"name_FN": "Forenoon", "start_FN": "09:30", "end_FN": "15:00",
                                         "name_AN": "Afternoon", "start_AN": "14:00", "end_AN": "17:00"}))
    assert "overlap" in changed and db.session.get(ExamSession, "FN").end_time == "12:30"
    # sessions that only touch are fine
    client.post("/calendar/sessions/new", data={"code": "EV", "name": "Evening", "start": "17:00", "end": "19:00"})
    assert db.session.get(ExamSession, "EV") is not None


def test_session_times_are_checked(client):
    assert "must end after it starts" in messages(client.post(
        "/calendar/sessions/new", follow_redirects=True, data={"code": "EV", "name": "E", "start": "19:00", "end": "18:00"}))
    assert "like 09:30" in messages(client.post(
        "/calendar/sessions/new", follow_redirects=True, data={"code": "EV", "name": "E", "start": "7pm", "end": "9pm"}))


def test_last_session_cannot_be_removed(client):
    client.post("/calendar/sessions/AN/delete")
    page = messages(client.post("/calendar/sessions/FN/delete", follow_redirects=True))
    assert "At least one session must remain" in page and ExamSession.query.count() == 1


def test_calendar_preview_shows_holidays_and_sundays(client):
    set_period(client, "2026-11-02", "2026-11-08")
    client.post("/calendar/holidays", data={"date": "2026-11-04", "reason": "Diwali"})
    page = messages(client.get("/calendar/"))
    assert "Holiday: Diwali" in page and "Sunday" in page and "5 exam days" in page and "10 exam slots" in page


# ---- constraints
def test_older_version_can_be_made_active_again(client):
    client.post("/constraints/save", data=constraint_form(students_per_invigilator="25"))
    first = ConstraintVersion.query.filter_by(version_no=1).one()
    client.post(f"/constraints/{first.id}/activate")
    assert get_active_version().version_no == 1
    assert ConstraintVersion.query.count() == 2                    # nothing was deleted


def test_history_says_what_changed_in_duty_limits(client):
    client.post("/constraints/save", data=constraint_form(max_duties=["2", "3", "6", ""]))
    newest, changes = version_history()[0]
    assert newest.is_active and changes == ["Assistant Professor duties: 1-4 -> 1-6"]
    assert version_history()[-1][1] == ["First version"]


def test_duty_limit_rows_are_checked(client, sample):
    wrong = messages(client.post("/constraints/save", follow_redirects=True,
                                 data=constraint_form(min_duties=["3", "1", "1", ""])))
    assert "the minimum cannot be above the maximum" in wrong
    dropped = messages(client.post("/constraints/save", follow_redirects=True, data=constraint_form(
        designation=["Professor", "Associate Professor", ""], min_duties=["0", "1", ""], max_duties=["2", "3", ""])))
    assert "Assistant Professor is used by faculty records" in dropped
    assert ConstraintVersion.query.count() == 1


def test_constraint_form_keeps_what_was_typed(client):
    page = messages(client.post("/constraints/save", follow_redirects=True,
                                data=constraint_form(students_per_invigilator="abc", room_fill_percent="55")))
    assert 'value="55"' in page and 'value="abc"' in page
