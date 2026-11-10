"""Requirement R8 - the independent validator must catch every kind of rule violation."""
import copy

import pytest

from app.scheduler.problem import RoomInput, SessionInput
from app.scheduler.validator import validate_timetable
from tests.conftest import record
from tests.sample import run


@pytest.fixture(scope="module")
def solved():
    result, context = run()
    return result.placements, context


def check(placements, context, rooms=None):
    exams, rooms_, faculty, slots, rules = context
    return validate_timetable(placements, exams, rooms or rooms_, faculty, slots, rules)


def test_TC14_generated_timetable_is_valid(solved):
    placements, context = solved
    violations = check(placements, context)
    record("TC14", f"{len(violations)} violations found")
    assert violations == []


def test_TC15_detect_student_clash(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)
    p = {x.course_code: x for x in bad}
    p["CS302"].exam_date, p["CS302"].session = p["CS301"].exam_date, p["CS301"].session
    violations = [v for v in check(bad, context) if "clash" in v]
    record("TC15", f"{len(violations)} student-clash violations, e.g. '{violations[0]}'")
    assert violations


def test_TC16_detect_room_over_capacity(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)
    p = {x.course_code: x for x in bad}["CS301"]
    p.rooms = {"R201": 60}                                       # 60 students in a 30-seat room
    violations = [v for v in check(bad, context) if "over capacity" in v]
    record("TC16", f"Violation: '{violations[0]}'")
    assert violations


def test_TC17_detect_faculty_double_booking(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)
    p = {x.course_code: x for x in bad}
    cs303, ee210 = p["CS303"], p["EE210"]
    students = sorted(s for room in ee210.seats.values() for s in room)
    # move EE210 into CS303's slot, in a spare room, with CS303's first invigilator
    ee210.exam_date, ee210.session = cs303.exam_date, cs303.session
    ee210.rooms = {"R999": len(students)}
    ee210.seats = {"R999": {s: f"R999-S{i:02d}" for i, s in enumerate(students, start=1)}}
    ee210.invigilators = {"R999": [next(iter(cs303.invigilators.values()))[0]]}
    rooms = context[1] + [RoomInput("R999", 100)]
    violations = [v for v in check(bad, context, rooms) if "double-booked" in v and "Faculty" in v]
    record("TC17", f"Violation: '{violations[0]}'")
    assert violations


def test_detect_room_double_booking(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)
    p = {x.course_code: x for x in bad}
    room = next(iter(p["CS303"].rooms))
    p["EE210"].exam_date, p["EE210"].session = p["CS303"].exam_date, p["CS303"].session
    p["EE210"].rooms = {room: 25}
    assert any("Room" in v and "double-booked" in v for v in check(bad, context))


def edited(placements, code):
    """A deep copy of the solved timetable and the placement of one course in it."""
    bad = copy.deepcopy(placements)
    return bad, {x.course_code: x for x in bad}[code]


def test_detect_student_seated_in_two_rooms(solved):
    placements, context = solved
    bad, p = edited(placements, "CS301")
    first_room = next(iter(p.rooms))
    student = next(iter(p.seats[first_room]))
    p.rooms["R999"] = 1                                          # the same student gets a second seat elsewhere
    p.seats["R999"] = {student: "R999-S01"}
    p.invigilators["R999"] = [context[2][0].faculty_id]
    rooms = context[1] + [RoomInput("R999", 10)]
    problems = check(bad, context, rooms)
    assert any("more than one room" in v or "registered" in v for v in problems)


def test_detect_unknown_invigilator(solved):
    placements, context = solved
    bad, p = edited(placements, "CS301")
    room = next(iter(p.invigilators))
    p.invigilators[room][0] = "GHOST"
    assert any("unknown invigilator GHOST" in v for v in check(bad, context))


def test_detect_too_many_invigilators(solved):
    placements, context = solved
    bad, p = edited(placements, "EE210")                         # 25 students: exactly one invigilator is needed
    room = next(iter(p.invigilators))
    spare = next(f.faculty_id for f in context[2] if f.faculty_id not in p.invigilators[room])
    p.invigilators[room].append(spare)
    assert any("invigilator(s), needs" in v for v in check(bad, context))


def test_detect_invigilators_in_a_room_the_exam_does_not_use(solved):
    placements, context = solved
    bad, p = edited(placements, "EE210")
    p.invigilators["R999"] = [context[2][0].faculty_id]
    assert any("does not use" in v for v in check(bad, context))


def test_detect_exam_longer_than_its_session(solved):
    placements, context = solved
    exams, rooms, faculty, slots, rules = context
    short = {"FN": SessionInput("FN", "09:30", "10:30"), "AN": SessionInput("AN", "14:00", "15:00")}   # 60 minutes each
    problems = validate_timetable(placements, exams, rooms, faculty, slots, rules, sessions=short)
    assert sum("lasts 180 minutes" in v for v in problems) == len(placements)      # every exam is too long


def test_detect_missing_exam_and_missing_invigilator(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)[1:]                           # drop one exam
    assert any("is not scheduled" in v for v in check(bad, context))
    bad = copy.deepcopy(placements)
    first_room = next(iter(bad[0].invigilators))
    bad[0].invigilators[first_room] = []
    assert any("invigilator(s), needs" in v for v in check(bad, context))
