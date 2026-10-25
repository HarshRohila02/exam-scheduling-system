"""SRS 3.2.4 - the CP-SAT model: timetable generation, room/seat allocation, invigilation duties.
Test-case IDs (TC02 ... TC13) match the test-case document."""
import math
from collections import Counter
from datetime import date

import pytest

from app.scheduler.problem import (FacultyInput, InfeasibleScheduleError, InputValidationError, RoomInput, Rules)
from app.scheduler.validator import validate_timetable
from tests.conftest import record
from tests.sample import (exam, rolls, run, sample_exams, sample_faculty, sample_rooms, sample_slots)


def by_code(result):
    return {p.course_code: p for p in result.placements}


def where(p):
    return f"{p.exam_date:%d-%b} {p.session}"


# =========================================================== F1: CSP timetable generation
def test_TC02_courses_with_common_students_on_different_days():
    result, _ = run()
    p = by_code(result)
    record("TC02", f"CS301 on {where(p['CS301'])}, CS302 on {where(p['CS302'])}, MA201 on {where(p['MA201'])} "
                   f"(courses sharing students are on different days)")
    assert p["CS301"].exam_date != p["CS302"].exam_date
    assert p["CS301"].exam_date != p["MA201"].exam_date


def test_TC03_holidays_and_sundays_excluded():
    holidays = {date(2026, 11, 2), date(2026, 11, 3)}
    result, _ = run(slots=sample_slots(date(2026, 11, 2), date(2026, 11, 8), holidays))
    used = sorted({p.exam_date for p in result.placements})
    record("TC03", "Exam dates used: " + ", ".join(f"{d:%d-%b (%a)}" for d in used))
    for day in used:
        assert day not in holidays and day.weekday() != 6


def test_TC04_infeasible_constraints_are_reported():
    common = rolls("S", 1, 11)
    exams = [exam("A1", common), exam("A2", common), exam("A3", common)]
    slots = sample_slots(date(2026, 11, 2), date(2026, 11, 2))          # one day, two sessions
    with pytest.raises(InfeasibleScheduleError) as error:
        run(exams=exams, slots=slots)
    record("TC04", f"InfeasibleScheduleError: {error.value} | {error.value.conflicts[0][:90]}...")
    assert "Student clashes" in error.value.conflicts[0]


def test_TC05_no_course_data_is_rejected():
    with pytest.raises(InputValidationError) as error:
        run(exams=[])
    record("TC05", f"InputValidationError: {error.value}")


def test_same_day_rule_can_be_switched_off():
    common = rolls("S", 1, 11)
    exams = [exam("A1", common), exam("A2", common)]
    one_day = sample_slots(date(2026, 11, 2), date(2026, 11, 2))
    with pytest.raises(InfeasibleScheduleError):
        run(exams=exams, slots=one_day, rules=Rules(no_same_day_exams=True))
    result, _ = run(exams=exams, slots=one_day, rules=Rules(no_same_day_exams=False))
    sessions = {p.session for p in result.placements}
    assert sessions == {"FN", "AN"}                       # morning and evening on the same day


# =========================================================== F2: room and seat allocation
def test_TC06_room_capacity_respected():
    result, (exams, rooms, *_) = run()
    cap = {r.room_id: r.capacity for r in rooms}
    size = {e.code: e.size for e in exams}
    for p in result.placements:
        assert sum(p.rooms.values()) == size[p.course_code]
        for room_id, count in p.rooms.items():
            assert count <= cap[room_id]
    record("TC06", "; ".join(f"{p.course_code}({size[p.course_code]})->" +
                             "+".join(f"{r}({cap[r]})" for r in p.rooms) for p in result.placements))


def test_TC07_no_room_double_booking():
    rooms = [RoomInput("R101", 60), RoomInput("R102", 45)]       # fewer rooms
    result, _ = run(rooms=rooms)
    pairs = [(p.slot, r) for p in result.placements for r in p.rooms]
    record("TC07", f"{len(pairs)} room bookings, {len(set(pairs))} unique (slot, room) pairs - none double-booked")
    assert len(pairs) == len(set(pairs))


def test_TC08_course_larger_than_all_rooms_together():
    big = [exam("CS401", rolls("B", 1, 201))]                    # 200 students; rooms hold 60+45+30=135
    with pytest.raises(InfeasibleScheduleError) as error:
        run(exams=big)
    record("TC08", f"InfeasibleScheduleError: {error.value} | {error.value.conflicts[0]}")
    assert "CS401" in error.value.conflicts[0] and "135" in error.value.conflicts[0]


def test_TC08b_large_course_is_split_across_rooms():
    big = [exam("CS401", rolls("B", 1, 91))]                     # 90 students: needs two rooms
    result, (exams, rooms, faculty, slots, rules) = run(exams=big)
    p = result.placements[0]
    record("TC08b", f"CS401 (90 students) split across rooms {dict(p.rooms)}")
    assert len(p.rooms) >= 2 and sum(p.rooms.values()) == 90
    assert validate_timetable(result.placements, exams, rooms, faculty, slots, rules) == []


def test_TC09_every_student_gets_a_unique_seat():
    result, _ = run()
    p = by_code(result)["CS301"]
    seats = [seat for room in p.seats.values() for seat in room.values()]
    record("TC09", f"CS301: {len(seats)}/60 students seated, {len(set(seats))} unique seats "
                   f"({seats[0]} ... {seats[-1]}), S001 -> {p.seats['R101']['S001']}")
    assert len(seats) == 60 and len(set(seats)) == 60
    assert p.seats["R101"]["S001"] == "R101-S01"


# =========================================================== F3: invigilation duty allocation
def test_TC10_invigilators_follow_ratio():
    result, _ = run()
    lines = []
    for p in result.placements:
        for room_id, count in p.rooms.items():
            need = math.ceil(count / 30)
            assert len(p.invigilators[room_id]) == need
            lines.append(f"{p.course_code}/{room_id}: {count} students -> {need}")
    record("TC10", "; ".join(lines))


def test_TC11_unavailable_faculty_not_assigned():
    slots = sample_slots()
    faculty = sample_faculty()
    faculty[0].unavailable = set(slots)                          # F1 on leave all week
    result, _ = run(faculty=faculty, slots=slots)
    assigned = sorted({f for p in result.placements for staff in p.invigilators.values() for f in staff})
    record("TC11", f"F1 marked unavailable; faculty assigned: {', '.join(assigned)} (F1 never assigned)")
    assert "F1" not in assigned


def test_TC12_duty_limit_and_fair_distribution():
    result, _ = run()
    load = Counter({f"F{i}": 0 for i in range(1, 7)})
    for p in result.placements:
        for staff in p.invigilators.values():
            load.update(staff)
    spread = max(load.values()) - min(load.values())
    record("TC12", "Duties per faculty: " + ", ".join(f"{k}={v}" for k, v in sorted(load.items()))
           + f" (max allowed 3, spread {spread})")
    assert max(load.values()) <= 3 and spread <= 1


def test_TC13_not_enough_faculty():
    faculty = [FacultyInput("F1", "Faculty 1", 0, 1), FacultyInput("F2", "Faculty 2", 0, 1)]
    exams = [exam(f"C{i}", rolls(f"X{i}", 1, 21)) for i in range(1, 4)]     # 3 exams x 1 invigilator
    with pytest.raises(InfeasibleScheduleError) as error:
        run(exams=exams, faculty=faculty)
    record("TC13", f"InfeasibleScheduleError: {error.value} | {error.value.conflicts[0]}")
    assert str(error.value) == "Insufficient invigilators"


def test_minimum_duties_are_met():
    faculty = sample_faculty(max_duties=3, min_duties=1)
    result, _ = run(faculty=faculty)
    load = Counter(f for p in result.placements for staff in p.invigilators.values() for f in staff)
    assert all(load[f.faculty_id] >= 1 for f in faculty)


def test_single_session_unavailability_is_respected():
    slots = sample_slots()
    faculty = sample_faculty()
    faculty[0].unavailable = {s for s in slots if s[1] == "FN"}  # F1 cannot do mornings
    result, _ = run(faculty=faculty, slots=slots)
    for p in result.placements:
        if p.session == "FN":
            assert all("F1" not in staff for staff in p.invigilators.values())


def test_infeasible_explanation_names_the_faculty_rule():
    # every faculty member is unavailable in every session -> the explanation points at availability
    slots = sample_slots(date(2026, 11, 2), date(2026, 11, 3))
    faculty = sample_faculty()
    for f in faculty:
        f.unavailable = set(slots)
    with pytest.raises(InfeasibleScheduleError) as error:
        run(faculty=faculty, slots=slots)
    assert any("unavailability" in c.lower() for c in error.value.conflicts)
