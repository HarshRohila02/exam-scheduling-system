"""Runs the CP-SAT solver and turns its answer into ExamPlacement objects (SRS 3.2.3 / 3.2.4)."""
import threading
import time
from typing import Dict, List

from ortools.sat.python import cp_model

from app.scheduler.cp_model_builder import GROUPS, ModelBundle, build_model
from app.scheduler.prechecks import run_prechecks
from app.scheduler.problem import (ExamInput, ExamPlacement, FacultyInput, InfeasibleScheduleError, RoomInput,
                                   Rules, SessionInput, Slot, SolveResult)
from app.scheduler.seating import assign_seats

WORKERS = 4
STALL_SECONDS = 3          # stop when no better timetable has appeared for this long


class StopWhenStalled(cp_model.CpSolverSolutionCallback):
    """Proving that a timetable is the very best can take far longer than finding it. Each time the
    solver reports a better timetable we restart a short timer; if no better one arrives before it
    rings, we stop and keep the best one found (CP-SAT reports that as FEASIBLE)."""

    def __init__(self, stall_seconds: float):
        super().__init__()
        self._stall_seconds = stall_seconds
        self._timer = None

    def on_solution_callback(self):
        self.cancel()
        self._timer = threading.Timer(self._stall_seconds, self.stop_search)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self):
        if self._timer is not None:
            self._timer.cancel()


def solve(exams: List[ExamInput], rooms: List[RoomInput], faculty: List[FacultyInput], slots: List[Slot],
          sessions: Dict[str, SessionInput], rules: Rules) -> SolveResult:
    """Return a feasible timetable, or raise InputValidationError / InfeasibleScheduleError."""
    run_prechecks(exams, rooms, faculty, slots, sessions, rules)

    started = time.time()
    bundle = build_model(exams, rooms, faculty, slots, rules)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = rules.time_limit_s
    solver.parameters.num_workers = WORKERS
    solver.parameters.random_seed = 1
    stopper = StopWhenStalled(STALL_SECONDS)
    status = solver.solve(bundle.model, stopper)
    stopper.cancel()

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        placements = _read_solution(solver, bundle, exams, rooms, faculty, slots)
        return SolveResult(solver.status_name(status), placements, round(time.time() - started, 2))

    if status == cp_model.INFEASIBLE:
        raise InfeasibleScheduleError(
            "No conflict-free timetable exists for the given constraints",
            _explain_infeasibility(exams, rooms, faculty, slots, rules))
    raise InfeasibleScheduleError(
        f"The solver found no timetable within {rules.time_limit_s} seconds",
        ["Increase the solver time limit on the Constraints page, or relax the constraints"])


def _read_solution(solver, bundle: ModelBundle, exams, rooms, faculty, slots) -> List[ExamPlacement]:
    """Read the chosen slot, rooms, seat counts and invigilators back out of the solver."""
    placements = []
    for e, exam in enumerate(exams):
        t = next(t for t in range(len(slots)) if solver.value(bundle.x[e, t]))
        placement = ExamPlacement(exam.code, slots[t][0], slots[t][1])
        for (ee, r, tt), used in bundle.use.items():
            if ee == e and tt == t and solver.value(used):
                placement.rooms[rooms[r].room_id] = solver.value(bundle.seats[e, r, t])
                placement.invigilators[rooms[r].room_id] = [
                    faculty[f].faculty_id for f in range(len(faculty)) if solver.value(bundle.duty[f, r, t])]
        placement.seats = assign_seats(exam, placement.rooms)
        placements.append(placement)
    return sorted(placements, key=lambda p: (p.slot, p.course_code))


def _explain_infeasibility(exams, rooms, faculty, slots, rules: Rules) -> List[str]:
    """Re-solve with every rule group switchable and ask CP-SAT which groups cannot all hold."""
    bundle = build_model(exams, rooms, faculty, slots, rules, explain=True)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1                      # core extraction needs a single worker
    solver.parameters.max_time_in_seconds = max(10, rules.time_limit_s)
    status = solver.solve(bundle.model)
    if status != cp_model.INFEASIBLE:
        return ["Relax constraints: add exam dates/sessions, rooms or faculty"]
    conflicts = []
    for index in solver.sufficient_assumptions_for_infeasibility():
        group = bundle.group_of_literal.get(index)
        if group:
            meaning, advice = GROUPS[group]
            conflicts.append(f"{meaning}. Try to {advice}.")
    return conflicts or ["Relax constraints: add exam dates/sessions, rooms or faculty"]
