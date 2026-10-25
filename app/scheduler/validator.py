"""Independent check of a finished timetable (SRS 2.2 point 6, requirement R8).

This file does NOT use the solver or the model. It re-reads the finished schedule and checks every
hard rule again, so a mistake in the model (or a hand edit of a draft) can never reach publishing.
An empty list of violations means the timetable is valid.
"""
import math
from collections import defaultdict
from typing import Dict, List

from app.scheduler.problem import ExamInput, ExamPlacement, FacultyInput, RoomInput, Rules, Slot


def validate_timetable(placements: List[ExamPlacement], exams: List[ExamInput], rooms: List[RoomInput],
                       faculty: List[FacultyInput], slots: List[Slot], rules: Rules) -> List[str]:
    exam_by_code = {e.code: e for e in exams}
    room_by_id = {r.room_id: r for r in rooms}
    faculty_by_id = {f.faculty_id: f for f in faculty}
    valid_slots = set(slots)
    problems: List[str] = []

    # --- every exam scheduled exactly once
    count = defaultdict(int)
    for p in placements:
        count[p.course_code] += 1
    for exam in exams:
        if count[exam.code] == 0:
            problems.append(f"Course {exam.code} is not scheduled")
        elif count[exam.code] > 1:
            problems.append(f"Course {exam.code} is scheduled {count[exam.code]} times")

    room_use = defaultdict(list)            # (slot, room) -> exam codes
    duties = defaultdict(list)              # faculty -> [(slot, room, exam)]
    student_slots = defaultdict(list)       # student -> [(slot, exam)]

    for p in placements:
        exam = exam_by_code.get(p.course_code)
        if exam is None:
            problems.append(f"Unknown course {p.course_code} in the timetable")
            continue
        if p.slot not in valid_slots:
            problems.append(f"{exam.code} is on {p.exam_date} {p.session}, which is not a valid exam slot "
                            f"(holiday, Sunday or outside the exam period)")

        seated = set()
        for room_id, student_count in p.rooms.items():
            room = room_by_id.get(room_id)
            if room is None:
                problems.append(f"{exam.code} uses unknown or unavailable room {room_id}")
                continue
            room_use[(p.slot, room_id)].append(exam.code)
            limit = rules.usable_capacity(room.capacity)
            if student_count > limit:
                problems.append(f"Room {room_id} over capacity for {exam.code} ({student_count} > {limit})")

            room_seats = p.seats.get(room_id, {})
            if len(room_seats) != student_count or len(set(room_seats.values())) != len(room_seats):
                problems.append(f"Seat allocation for {exam.code} in {room_id} is incomplete or duplicated")
            seated.update(room_seats)

            need = math.ceil(student_count / rules.students_per_invigilator)
            staff = p.invigilators.get(room_id, [])
            if len(staff) < need:
                problems.append(f"{exam.code} in {room_id} has {len(staff)} invigilator(s), needs {need}")
            for person in staff:
                duties[person].append((p.slot, room_id, exam.code))
                member = faculty_by_id.get(person)
                if member is not None and p.slot in member.unavailable:
                    problems.append(f"Faculty {person} assigned to {exam.code} while unavailable")

        for room_id in sorted(set(p.seats) - set(p.rooms)):
            problems.append(f"{exam.code} has seats in room {room_id}, which the exam does not use")
        if seated != set(exam.students):
            problems.append(f"Seat allocation for {exam.code} does not match its registered students")
        for student in exam.students:
            student_slots[student].append((p.slot, exam.code))

    for (slot, room_id), codes in room_use.items():
        if len(codes) > 1:
            problems.append(f"Room {room_id} double-booked on {slot[0]} {slot[1]}: {', '.join(sorted(codes))}")

    for student, items in student_slots.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (slot_a, code_a), (slot_b, code_b) = items[i], items[j]
                if slot_a == slot_b:
                    problems.append(f"Student clash: {student} has {code_a} and {code_b} on {slot_a[0]} {slot_a[1]}")
                elif rules.no_same_day_exams and slot_a[0] == slot_b[0]:
                    problems.append(f"Student {student} has two exams on {slot_a[0]} ({code_a}, {code_b})")

    for person, items in duties.items():
        in_slot = defaultdict(list)
        for slot, room_id, code in items:
            in_slot[slot].append(f"{code} ({room_id})")
        for slot, labels in in_slot.items():
            if len(labels) > 1:
                problems.append(f"Faculty {person} double-booked on {slot[0]} {slot[1]}: {', '.join(sorted(labels))}")
        member = faculty_by_id.get(person)
        if member is not None and len(items) > member.max_duties:
            problems.append(f"Faculty {person} exceeds max duties ({len(items)} > {member.max_duties})")

    for member in faculty:
        if len(duties.get(member.faculty_id, [])) < min(member.min_duties, member.max_duties):
            problems.append(f"Faculty {member.faculty_id} has fewer than the minimum "
                            f"{member.min_duties} duties")
    return problems
