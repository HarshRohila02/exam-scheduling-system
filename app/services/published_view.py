"""Read-only views of the PUBLISHED timetable, for the Faculty and Student portals (SRS 3.2.5 - 3.2.8).

Those portals are built by other team members; they call these functions instead of querying the
timetable tables themselves, so the rule "nothing is visible until the admin publishes" lives in one place.
"""
from typing import Dict, List

from app.models import (Course, ExamSession, InvigilationDuty, Notification, Registration, SeatAssignment,
                        Timetable, TimetableEntry)
from app.scheduler.problem import NotPublishedError


def current_published() -> Timetable:
    timetable = Timetable.query.filter_by(status="PUBLISHED").first()
    if timetable is None:
        raise NotPublishedError("Examination timetable has not been published yet")
    return timetable


def _exam_details(entry, sessions, courses) -> Dict:
    session = sessions.get(entry.session_code)
    course = courses.get(entry.course_code)
    return {"course": entry.course_code, "course_name": course.name if course else entry.course_code,
            "duration_minutes": course.exam_duration_minutes if course else None,
            "date": str(entry.exam_date), "session": entry.session_code,
            "start_time": session.start_time if session else "", "end_time": session.end_time if session else ""}


def _lookups():
    return ({s.code: s for s in ExamSession.query}, {c.course_code: c for c in Course.query})


def get_student_exams(student_id: str) -> List[Dict]:
    """The student's own exams, in date order. Built from their REGISTRATIONS, so an exam is listed even when
    no seat has been assigned yet (room and seat are then None and the page says so)."""
    timetable = current_published()
    sessions, courses = _lookups()
    entries = {e.course_code: e for e in timetable.entries}
    # this student's seats only (not every seat of the timetable), keyed by exam
    seats = {s.entry_id: s for s in SeatAssignment.query.join(TimetableEntry).filter(
        TimetableEntry.timetable_id == timetable.id, SeatAssignment.student_id == student_id)}
    rows = []
    for registration in Registration.query.filter_by(student_id=student_id):
        entry = entries.get(registration.course_code)
        if entry is None:                       # registered, but the course has no slot in this timetable
            continue
        seat = seats.get(entry.id)
        row = _exam_details(entry, sessions, courses)
        course = courses.get(entry.course_code)
        row.update(room=seat.room_id if seat else None, seat=seat.seat_no if seat else None,
                   exam_type=seat.exam_type if seat else registration.exam_type,
                   instructions=(course.exam_instructions or "").strip() if course else "")
        rows.append(row)
    return sorted(rows, key=lambda r: (r["date"], r["start_time"]))


def get_faculty_duties(faculty_id: str, timetable=None) -> List[Dict]:
    """The faculty member's own invigilation duties in date order.

    `duty_id` is the InvigilationDuty row, so a duty-swap request can point at exactly one duty."""
    timetable = timetable or current_published()
    sessions, courses = _lookups()
    duties = (InvigilationDuty.query.join(TimetableEntry)
              .filter(TimetableEntry.timetable_id == timetable.id, InvigilationDuty.faculty_id == faculty_id))
    rows = []
    for duty in duties:
        row = _exam_details(duty.entry, sessions, courses)
        row.update(room=duty.room_id, duty_id=duty.id)
        rows.append(row)
    return sorted(rows, key=lambda r: (r["date"], r["start_time"]))


def get_master_timetable() -> List[Dict]:
    """Every exam with its rooms (Faculty Portal: Timetable View)."""
    timetable = current_published()
    sessions, courses = _lookups()
    rows = []
    for entry in timetable.entries:
        row = _exam_details(entry, sessions, courses)
        row["rooms"] = sorted(r.room_id for r in entry.rooms)
        rows.append(row)
    return sorted(rows, key=lambda r: (r["date"], r["start_time"], r["course"]))


def get_exam_instructions() -> str:
    return current_published().instructions or ""


def get_notifications(recipient_type: str, recipient_id: str):
    return (Notification.query.filter_by(recipient_type=recipient_type, recipient_id=recipient_id)
            .order_by(Notification.created_at.desc()).all())
