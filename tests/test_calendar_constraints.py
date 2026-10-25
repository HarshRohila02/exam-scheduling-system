"""SRS 3.2.2 - University Calendar and versioned Constraints."""
from datetime import date

from app.extensions import db
from app.models import AuditLog, ConstraintVersion, ExamCalendar, ExamSession, Holiday
from app.scheduler.problem import build_slots
from app.services.constraint_versions import get_active_version


def constraint_form(**overrides):
    data = {"students_per_invigilator": "30", "room_fill_percent": "100", "no_same_day_exams": "yes",
            "duty_balance_weight": "10", "exam_spacing_weight": "1", "solver_time_limit_s": "30",
            "note": "test change",
            "designation": ["Professor", "Associate Professor", "Assistant Professor", "", ""],
            "min_duties": ["0", "1", "1", "", ""], "max_duties": ["2", "3", "4", "", ""]}
    data.update(overrides)
    return data


# ---------------------------------------------------------------- calendar
def test_slots_skip_holidays_and_sundays():
    slots = build_slots(date(2026, 11, 2), date(2026, 11, 8), ["FN", "AN"],
                        {date(2026, 11, 2), date(2026, 11, 3)}, skip_sundays=True)
    days = sorted({d for d, _ in slots})
    assert days == [date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)]
    assert len(slots) == 8                                   # 4 days x 2 sessions


def test_save_exam_period(client):
    client.post("/calendar/period", data={"start_date": "2026-12-01", "end_date": "2026-12-10",
                                          "skip_sundays": "yes"})
    calendar = ExamCalendar.query.first()
    assert (calendar.start_date, calendar.end_date) == (date(2026, 12, 1), date(2026, 12, 10))


def test_period_end_before_start_is_rejected(client):
    response = client.post("/calendar/period", follow_redirects=True,
                           data={"start_date": "2026-12-10", "end_date": "2026-12-01", "skip_sundays": "yes"})
    assert b"end date cannot be before the start date" in response.data
    assert ExamCalendar.query.count() == 0


def test_add_and_remove_holiday(client):
    client.post("/calendar/holidays/add", data={"date": "2026-11-04", "reason": "Diwali"})
    assert Holiday.query.filter_by(date=date(2026, 11, 4)).count() == 1
    again = client.post("/calendar/holidays/add", data={"date": "2026-11-04"}, follow_redirects=True)
    assert b"already a holiday" in again.data
    client.post(f"/calendar/holidays/{Holiday.query.first().id}/delete")
    assert Holiday.query.count() == 0


def test_session_times_are_validated(client):
    bad = client.post("/calendar/sessions/add", follow_redirects=True,
                      data={"code": "EV", "name": "Evening", "start": "18:00", "end": "17:00"})
    assert b"must end after it starts" in bad.data
    client.post("/calendar/sessions/add", data={"code": "EV", "name": "Evening", "start": "18:00", "end": "20:00"})
    assert db.session.get(ExamSession, "EV") is not None


def test_last_session_cannot_be_removed(client):
    client.post("/calendar/sessions/AN/delete")
    response = client.post("/calendar/sessions/FN/delete", follow_redirects=True)
    assert b"At least one session must remain" in response.data
    assert ExamSession.query.count() == 1


def test_calendar_page_shows_preview(client, sample):
    page = client.get("/calendar/").data
    assert b"exam days" in page and b"Mon, 02 Nov 2026" in page


# ---------------------------------------------------------------- constraints
def test_default_version_is_active(app):
    version = get_active_version()
    assert version.version_no == 1 and version.students_per_invigilator == 30
    assert {l.designation for l in version.limits} == {"Professor", "Associate Professor", "Assistant Professor"}


def test_saving_creates_new_version_and_keeps_old(client):
    client.post("/constraints/save", data=constraint_form(students_per_invigilator="25"))
    versions = ConstraintVersion.query.order_by(ConstraintVersion.version_no).all()
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[0].students_per_invigilator == 30 and not versions[0].is_active    # old one untouched
    assert versions[1].students_per_invigilator == 25 and versions[1].is_active
    assert versions[1].note == "test change"


def test_activate_old_version(client):
    client.post("/constraints/save", data=constraint_form(students_per_invigilator="25"))
    v1 = ConstraintVersion.query.filter_by(version_no=1).first()
    client.post(f"/constraints/{v1.id}/activate")
    assert get_active_version().version_no == 1


def test_invalid_constraint_values_rejected(client):
    response = client.post("/constraints/save", follow_redirects=True,
                           data=constraint_form(students_per_invigilator="0",
                                                min_duties=["3", "1", "1", "", ""]))
    assert b"Students per invigilator must be between 1 and 200" in response.data
    assert b"need 0 &lt;= minimum &lt;= maximum" in response.data
    assert ConstraintVersion.query.count() == 1


def test_cannot_drop_designation_used_by_faculty(client, sample):
    response = client.post("/constraints/save", follow_redirects=True, data=constraint_form(
        designation=["Professor", "Associate Professor", "", "", ""],
        min_duties=["0", "1", "", "", ""], max_duties=["2", "3", "", "", ""]))
    assert b"Assistant Professor" in response.data and b"must keep its duty limits" in response.data
    assert ConstraintVersion.query.count() == 1


def test_constraint_changes_are_audit_logged(client):
    client.post("/constraints/save", data=constraint_form())
    assert AuditLog.query.filter_by(action="SAVE_CONSTRAINTS").count() == 1
