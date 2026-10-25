"""Small hand-made problems for the scheduler tests (the same sample as the test-case document:
5 courses, 3 rooms, 6 faculty, exam week 2-7 Nov 2026)."""
from datetime import date

from app.scheduler.problem import (ExamInput, FacultyInput, RoomInput, Rules, SessionInput, build_slots)
from app.scheduler.solver import solve

SESSIONS = {"FN": SessionInput("FN", "09:30", "12:30"), "AN": SessionInput("AN", "14:00", "17:00")}


def rolls(prefix, first, last):
    """rolls("S", 1, 4) -> ["S001", "S002", "S003"]  (last is not included)"""
    return [f"{prefix}{i:03d}" for i in range(first, last)]


def exam(code, students, minutes=180):
    return ExamInput(code, code, minutes, {s: "regular" for s in students})


def sample_exams():
    return [
        exam("CS301", rolls("S", 1, 61)),                          # 60 students
        exam("CS302", rolls("S", 41, 86)),                         # 45 (20 shared with CS301)
        exam("CS303", rolls("T", 1, 41)),                          # 40
        exam("MA201", rolls("S", 1, 21) + rolls("U", 1, 16)),      # 35 (20 shared with CS301)
        exam("EE210", rolls("V", 1, 26)),                          # 25
    ]


def sample_rooms():
    return [RoomInput("R101", 60), RoomInput("R102", 45), RoomInput("R201", 30)]


def sample_faculty(count=6, max_duties=3, min_duties=0):
    return [FacultyInput(f"F{i}", f"Faculty {i}", min_duties, max_duties) for i in range(1, count + 1)]


def sample_slots(start=date(2026, 11, 2), end=date(2026, 11, 7), holidays=()):
    return build_slots(start, end, ["FN", "AN"], set(holidays), skip_sundays=True)


def run(exams=None, rooms=None, faculty=None, slots=None, rules=None):
    """Solve a problem; anything not given comes from the sample."""
    exams = exams if exams is not None else sample_exams()
    rooms = rooms if rooms is not None else sample_rooms()
    faculty = faculty if faculty is not None else sample_faculty()
    slots = slots if slots is not None else sample_slots()
    rules = rules or Rules(time_limit_s=20)
    result = solve(exams, rooms, faculty, slots, SESSIONS, rules)
    return result, (exams, rooms, faculty, slots, rules)
