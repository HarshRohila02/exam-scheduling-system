"""Rules for the master data - SRS 3.2.1 Data Management.

SRS 3.2.1 "Processing": check that all necessary data is included and that every ID is unique,
then store the record. This file describes each kind of record (its fields and their rules) and
has one function, check_record(), that checks a record against those rules.

The same check_record() is used by the Add / Edit forms AND by the CSV / Excel upload, so a record
is checked in exactly the same way wherever it comes from (use case "Validate Record Data").
"""
import re

from app.extensions import db
from app.models import ConstraintVersion, Course, Faculty, Registration, Room, Student

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")             # S001, CS301, R-101 ...
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
YES_WORDS = ("yes", "y", "true", "1")
NO_WORDS = ("no", "n", "false", "0")


class RecordType:
    """One kind of record (Students, Courses, ...): its table and its fields.

    Each field is a small dictionary:
        name      column name in the table, and column name in a CSV / Excel file
        label     text shown to the admin
        type      id | text | email | number | yes_no | choice | designation
        required  True if the field may not be left empty
        plus, when needed: min / max (numbers), choices (choice), default (optional fields)
    """

    def __init__(self, key, title, singular, model, id_field, fields, example):
        self.key = key                  # used in the web address: /data/<key>/
        self.title = title              # "Students"
        self.singular = singular        # "Student"
        self.model = model              # the database table (from app/models.py)
        self.id_field = id_field        # the column that identifies one record
        self.fields = fields
        self.example = example          # one example row, used for the downloadable template

    def field_names(self):
        return [f["name"] for f in self.fields]

    def first_label(self):
        return self.fields[0]["label"]


ENTITIES = {
    "students": RecordType(
        "students", "Students", "Student", Student, "student_id",
        [
            {"name": "student_id", "label": "Student ID", "type": "id", "required": True},
            {"name": "name", "label": "Name", "type": "text", "required": True},
            {"name": "program", "label": "Program", "type": "text", "required": True},
            {"name": "section", "label": "Section", "type": "text", "required": True},
            {"name": "semester", "label": "Semester", "type": "number", "required": True, "min": 1, "max": 8},
            {"name": "email", "label": "Email", "type": "email", "required": False},
        ],
        {"student_id": "S001", "name": "Aarav Sharma", "program": "B.Tech CSE", "section": "A",
         "semester": "5", "email": "s001@university.edu"}),

    "courses": RecordType(
        "courses", "Courses", "Course", Course, "course_code",
        [
            {"name": "course_code", "label": "Course Code", "type": "id", "required": True},
            {"name": "name", "label": "Course Name", "type": "text", "required": True},
            {"name": "semester", "label": "Semester", "type": "number", "required": True, "min": 1, "max": 8},
            {"name": "exam_duration_minutes", "label": "Exam Duration (minutes)", "type": "number",
             "required": False, "min": 30, "max": 360, "default": 180},
        ],
        {"course_code": "CS301", "name": "Software Engineering", "semester": "5", "exam_duration_minutes": "180"}),

    # A registration links a student to a course exam. This is where the SRS fields
    # "course" and "regular or repeat" of a student are stored (a student has several courses).
    "registrations": RecordType(
        "registrations", "Registrations", "Registration", Registration, "id",
        [
            {"name": "student_id", "label": "Student ID", "type": "id", "required": True},
            {"name": "course_code", "label": "Course Code", "type": "id", "required": True},
            {"name": "exam_type", "label": "Exam Type", "type": "choice", "required": True,
             "choices": ["regular", "backlog"]},
        ],
        {"student_id": "S001", "course_code": "CS301", "exam_type": "regular"}),

    "faculty": RecordType(
        "faculty", "Faculty", "Faculty member", Faculty, "faculty_id",
        [
            {"name": "faculty_id", "label": "Faculty ID", "type": "id", "required": True},
            {"name": "name", "label": "Name", "type": "text", "required": True},
            {"name": "designation", "label": "Designation", "type": "designation", "required": True},
            {"name": "department", "label": "Department", "type": "text", "required": True},
            {"name": "email", "label": "Email", "type": "email", "required": False},
            {"name": "max_duties_override", "label": "Personal Max Duties", "type": "number",
             "required": False, "min": 1, "max": 50, "default": None},
        ],
        {"faculty_id": "F1", "name": "Dr. Anita Rao", "designation": "Professor", "department": "Computer Science",
         "email": "f1@university.edu", "max_duties_override": ""}),

    "rooms": RecordType(
        "rooms", "Rooms", "Room", Room, "room_id",
        [
            {"name": "room_id", "label": "Room ID", "type": "id", "required": True},
            {"name": "building", "label": "Building", "type": "text", "required": False, "default": "Main"},
            {"name": "capacity", "label": "Capacity", "type": "number", "required": True, "min": 1, "max": 2000},
            {"name": "is_available", "label": "Available for exams", "type": "yes_no", "required": False,
             "default": True},
        ],
        {"room_id": "R101", "building": "Main Block", "capacity": "60", "is_available": "yes"}),
}

# Words the admin may type for the exam type (the SRS says "regular or repeat")
EXAM_TYPE_WORDS = {"regular": "regular", "backlog": "backlog", "repeat": "backlog"}


# ---------------------------------------------------------------- what already exists
def collect_known():
    """Read the IDs that are already in the database, once.

    The upload adds every accepted row to these sets, so a duplicate inside the same file is also caught."""
    active = ConstraintVersion.query.filter_by(is_active=True).first()
    return {
        "students": {row[0] for row in db.session.query(Student.student_id)},
        "courses": {row[0] for row in db.session.query(Course.course_code)},
        "faculty": {row[0] for row in db.session.query(Faculty.faculty_id)},
        "rooms": {row[0] for row in db.session.query(Room.room_id)},
        "registrations": {(r[0], r[1]) for r in db.session.query(Registration.student_id, Registration.course_code)},
        "designations": [limit.designation for limit in active.limits] if active else [],
    }


def remember(record_type, values, known):
    """Add an accepted record to `known` (used while importing a file)."""
    if record_type.key == "registrations":
        known["registrations"].add((values["student_id"], values["course_code"]))
    else:
        known[record_type.key].add(values[record_type.id_field])


# ---------------------------------------------------------------- checking one field
def check_field(field, raw, known):
    """Check one value. Returns (clean_value, error_message). error_message is None when it is fine."""
    text = "" if raw is None else str(raw).strip()
    label = field["label"]

    if text == "":
        if field["required"]:
            return None, f"{label} is required"
        return field.get("default"), None

    if len(text) > 120:
        return None, f"{label} is too long (at most 120 characters)"

    kind = field["type"]
    if kind == "id":
        if not ID_PATTERN.match(text) or len(text) > 20:
            return None, f"{label} may contain only letters, digits, - and _ (at most 20 characters)"
        return text, None

    if kind == "email":
        if not EMAIL_PATTERN.match(text):
            return None, f"{label} '{text}' is not a valid email address"
        return text, None

    if kind == "number":
        try:
            number = int(text)                     # "abc", "2.5" and "--5" are refused here
        except ValueError:
            return None, f"{label} must be a whole number"
        if number < field["min"] or number > field["max"]:
            return None, f"{label} must be between {field['min']} and {field['max']}"
        return number, None

    if kind == "yes_no":
        if text.lower() in YES_WORDS:
            return True, None
        if text.lower() in NO_WORDS:
            return False, None
        return None, f"{label} must be yes or no"

    if kind == "choice":
        word = EXAM_TYPE_WORDS.get(text.lower(), text.lower())
        if word not in field["choices"]:
            return None, f"{label} must be one of: regular, repeat (backlog)"
        return word, None

    if kind == "designation":
        allowed = known["designations"]
        if allowed and text not in allowed:
            return None, (f"Designation '{text}' has no duty limits in the active constraints "
                          f"({', '.join(allowed)}). Add it on the Constraints page first")
        return text, None

    return text, None                                   # plain text


# ---------------------------------------------------------------- checking a whole record
def check_record(record_type, form, known, is_new=True):
    """Check every field of one record. Returns (clean_values, list_of_errors).

    For a new record it also checks that the ID is unique, and for a registration that the
    student and course exist. When editing, the ID cannot change, so those checks are skipped."""
    values, errors = {}, []
    for field in record_type.fields:
        value, error = check_field(field, form.get(field["name"]), known)
        if error:
            errors.append(error)
        else:
            values[field["name"]] = value

    if errors or not is_new:
        return values, errors

    if record_type.key == "registrations":
        student, course = values["student_id"], values["course_code"]
        if student not in known["students"]:
            errors.append(f"Student {student} does not exist")
        if course not in known["courses"]:
            errors.append(f"Course {course} does not exist")
        if (student, course) in known["registrations"]:
            errors.append(f"Student {student} is already registered for {course}")
    else:
        new_id = values[record_type.id_field]
        if new_id in known[record_type.key]:
            errors.append(f"{record_type.first_label()} {new_id} already exists")
    return values, errors
