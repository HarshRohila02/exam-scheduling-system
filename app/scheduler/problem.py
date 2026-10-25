"""Plain Python description of one scheduling problem and its answer (SRS 3.2.4).

Nothing in the scheduler package knows about Flask or the database: timetable_service.py turns
database rows into these small classes, hands them to the solver, and turns the answer back into rows.
That keeps the CSP code easy to read and easy to test.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

Slot = Tuple[date, str]            # (exam date, session code), e.g. (2026-11-02, "FN")


# ---------------------------------------------------------------- errors
class InputValidationError(Exception):
    """Required data is missing, so a timetable cannot even be attempted."""


class InfeasibleScheduleError(Exception):
    """No conflict-free schedule exists. `conflicts` lists the reasons in plain English."""

    def __init__(self, message: str, conflicts: Optional[List[str]] = None):
        super().__init__(message)
        self.conflicts = conflicts or []


class NotPublishedError(Exception):
    """A student or faculty member asked for a timetable that has not been published yet."""


# ---------------------------------------------------------------- inputs
@dataclass
class SessionInput:
    code: str                       # "FN"
    start_time: str                 # "09:30"
    end_time: str                   # "12:30"

    @property
    def minutes(self) -> int:
        sh, sm = map(int, self.start_time.split(":"))
        eh, em = map(int, self.end_time.split(":"))
        return (eh * 60 + em) - (sh * 60 + sm)


@dataclass
class ExamInput:
    code: str
    name: str
    duration_minutes: int
    students: Dict[str, str]        # student_id -> "regular" or "backlog"

    @property
    def size(self) -> int:
        return len(self.students)


@dataclass
class RoomInput:
    room_id: str
    capacity: int


@dataclass
class FacultyInput:
    faculty_id: str
    name: str
    min_duties: int
    max_duties: int
    unavailable: Set[Slot] = field(default_factory=set)   # slots the person cannot invigilate


@dataclass
class Rules:
    """The active constraint version (SRS 3.2.2)."""
    students_per_invigilator: int = 30
    no_same_day_exams: bool = True          # a student never has two exams on the same day
    room_fill_percent: int = 100            # share of each room's seats that may be used
    duty_balance_weight: int = 10           # soft: even spread of invigilation duties
    exam_spacing_weight: int = 1            # soft: gap between a student's exams
    time_limit_s: int = 30

    def usable_capacity(self, capacity: int) -> int:
        return capacity * self.room_fill_percent // 100


# ---------------------------------------------------------------- output
@dataclass
class ExamPlacement:
    """Where one exam was scheduled, in which rooms, with which students and invigilators."""
    course_code: str
    exam_date: date
    session: str
    rooms: Dict[str, int] = field(default_factory=dict)                    # room_id -> students seated
    seats: Dict[str, Dict[str, str]] = field(default_factory=dict)         # room_id -> {student_id: seat_no}
    invigilators: Dict[str, List[str]] = field(default_factory=dict)       # room_id -> [faculty_id]

    @property
    def slot(self) -> Slot:
        return (self.exam_date, self.session)


@dataclass
class SolveResult:
    status: str                     # OPTIMAL or FEASIBLE (INFEASIBLE raises InfeasibleScheduleError)
    placements: List[ExamPlacement]
    solve_seconds: float


# ---------------------------------------------------------------- calendar -> slots
def build_slots(start: date, end: date, session_codes: Iterable[str],
                holidays: Set[date], skip_sundays: bool = True) -> List[Slot]:
    """Every valid (date, session) between start and end, skipping holidays and Sundays."""
    slots, day = [], start
    while day <= end:
        if day not in holidays and not (skip_sundays and day.weekday() == 6):
            slots.extend((day, code) for code in session_codes)
        day += timedelta(days=1)
    return slots
