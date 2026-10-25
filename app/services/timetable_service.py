"""Glue between the database and the scheduler (SRS 3.2.3).

    generate_timetable()  database rows -> scheduler -> validator -> saved as DRAFT (or CONFLICT)
    publish_timetable()   re-validates the draft against today's data, then makes it PUBLISHED

Status flow:  DRAFT -> PUBLISHED -> ARCHIVED     (CONFLICT = the solver found no feasible schedule)
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

from app.auth import log_action
from app.extensions import db
from app.models import (Course, ExamCalendar, ExamRoom, ExamSession, Faculty, FacultyUnavailability, Holiday,
                        InvigilationDuty, Notification, Registration, Room, SeatAssignment, Timetable,
                        TimetableEntry)
from app.scheduler.problem import (ExamInput, ExamPlacement, FacultyInput, InfeasibleScheduleError,
                                   InputValidationError, RoomInput, Rules, SessionInput, Slot, build_slots)
from app.scheduler.solver import solve
from app.scheduler.validator import validate_timetable
from app.services.constraint_versions import get_active_version


@dataclass
class Problem:
    """Everything the scheduler needs, read from the database at one moment."""
    exams: List[ExamInput]
    rooms: List[RoomInput]
    faculty: List[FacultyInput]
    slots: List[Slot]
    sessions: Dict[str, SessionInput]
    rules: Rules
    version_id: int
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- database -> scheduler input
def build_problem() -> Problem:
    calendar = ExamCalendar.query.first()
    if calendar is None:
        raise InputValidationError("Set the exam period on the University Calendar page first")
    version = get_active_version()
    if version is None:
        raise InputValidationError("There is no active constraint version")

    sessions = {s.code: SessionInput(s.code, s.start_time, s.end_time)
                for s in ExamSession.query.order_by(ExamSession.start_time)}
    holidays = {h.date for h in Holiday.query}
    slots = build_slots(calendar.start_date, calendar.end_date, list(sessions), holidays, calendar.skip_sundays)

    notes, exams = [], []
    students_of = {}
    for reg in Registration.query:
        students_of.setdefault(reg.course_code, {})[reg.student_id] = reg.exam_type
    for course in Course.query.order_by(Course.course_code):
        students = students_of.get(course.course_code)
        if students:
            exams.append(ExamInput(course.course_code, course.name, course.exam_duration_minutes, students))
        else:
            notes.append(f"{course.course_code} has no registered students and was not scheduled")

    rooms = [RoomInput(r.room_id, r.capacity) for r in Room.query.filter_by(is_available=True).order_by(Room.room_id)]

    slot_set = set(slots)
    unavailable = {}
    for row in FacultyUnavailability.query:
        codes = [row.session_code] if row.session_code else list(sessions)
        unavailable.setdefault(row.faculty_id, set()).update((row.date, c) for c in codes if (row.date, c) in slot_set)
    faculty, missing = [], set()
    for person in Faculty.query.order_by(Faculty.faculty_id):
        limit = version.limit_for(person.designation)
        if limit is None:
            missing.add(person.designation)
            continue
        most = person.max_duties_override or limit.max_duties
        faculty.append(FacultyInput(person.faculty_id, person.name, min(limit.min_duties, most), most,
                                    unavailable.get(person.faculty_id, set())))
    if missing:
        raise InputValidationError("These designations have no duty limits in the active constraints: "
                                   + ", ".join(sorted(missing)))

    rules = Rules(version.students_per_invigilator, version.no_same_day_exams, version.room_fill_percent,
                  version.duty_balance_weight, version.exam_spacing_weight, version.solver_time_limit_s)
    return Problem(exams, rooms, faculty, slots, sessions, rules, version.id, notes)


def readiness_checklist():
    """What must exist before Generate Timetable can run. Returns [(label, ok, detail, endpoint)]."""
    calendar = ExamCalendar.query.first()
    version = get_active_version()
    registered = db.session.query(Registration.course_code).distinct().count()
    holidays = {h.date for h in Holiday.query}
    codes = [s.code for s in ExamSession.query]
    slot_count = len(build_slots(calendar.start_date, calendar.end_date, codes, holidays,
                                 calendar.skip_sundays)) if calendar else 0
    designations = {d for (d,) in db.session.query(Faculty.designation).distinct()}
    missing = sorted(designations - {l.designation for l in version.limits}) if version else sorted(designations)
    rooms = Room.query.filter_by(is_available=True).count()
    faculty = Faculty.query.count()
    return [
        ("Courses with registered students", registered > 0, f"{registered} courses", "data.list_view", "courses"),
        ("Available rooms", rooms > 0, f"{rooms} rooms", "data.list_view", "rooms"),
        ("Faculty for invigilation", faculty > 0 and not missing,
         f"{faculty} faculty" + (f"; no duty limits for {', '.join(missing)}" if missing else ""),
         "data.list_view", "faculty"),
        ("Exam period and sessions", slot_count > 0, f"{slot_count} exam slots", "calendar.index", None),
        ("Active constraint version", version is not None,
         f"version {version.version_no}" if version else "none", "constraints.index", None),
    ]


# ---------------------------------------------------------------- scheduler output <-> database rows
def _save_placements(timetable: Timetable, placements: List[ExamPlacement], problem: Problem) -> None:
    type_of = {e.code: e.students for e in problem.exams}
    for p in placements:
        entry = TimetableEntry(course_code=p.course_code, exam_date=p.exam_date, session_code=p.session)
        for room_id, count in p.rooms.items():
            entry.rooms.append(ExamRoom(room_id=room_id, students_count=count))
            for student_id, seat_no in p.seats[room_id].items():
                entry.seats.append(SeatAssignment(room_id=room_id, student_id=student_id, seat_no=seat_no,
                                                  exam_type=type_of[p.course_code][student_id]))
            for faculty_id in p.invigilators.get(room_id, []):
                entry.duties.append(InvigilationDuty(room_id=room_id, faculty_id=faculty_id))
        timetable.entries.append(entry)


def placements_from_db(timetable: Timetable) -> List[ExamPlacement]:
    placements = []
    for entry in timetable.entries:
        p = ExamPlacement(entry.course_code, entry.exam_date, entry.session_code)
        for room in entry.rooms:
            p.rooms[room.room_id] = room.students_count
        for seat in entry.seats:                                  # setdefault: a hand-edited draft may be
            p.seats.setdefault(seat.room_id, {})[seat.student_id] = seat.seat_no   # inconsistent; the
        for duty in entry.duties:                                 # validator reports it instead of crashing
            p.invigilators.setdefault(duty.room_id, []).append(duty.faculty_id)
        placements.append(p)
    return placements


def _validate(problem: Problem, placements: List[ExamPlacement]) -> List[str]:
    return validate_timetable(placements, problem.exams, problem.rooms, problem.faculty, problem.slots, problem.rules)


def _set_report(timetable: Timetable, **report) -> None:
    timetable.report = json.dumps(report)


def get_report(timetable: Timetable) -> dict:
    return json.loads(timetable.report) if timetable.report else {}


# ---------------------------------------------------------------- generate
def generate_timetable(username: str) -> Timetable:
    """Run the solver and save the result. Raises InputValidationError when data is missing.

    A previous DRAFT or CONFLICT result is replaced; a PUBLISHED timetable is left untouched.
    """
    problem = build_problem()
    for old in Timetable.query.filter(Timetable.status.in_(["DRAFT", "CONFLICT"])).all():
        db.session.delete(old)

    timetable = Timetable(constraint_version_id=problem.version_id, created_by=username)
    try:
        result = solve(problem.exams, problem.rooms, problem.faculty, problem.slots, problem.sessions, problem.rules)
        violations = _validate(problem, result.placements)       # R8: the validator has the last word
        timetable.solver_status, timetable.solve_seconds = result.status, result.solve_seconds
        if violations:
            timetable.status = "CONFLICT"
            _set_report(timetable, message="The solver's answer failed independent validation",
                        conflicts=violations, violations=violations, notes=problem.notes)
        else:
            timetable.status = "DRAFT"
            _save_placements(timetable, result.placements, problem)
            _set_report(timetable, message="", conflicts=[], violations=[], notes=problem.notes)
    except InfeasibleScheduleError as error:
        timetable.status, timetable.solver_status = "CONFLICT", "INFEASIBLE"
        _set_report(timetable, message=str(error), conflicts=error.conflicts, violations=[], notes=problem.notes)

    db.session.add(timetable)
    log_action("GENERATE_TIMETABLE", f"Result: {timetable.status} ({timetable.solver_status}) using "
                                      f"constraint version {problem.version_id}")
    db.session.commit()
    return timetable


# ---------------------------------------------------------------- publish / archive
def publish_timetable(timetable: Timetable, instructions: str) -> Timetable:
    """Make a DRAFT visible to faculty and students. Raises ValueError with the reason if it cannot."""
    if timetable.status == "PUBLISHED":
        raise ValueError("This timetable is already published")
    if timetable.status != "DRAFT":
        raise ValueError("Only a valid draft timetable can be published; generate one first")

    problem = build_problem()                                     # check against today's data
    violations = _validate(problem, placements_from_db(timetable))
    if violations:
        _set_report(timetable, message="Validation failed at publishing", conflicts=[], violations=violations,
                    notes=problem.notes)
        db.session.commit()
        raise ValueError("Cannot publish: validation failed - " + "; ".join(violations[:5])
                         + (f" (and {len(violations) - 5} more)" if len(violations) > 5 else ""))

    for old in Timetable.query.filter_by(status="PUBLISHED").all():
        old.status = "ARCHIVED"
    timetable.status, timetable.published_at = "PUBLISHED", datetime.now()
    timetable.instructions = instructions.strip()
    _notify(timetable)
    log_action("PUBLISH_TIMETABLE", f"Timetable {timetable.id} published")
    db.session.commit()
    return timetable


def _notify(timetable: Timetable) -> None:
    """Write the in-portal notifications that the Faculty and Student portals display."""
    duties = {}
    students = set()
    for entry in timetable.entries:
        for duty in entry.duties:
            duties[duty.faculty_id] = duties.get(duty.faculty_id, 0) + 1
        students.update(seat.student_id for seat in entry.seats)
    for (faculty_id,) in db.session.query(Faculty.faculty_id):
        count = duties.get(faculty_id, 0)
        text = (f"The examination timetable has been published. You have {count} invigilation "
                f"dut{'y' if count == 1 else 'ies'}." if count else
                "The examination timetable has been published. You have no invigilation duties.")
        db.session.add(Notification(recipient_type="FACULTY", recipient_id=faculty_id, message=text))
    for student_id in students:
        db.session.add(Notification(recipient_type="STUDENT", recipient_id=student_id,
                                    message="Your examination timetable, rooms and seats have been published."))


def archive_timetable(timetable: Timetable) -> None:
    if timetable.status != "PUBLISHED":
        raise ValueError("Only a published timetable can be archived")
    timetable.status = "ARCHIVED"
    log_action("ARCHIVE_TIMETABLE", f"Timetable {timetable.id} archived")
    db.session.commit()
