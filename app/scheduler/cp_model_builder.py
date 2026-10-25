"""The CSP model for exam scheduling (SRS 3.2.4), written for Google OR-Tools CP-SAT.

How to read this file
---------------------
A Constraint Satisfaction Problem has three parts: variables, domains and constraints.

  Variables (SRS: "one variable per examination for a (date, session, room, invigilator) combination")
    x[e,t]      True if exam e is held in slot t           (slot t = one date + one session)
    use[e,r,t]  True if exam e uses room r in slot t        (an exam may use several rooms)
    seats[e,r,t] how many students of exam e sit in room r  (0 .. usable room capacity)
    inv[e,r,t]  how many invigilators room r needs for exam e
    duty[f,r,t] True if faculty member f invigilates room r in slot t

  Domains: the slots come from the University Calendar, the rooms from the Room list (capacity and
  availability), the faculty from the Faculty list (availability and remaining duty limit).

  Hard constraints (never broken) and soft constraints (the objective the solver minimises) are
  added in build_model() below, each one numbered to match the comments there.

When the solver says INFEASIBLE we build the model a second time with `explain=True`: every group of
hard constraints is then switched by its own "assumption" switch, and CP-SAT tells us which switches
cannot all be on together. That is how the admin gets "these rules conflict" instead of just "no".
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ortools.sat.python import cp_model

from app.scheduler.problem import ExamInput, FacultyInput, RoomInput, Rules, Slot

# rule group -> (what the group means, what the admin can do about it)
GROUPS = {
    "student_clash": ("Student clashes: courses that share students do not fit into the available "
                      "dates and sessions without two exams meeting for one student",
                      "add exam days or sessions, remove a holiday, or allow two exams on one day"),
    "room_capacity": ("Room capacity: the exams do not fit into the available rooms and sessions "
                      "(a room can hold only one exam per session)",
                      "add rooms, add exam days or sessions, or raise the room fill percentage"),
    "invigilator_ratio": ("Invigilator ratio: the invigilators required for the rooms cannot all be staffed",
                          "add faculty, or raise the students-per-invigilator ratio"),
    "faculty_one_duty": ("A faculty member can only supervise one room per session",
                         "add faculty or add sessions"),
    "faculty_availability": ("Faculty unavailability leaves too few invigilators in some sessions",
                             "reduce faculty unavailability or add faculty"),
    "faculty_max_duties": ("Faculty maximum duty limits are too low for the invigilation work",
                           "raise the maximum duties of a designation, or add faculty"),
    "faculty_min_duties": ("Faculty minimum duty limits are higher than the invigilation work available",
                           "lower the minimum duties of a designation"),
}

SPACING_CAP = 3          # a gap of more than this many days between two exams earns no extra credit
ROOM_PENALTY = 1         # small cost per room used


@dataclass
class ModelBundle:
    model: cp_model.CpModel
    x: Dict[Tuple[int, int], cp_model.IntVar]
    use: Dict[Tuple[int, int, int], cp_model.IntVar]
    seats: Dict[Tuple[int, int, int], cp_model.IntVar]
    duty: Dict[Tuple[int, int, int], cp_model.IntVar]
    group_of_literal: Dict[int, str] = field(default_factory=dict)   # variable index -> rule group


def shared_student_counts(exams: List[ExamInput]) -> Dict[Tuple[int, int], int]:
    """For every pair of exams that have at least one student in common, how many students."""
    exams_of_student = defaultdict(list)
    for e, exam in enumerate(exams):
        for student in exam.students:
            exams_of_student[student].append(e)
    pairs = defaultdict(int)
    for exam_list in exams_of_student.values():
        for i in range(len(exam_list)):
            for j in range(i + 1, len(exam_list)):
                pairs[(exam_list[i], exam_list[j])] += 1
    return pairs


def build_model(exams: List[ExamInput], rooms: List[RoomInput], faculty: List[FacultyInput],
                slots: List[Slot], rules: Rules, explain: bool = False) -> ModelBundle:
    model = cp_model.CpModel()
    E, F, T = len(exams), len(faculty), len(slots)
    usable = [rules.usable_capacity(r.capacity) for r in rooms]
    room_ids = [r for r in range(len(rooms)) if usable[r] >= 1]
    ratio = rules.students_per_invigilator
    slot_index = {slot: t for t, slot in enumerate(slots)}

    # group literals: only used when explaining an infeasible model
    switch = {g: model.new_bool_var(f"group_{g}") for g in GROUPS} if explain else {}

    def hard(constraint, group):
        """Register a hard constraint under its rule group."""
        if explain:
            constraint.only_enforce_if(switch[group])
        return constraint

    # ------------------------------------------------------------ variables
    x = {(e, t): model.new_bool_var(f"x_{e}_{t}") for e in range(E) for t in range(T)}
    use, seats, inv = {}, {}, {}
    for e in range(E):
        for t in range(T):
            for r in room_ids:
                biggest = min(usable[r], exams[e].size)
                use[e, r, t] = model.new_bool_var(f"use_{e}_{r}_{t}")
                seats[e, r, t] = model.new_int_var(0, biggest, f"seats_{e}_{r}_{t}")
                inv[e, r, t] = model.new_int_var(0, math.ceil(biggest / ratio), f"inv_{e}_{r}_{t}")
    duty = {(f, r, t): model.new_bool_var(f"duty_{f}_{r}_{t}")
            for f in range(F) for r in room_ids for t in range(T)}

    # ------------------------------------------------------------ base rules of the problem
    for e in range(E):
        # every exam is held in exactly one (date, session)
        model.add_exactly_one(x[e, t] for t in range(T))
    for (e, r, t), used in use.items():
        model.add_implication(used, x[e, t])                      # a room is used only in the exam's slot
        model.add(seats[e, r, t] >= 1).only_enforce_if(used)      # a used room has students in it
        model.add(seats[e, r, t] == 0).only_enforce_if(used.Not())

    # ------------------------------------------------------------ HARD constraints
    # H1  every student of an exam gets a seat, and a room holds one exam per session
    for e in range(E):
        total = sum(seats[e, r, t] for r in room_ids for t in range(T))
        hard(model.add(total == exams[e].size), "room_capacity")
    for r in room_ids:
        for t in range(T):
            hard(model.add(sum(use[e, r, t] for e in range(E)) <= 1), "room_capacity")

    # H2  courses that share a student never meet: not in the same session and, if the rule is
    #     on, not even on the same day (so no morning + evening exam for one student)
    days = sorted({d for d, _ in slots})
    day_no = {d: i for i, d in enumerate(days)}
    slots_on_day = defaultdict(list)
    for t, (d, _) in enumerate(slots):
        slots_on_day[day_no[d]].append(t)
    shared = shared_student_counts(exams)
    for (a, b) in shared:
        if rules.no_same_day_exams:
            for ts in slots_on_day.values():
                hard(model.add(sum(x[a, t] + x[b, t] for t in ts) <= 1), "student_clash")
        else:
            for t in range(T):
                hard(model.add(x[a, t] + x[b, t] <= 1), "student_clash")

    # H3  invigilators per room follow the ratio: inv = ceil(students / ratio), and exactly that
    #     many faculty members are put on duty in the room
    for (e, r, t) in use:
        hard(model.add(ratio * inv[e, r, t] - seats[e, r, t] >= 0), "invigilator_ratio")
        hard(model.add(ratio * inv[e, r, t] - seats[e, r, t] <= ratio - 1), "invigilator_ratio")
    for r in room_ids:
        for t in range(T):
            needed = sum(inv[e, r, t] for e in range(E))
            hard(model.add(sum(duty[f, r, t] for f in range(F)) == needed), "invigilator_ratio")

    # H4  nobody supervises two rooms in the same session
    for f in range(F):
        for t in range(T):
            hard(model.add(sum(duty[f, r, t] for r in room_ids) <= 1), "faculty_one_duty")

    # H5  nobody is given duty in a session they are unavailable for
    for f, person in enumerate(faculty):
        for slot in person.unavailable:
            t = slot_index.get(slot)
            if t is not None:
                hard(model.add(sum(duty[f, r, t] for r in room_ids) == 0), "faculty_availability")

    # H6  minimum and maximum duties of every faculty member
    loads = []
    for f, person in enumerate(faculty):
        load = model.new_int_var(0, T * max(1, len(room_ids)), f"load_{f}")
        model.add(load == sum(duty[f, r, t] for r in room_ids for t in range(T)))
        hard(model.add(load <= person.max_duties), "faculty_max_duties")
        hard(model.add(load >= min(person.min_duties, person.max_duties)), "faculty_min_duties")
        loads.append(load)

    if explain:
        model.add_assumptions(list(switch.values()))
        return ModelBundle(model, x, use, seats, duty, {lit.index: g for g, lit in switch.items()})

    # ------------------------------------------------------------ SOFT constraints (the objective)
    # S1  spread the invigilation duties evenly: minimise (busiest load - lightest load)
    most = model.new_int_var(0, T * max(1, len(room_ids)), "most_duties")
    least = model.new_int_var(0, T * max(1, len(room_ids)), "least_duties")
    for load in loads:
        model.add(load <= most)
        model.add(load >= least)

    # S2  leave rest days between the exams of students who take several: for every pair of
    #     courses that share students, reward the gap in days (up to SPACING_CAP)
    gaps = []
    if len(days) > 1:
        day_of = [sum(day_no[slots[t][0]] * x[e, t] for t in range(T)) for e in range(E)]
        for (a, b) in shared:
            difference = model.new_int_var(-(len(days) - 1), len(days) - 1, f"diff_{a}_{b}")
            model.add(difference == day_of[a] - day_of[b])
            distance = model.new_int_var(0, len(days) - 1, f"dist_{a}_{b}")
            model.add_abs_equality(distance, difference)
            gap = model.new_int_var(0, min(SPACING_CAP, len(days) - 1), f"gap_{a}_{b}")
            model.add(gap <= distance)
            gaps.append(gap)

    # S3  never add rooms or invigilators just to make the numbers look even: every extra
    #     invigilator costs a little more than the best possible gain in S1, and a room costs a bit
    #     too, so one 60-seat room beats two 30-seat rooms
    invigilators_total = sum(inv.values())
    rooms_used = sum(use.values())

    model.minimize(rules.duty_balance_weight * (most - least)
                   + (rules.duty_balance_weight + 1) * invigilators_total
                   + ROOM_PENALTY * rooms_used
                   - rules.exam_spacing_weight * sum(gaps))
    return ModelBundle(model, x, use, seats, duty)
