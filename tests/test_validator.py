"""Requirement R8 - the independent validator must catch every kind of rule violation."""
import copy

import pytest

from app.scheduler.problem import RoomInput
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


def test_detect_missing_exam_and_missing_invigilator(solved):
    placements, context = solved
    bad = copy.deepcopy(placements)[1:]                           # drop one exam
    assert any("is not scheduled" in v for v in check(bad, context))
    bad = copy.deepcopy(placements)
    first_room = next(iter(bad[0].invigilators))
    bad[0].invigilators[first_room] = []
    assert any("invigilator(s), needs" in v for v in check(bad, context))
