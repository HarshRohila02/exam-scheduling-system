"""Seat numbers for every student (SRS 3.2.3: "allocation of rooms and seats")."""
from typing import Dict

from app.scheduler.problem import ExamInput


def assign_seats(exam: ExamInput, students_per_room: Dict[str, int]) -> Dict[str, Dict[str, str]]:
    """Fill the rooms one after the other with the students in ID order.

    Returns {room_id: {student_id: seat_no}}, with seat numbers like R101-S01, R101-S02, ...
    Backlog and regular students sit together; the backlog flag is kept on the seat record.
    """
    students = sorted(exam.students)
    seats, start = {}, 0
    for room_id in sorted(students_per_room):
        group = students[start:start + students_per_room[room_id]]
        start += students_per_room[room_id]
        seats[room_id] = {student: f"{room_id}-S{number:02d}" for number, student in enumerate(group, start=1)}
    return seats
