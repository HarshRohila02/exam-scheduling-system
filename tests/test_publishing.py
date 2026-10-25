"""SRS 3.2.3 - generate a draft from the database, validate it, publish it, and the published views
that the Faculty and Student portals read. Test-case IDs match the test-case document."""
from datetime import date

import pytest

from app.extensions import db
from app.models import (AuditLog, ExamCalendar, ExamRoom, Faculty, FacultyUnavailability, Notification,
                        Room, Timetable)
from app.scheduler.problem import InputValidationError, NotPublishedError
from app.services.published_view import get_faculty_duties, get_master_timetable, get_student_exams
from app.services.timetable_service import build_problem, generate_timetable, publish_timetable
from tests.conftest import record


def draft(client):
    client.post("/timetable/generate")
    return Timetable.query.order_by(Timetable.id.desc()).first()


def test_TC01_generate_draft_with_valid_data(client, sample):
    response = client.post("/timetable/generate", follow_redirects=True)
    timetable = Timetable.query.one()
    placed = ", ".join(f"{e.course_code}->{e.exam_date:%d-%b} {e.session_code}/"
                       + "+".join(r.room_id for r in e.rooms) for e in timetable.entries)
    record("TC01", f"{len(timetable.entries)}/5 courses scheduled, status={timetable.status}: {placed}")
    assert b"Draft timetable generated" in response.data
    assert timetable.status == "DRAFT" and len(timetable.entries) == 5


def test_generation_without_data_gives_a_clear_message(client):
    response = client.post("/timetable/generate", follow_redirects=True)
    assert b"Set the exam period on the University Calendar page first" in response.data
    assert Timetable.query.count() == 0


def test_generation_with_no_courses_is_rejected(client, app):
    db.session.add(ExamCalendar(start_date=date(2026, 11, 2), end_date=date(2026, 11, 7)))
    db.session.commit()
    with pytest.raises(InputValidationError, match="No courses found"):
        generate_timetable("admin")


def test_new_generation_replaces_the_old_draft(client, sample):
    first = draft(client)
    second = draft(client)
    assert first.id != second.id
    assert Timetable.query.filter_by(status="DRAFT").count() == 1


def test_infeasible_data_saves_a_conflict_report(client, sample):
    calendar = ExamCalendar.query.first()
    calendar.end_date = calendar.start_date                      # a single day: far too short
    db.session.commit()
    page = client.post("/timetable/generate", follow_redirects=True).data
    timetable = Timetable.query.one()
    assert timetable.status == "CONFLICT" and not timetable.entries
    assert b"No feasible timetable" in page and b"Student clashes" in page


def test_whole_day_unavailability_blocks_every_session(sample):
    db.session.add(FacultyUnavailability(faculty_id="F1", date=date(2026, 11, 3), session_code=None))
    db.session.commit()
    f1 = next(f for f in build_problem().faculty if f.faculty_id == "F1")
    assert {(date(2026, 11, 3), "FN"), (date(2026, 11, 3), "AN")} <= f1.unavailable


def test_designation_limits_and_personal_override_reach_the_solver(sample):
    db.session.get(Faculty, "F1").max_duties_override = 1
    db.session.commit()
    problem = {f.faculty_id: f for f in build_problem().faculty}
    assert problem["F1"].max_duties == 1                        # personal limit beats the designation limit (2)
    assert problem["F3"].max_duties == 3 and problem["F3"].min_duties == 1


# =========================================================== F5: publishing
def test_TC18_admin_publishes_valid_draft(client, sample):
    timetable = draft(client)
    client.post(f"/timetable/{timetable.id}/publish", data={"instructions": "Carry your ID card."})
    assert db.session.get(Timetable, timetable.id).status == "PUBLISHED"
    student = get_student_exams("S001")
    duties = get_faculty_duties("F1")
    master = get_master_timetable()
    record("TC18", f"status=PUBLISHED; student S001 sees {len(student)} exams, e.g. {student[0]}; "
                   f"F1 sees {len(duties)} duties; master timetable has {len(master)} exams")
    assert {x["course"] for x in student} == {"CS301", "MA201"}
    assert all(x["room"] and x["seat"] for x in student)
    assert len(master) == 5


def test_publishing_notifies_faculty_and_students(client, sample):
    timetable = draft(client)
    client.post(f"/timetable/{timetable.id}/publish")
    assert Notification.query.filter_by(recipient_type="FACULTY").count() == 6
    assert Notification.query.filter_by(recipient_type="STUDENT", recipient_id="S001").count() == 1


def test_TC19_timetable_hidden_before_publishing(client, sample):
    draft(client)
    with pytest.raises(NotPublishedError) as error:
        get_student_exams("S001")
    with pytest.raises(NotPublishedError):
        get_faculty_duties("F1")
    record("TC19", f"status=DRAFT; NotPublishedError: {error.value}")


def test_TC20_only_admin_can_publish(client, sample, app):
    timetable = draft(client)
    anonymous = app.test_client()
    response = anonymous.post(f"/timetable/{timetable.id}/publish")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    faculty = app.test_client()
    with faculty.session_transaction() as session:
        session["username"], session["role"] = "F1", "FACULTY"
    assert "/login" in faculty.post(f"/timetable/{timetable.id}/publish").headers["Location"]
    record("TC20", "Anonymous and FACULTY users are redirected to login; status stays DRAFT")
    assert db.session.get(Timetable, timetable.id).status == "DRAFT"


def test_TC21_cannot_publish_without_a_valid_draft(client, sample):
    calendar = ExamCalendar.query.first()
    calendar.end_date = calendar.start_date
    db.session.commit()
    conflict = draft(client)                                     # only a CONFLICT result exists
    with pytest.raises(ValueError) as error:
        publish_timetable(conflict, "")
    record("TC21", f"ValueError: {error.value}; status={conflict.status}")
    assert "generate one first" in str(error.value)
    assert conflict.status == "CONFLICT"


def test_TC22_invalid_draft_cannot_be_published(client, sample):
    timetable = draft(client)
    cs301 = next(e for e in timetable.entries if e.course_code == "CS301")
    db.session.query(ExamRoom).filter_by(entry_id=cs301.id).update({"room_id": "R201"})   # 60 students, 30 seats
    db.session.commit()
    page = client.post(f"/timetable/{timetable.id}/publish", follow_redirects=True).data
    status = db.session.get(Timetable, timetable.id).status
    record("TC22", f"Publishing blocked: 'Cannot publish: validation failed'; status={status}")
    assert b"Cannot publish: validation failed" in page and b"over capacity" in page
    assert status == "DRAFT"


def test_data_changed_after_draft_blocks_publishing(client, sample):
    timetable = draft(client)
    room = next(iter(timetable.entries[0].rooms)).room_id
    db.session.get(Room, room).capacity = 5                      # the room shrank after the draft was made
    db.session.commit()
    page = client.post(f"/timetable/{timetable.id}/publish", follow_redirects=True).data
    assert b"Cannot publish" in page
    assert db.session.get(Timetable, timetable.id).status == "DRAFT"


def test_publishing_a_new_timetable_archives_the_old_one(client, sample):
    first = draft(client)
    client.post(f"/timetable/{first.id}/publish")
    second = draft(client)
    client.post(f"/timetable/{second.id}/publish")
    assert db.session.get(Timetable, first.id).status == "ARCHIVED"
    assert db.session.get(Timetable, second.id).status == "PUBLISHED"


def test_archived_timetable_is_hidden_again(client, sample):
    timetable = draft(client)
    client.post(f"/timetable/{timetable.id}/publish")
    client.post(f"/timetable/{timetable.id}/archive")
    with pytest.raises(NotPublishedError):
        get_student_exams("S001")


def test_records_used_by_a_live_timetable_cannot_be_deleted(client, sample):
    draft(client)
    assert b"a draft or published timetable uses it" in client.post("/data/students/S001/delete", follow_redirects=True).data
    assert b"a draft or published timetable uses it" in client.post("/data/courses/CS301/delete", follow_redirects=True).data
    assert b"a draft or published timetable uses it" in client.post("/data/rooms/R101/delete", follow_redirects=True).data


def test_schedule_actions_are_audit_logged(client, sample):
    timetable = draft(client)
    client.post(f"/timetable/{timetable.id}/publish")
    actions = {log.action for log in AuditLog.query}
    assert {"GENERATE_TIMETABLE", "PUBLISH_TIMETABLE", "LOGIN"} <= actions


def test_pages_render(client, sample):
    timetable = draft(client)
    for url in ("/", "/timetable/", f"/timetable/{timetable.id}", "/audit/", "/constraints/", "/calendar/",
                "/data/students/", "/data/registrations/", "/data/faculty/F1/unavailability"):
        assert client.get(url).status_code == 200, url
