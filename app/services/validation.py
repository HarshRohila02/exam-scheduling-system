"""Field rules for the master data (SRS 3.2.1: "check if all necessary data is included and
check ID uniqueness").

The rules are written once, as data, in ENTITIES below. The web forms AND the CSV upload both
call validate_record(), so a record is checked in exactly the same way wherever it comes from.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.extensions import db
from app.models import (ConstraintVersion, Course, Faculty, Registration, Room, Student)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
TRUE_WORDS = {"yes", "y", "true", "1"}
FALSE_WORDS = {"no", "n", "false", "0"}


@dataclass
class Field:
    name: str                       # attribute name on the model and column name in the CSV
    label: str                      # text shown to the admin
    kind: str = "text"              # text | id | int | email | choice | bool | designation
    required: bool = True
    choices: Tuple[str, ...] = ()
    min: Optional[int] = None
    max: Optional[int] = None
    max_len: int = 100
    default: Any = None             # used when an optional field is left blank


@dataclass
class Entity:
    key: str                        # used in URLs: /data/<key>/
    title: str
    singular: str
    model: Any
    pk_attr: str                    # model attribute that identifies one row
    fields: List[Field]
    locked_on_edit: Tuple[str, ...] # fields that cannot be changed when editing
    example: Dict[str, str] = field(default_factory=dict)   # sample row for the CSV template

    @property
    def id_fields(self):
        return [f for f in self.fields if f.kind == "id"]


ENTITIES: Dict[str, Entity] = {
    "students": Entity(
        "students", "Students", "Student", Student, "student_id",
        [Field("student_id", "Student ID", "id", max_len=20),
         Field("name", "Name"),
         Field("program", "Program", max_len=50),
         Field("section", "Section", max_len=10),
         Field("semester", "Semester", "int", min=1, max=8),
         Field("email", "Email", "email", required=False, max_len=120)],
        ("student_id",),
        {"student_id": "S001", "name": "Aarav Sharma", "program": "B.Tech CSE", "section": "A",
         "semester": "5", "email": "s001@university.edu"}),
    "courses": Entity(
        "courses", "Courses", "Course", Course, "course_code",
        [Field("course_code", "Course Code", "id", max_len=20),
         Field("name", "Course Name", max_len=120),
         Field("semester", "Semester", "int", min=1, max=8),
         Field("exam_duration_minutes", "Exam Duration (min)", "int", required=False,
               min=30, max=360, default=180)],
        ("course_code",),
        {"course_code": "CS301", "name": "Software Engineering", "semester": "5",
         "exam_duration_minutes": "180"}),
    "faculty": Entity(
        "faculty", "Faculty", "Faculty member", Faculty, "faculty_id",
        [Field("faculty_id", "Faculty ID", "id", max_len=20),
         Field("name", "Name"),
         Field("designation", "Designation", "designation", max_len=50),
         Field("department", "Department", max_len=60),
         Field("email", "Email", "email", required=False, max_len=120),
         Field("max_duties_override", "Personal Max Duties", "int", required=False, min=1, max=50)],
        ("faculty_id",),
        {"faculty_id": "F1", "name": "Dr. Anita Rao", "designation": "Professor",
         "department": "Computer Science", "email": "f1@university.edu", "max_duties_override": ""}),
    "rooms": Entity(
        "rooms", "Rooms", "Room", Room, "room_id",
        [Field("room_id", "Room ID", "id", max_len=20),
         Field("building", "Building", required=False, max_len=50, default="Main"),
         Field("capacity", "Capacity", "int", min=1, max=2000),
         Field("is_available", "Available for exams", "bool", required=False, default=True)],
        ("room_id",),
        {"room_id": "R101", "building": "Main Block", "capacity": "60", "is_available": "yes"}),
    "registrations": Entity(
        "registrations", "Registrations", "Registration", Registration, "id",
        [Field("student_id", "Student ID", "id", max_len=20),
         Field("course_code", "Course Code", "id", max_len=20),
         Field("exam_type", "Exam Type", "choice", choices=("regular", "backlog"))],
        ("student_id", "course_code"),
        {"student_id": "S001", "course_code": "CS301", "exam_type": "regular"}),
}


# ---------------------------------------------------------------- lookup data for the checks
def load_context() -> dict:
    """Read the IDs that already exist so rows can be checked without querying the DB per row."""
    active = ConstraintVersion.query.filter_by(is_active=True).first()
    return {
        "student_ids": {r[0] for r in db.session.query(Student.student_id)},
        "course_codes": {r[0] for r in db.session.query(Course.course_code)},
        "faculty_ids": {r[0] for r in db.session.query(Faculty.faculty_id)},
        "room_ids": {r[0] for r in db.session.query(Room.room_id)},
        "reg_pairs": {(r[0], r[1]) for r in db.session.query(Registration.student_id, Registration.course_code)},
        "designations": [lim.designation for lim in active.limits] if active else [],
    }


# which context set holds the existing IDs of each entity
_ID_SET = {"students": "student_ids", "courses": "course_codes", "faculty": "faculty_ids", "rooms": "room_ids"}


def remember(entity: Entity, clean: dict, ctx: dict) -> None:
    """After a row is accepted, add it to ctx so later rows in the same file see it."""
    if entity.key in _ID_SET:
        ctx[_ID_SET[entity.key]].add(clean[entity.pk_attr])
    else:
        ctx["reg_pairs"].add((clean["student_id"], clean["course_code"]))


# ---------------------------------------------------------------- checks for a single field
def _check_field(f: Field, raw, ctx) -> Tuple[Any, Optional[str]]:
    text = "" if raw is None else str(raw).strip()
    if text == "":
        if f.required:
            return None, f"{f.label} is required"
        return f.default, None
    if len(text) > f.max_len:
        return None, f"{f.label} is too long (maximum {f.max_len} characters)"

    if f.kind == "id":
        if not ID_RE.match(text):
            return None, f"{f.label} may contain only letters, digits, - and _"
        return text, None
    if f.kind == "email":
        if not EMAIL_RE.match(text):
            return None, f"{f.label} '{text}' is not a valid email address"
        return text, None
    if f.kind == "int":
        try:
            number = int(text)
        except ValueError:
            return None, f"{f.label} must be a whole number"
        if f.min is not None and number < f.min or f.max is not None and number > f.max:
            return None, f"{f.label} must be between {f.min} and {f.max}"
        return number, None
    if f.kind == "choice":
        if text.lower() not in f.choices:
            return None, f"{f.label} must be one of: {', '.join(f.choices)}"
        return text.lower(), None
    if f.kind == "bool":
        if text.lower() in TRUE_WORDS:
            return True, None
        if text.lower() in FALSE_WORDS:
            return False, None
        return None, f"{f.label} must be yes or no"
    if f.kind == "designation":
        known = ctx["designations"]
        if known and text not in known:
            return None, (f"Designation '{text}' has no duty limits in the active constraints "
                          f"(defined: {', '.join(known)}). Add it on the Constraints page first")
        return text, None
    return text, None            # plain text


# ---------------------------------------------------------------- checks for a whole record
def validate_record(entity: Entity, raw: dict, ctx: dict, editing: bool = False):
    """Return (clean_values, errors). errors is an empty list when the record is acceptable."""
    clean, errors = {}, []
    for f in entity.fields:
        value, error = _check_field(f, raw.get(f.name), ctx)
        if error:
            errors.append(error)
        else:
            clean[f.name] = value
    if errors or editing:
        return clean, errors

    # uniqueness and references (only when creating a new record)
    if entity.key in _ID_SET:
        key_value = clean[entity.pk_attr]
        if key_value in ctx[_ID_SET[entity.key]]:
            label = entity.fields[0].label
            errors.append(f"{label} {key_value} already exists")
    else:   # registrations
        sid, code = clean["student_id"], clean["course_code"]
        if sid not in ctx["student_ids"]:
            errors.append(f"Student {sid} does not exist")
        if code not in ctx["course_codes"]:
            errors.append(f"Course {code} does not exist")
        if (sid, code) in ctx["reg_pairs"]:
            errors.append(f"Student {sid} is already registered for {code}")
    return clean, errors
