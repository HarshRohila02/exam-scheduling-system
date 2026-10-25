"""Quick sanity checks that run BEFORE the solver, so the admin gets a precise message instead of a
vague "no solution" (SRS 3.2.3: "conflicting constraints are identified for further manual analysis").
"""
import math
from typing import Dict, List

from app.scheduler.problem import (ExamInput, FacultyInput, InfeasibleScheduleError, InputValidationError,
                                   RoomInput, Rules, SessionInput, Slot)


def run_prechecks(exams: List[ExamInput], rooms: List[RoomInput], faculty: List[FacultyInput],
                  slots: List[Slot], sessions: Dict[str, SessionInput], rules: Rules) -> None:
    # --- missing data: nothing can be attempted
    if not exams:
        raise InputValidationError("No courses found for the exam cycle")
    if not rooms:
        raise InputValidationError("No rooms available")
    if not faculty:
        raise InputValidationError("No faculty available for invigilation")
    if not slots:
        raise InputValidationError("University calendar has no working exam slots")
    if rules.students_per_invigilator <= 0:
        raise InputValidationError("Students per invigilator must be positive")

    # --- an exam bigger than all rooms put together
    total_seats = sum(rules.usable_capacity(r.capacity) for r in rooms)
    too_big = [f"{e.code} has {e.size} students; all rooms together seat only {total_seats} "
               f"(largest room: {max(r.capacity for r in rooms)})"
               for e in exams if e.size > total_seats]
    if too_big:
        raise InfeasibleScheduleError("Room capacity constraint cannot be satisfied", too_big)

    # --- an exam longer than every session
    longest = max(s.minutes for s in sessions.values())
    too_long = [f"{e.code} lasts {e.duration_minutes} minutes but the longest session is {longest} minutes"
                for e in exams if e.duration_minutes > longest]
    if too_long:
        raise InfeasibleScheduleError("Exam duration does not fit any session", too_long)

    # --- not enough invigilation capacity in total
    needed = sum(math.ceil(e.size / rules.students_per_invigilator) for e in exams)
    supply = sum(f.max_duties for f in faculty)
    if needed > supply:
        raise InfeasibleScheduleError(
            "Insufficient invigilators",
            [f"The exams need at least {needed} invigilation duties, but the faculty duty limits "
             f"allow only {supply} in total. Add faculty or raise the maximum duties"])
