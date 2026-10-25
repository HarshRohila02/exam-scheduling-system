"""Database tables (SRS section 3.4 - Logical Database Requirements).

Every class below is one table. SQLAlchemy turns each Column into a database column,
and relationship() lets us walk from a parent row to its children in Python.
"""
from datetime import datetime

from app.extensions import db


# ---------------------------------------------------------------- users
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="ADMIN")   # ADMIN / FACULTY / STUDENT


# ---------------------------------------------------------------- master data (SRS 3.2.1)
class Student(db.Model):
    student_id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    program = db.Column(db.String(50), nullable=False)
    section = db.Column(db.String(10), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    email = db.Column(db.String(120))
    registrations = db.relationship("Registration", backref="student", cascade="all, delete-orphan")


class Course(db.Model):
    course_code = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    exam_duration_minutes = db.Column(db.Integer, nullable=False, default=180)
    registrations = db.relationship("Registration", backref="course", cascade="all, delete-orphan")


class Registration(db.Model):
    """A student registered for a course exam. exam_type is "regular" or "backlog"."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), db.ForeignKey("student.student_id"), nullable=False)
    course_code = db.Column(db.String(20), db.ForeignKey("course.course_code"), nullable=False)
    exam_type = db.Column(db.String(10), nullable=False, default="regular")
    __table_args__ = (db.UniqueConstraint("student_id", "course_code", name="uq_student_course"),)


class Faculty(db.Model):
    faculty_id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    designation = db.Column(db.String(50), nullable=False)
    department = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120))
    max_duties_override = db.Column(db.Integer)          # optional personal limit
    unavailability = db.relationship("FacultyUnavailability", backref="faculty", cascade="all, delete-orphan")


class FacultyUnavailability(db.Model):
    """A date (and optionally one session) on which the faculty member cannot invigilate."""
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.String(20), db.ForeignKey("faculty.faculty_id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    session_code = db.Column(db.String(10))              # None = the whole day


class Room(db.Model):
    room_id = db.Column(db.String(20), primary_key=True)
    building = db.Column(db.String(50), nullable=False, default="Main")
    capacity = db.Column(db.Integer, nullable=False)
    is_available = db.Column(db.Boolean, nullable=False, default=True)


# ---------------------------------------------------------------- calendar (SRS 3.2.2)
class ExamCalendar(db.Model):
    """The exam period. There is only ever one row."""
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    skip_sundays = db.Column(db.Boolean, nullable=False, default=True)


class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    reason = db.Column(db.String(100))


class ExamSession(db.Model):
    code = db.Column(db.String(10), primary_key=True)    # FN / AN
    name = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)  # "09:30"
    end_time = db.Column(db.String(5), nullable=False)    # "12:30"


# ---------------------------------------------------------------- versioned constraints (SRS 3.2.2)
class ConstraintVersion(db.Model):
    """One saved set of scheduling rules. Saving changes never edits a row - it adds a new version."""
    id = db.Column(db.Integer, primary_key=True)
    version_no = db.Column(db.Integer, unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    created_by = db.Column(db.String(50))
    note = db.Column(db.String(200))
    # hard constraint values
    students_per_invigilator = db.Column(db.Integer, nullable=False, default=30)
    no_same_day_exams = db.Column(db.Boolean, nullable=False, default=True)
    room_fill_percent = db.Column(db.Integer, nullable=False, default=100)
    # soft constraint weights
    duty_balance_weight = db.Column(db.Integer, nullable=False, default=10)
    exam_spacing_weight = db.Column(db.Integer, nullable=False, default=1)
    solver_time_limit_s = db.Column(db.Integer, nullable=False, default=30)
    limits = db.relationship("DesignationLimit", backref="version", cascade="all, delete-orphan")

    def limit_for(self, designation):
        for lim in self.limits:
            if lim.designation == designation:
                return lim
        return None


class DesignationLimit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey("constraint_version.id"), nullable=False)
    designation = db.Column(db.String(50), nullable=False)
    min_duties = db.Column(db.Integer, nullable=False, default=0)
    max_duties = db.Column(db.Integer, nullable=False, default=3)


# ---------------------------------------------------------------- generated schedule (SRS 3.2.3)
class Timetable(db.Model):
    """status: DRAFT -> PUBLISHED -> ARCHIVED, or CONFLICT when no feasible schedule was found."""
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(12), nullable=False, default="DRAFT")
    constraint_version_id = db.Column(db.Integer, db.ForeignKey("constraint_version.id"))
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    solver_status = db.Column(db.String(20))
    solve_seconds = db.Column(db.Float)
    report = db.Column(db.Text)                          # JSON: validation violations / conflicts
    instructions = db.Column(db.Text)                    # exam instructions shown to students
    published_at = db.Column(db.DateTime)
    entries = db.relationship("TimetableEntry", backref="timetable", cascade="all, delete-orphan")
    constraint_version = db.relationship("ConstraintVersion")


class TimetableEntry(db.Model):
    """One exam placed on one (date, session). Plain strings are used for course_code so that
    archived timetables survive later edits to the master data."""
    id = db.Column(db.Integer, primary_key=True)
    timetable_id = db.Column(db.Integer, db.ForeignKey("timetable.id"), nullable=False)
    course_code = db.Column(db.String(20), nullable=False)
    exam_date = db.Column(db.Date, nullable=False)
    session_code = db.Column(db.String(10), nullable=False)
    rooms = db.relationship("ExamRoom", backref="entry", cascade="all, delete-orphan")
    seats = db.relationship("SeatAssignment", backref="entry", cascade="all, delete-orphan")
    duties = db.relationship("InvigilationDuty", backref="entry", cascade="all, delete-orphan")


class ExamRoom(db.Model):
    """A room used by an exam (an exam can use several rooms)."""
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("timetable_entry.id"), nullable=False)
    room_id = db.Column(db.String(20), nullable=False)
    students_count = db.Column(db.Integer, nullable=False)


class SeatAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("timetable_entry.id"), nullable=False)
    room_id = db.Column(db.String(20), nullable=False)
    student_id = db.Column(db.String(20), nullable=False)
    seat_no = db.Column(db.String(20), nullable=False)
    exam_type = db.Column(db.String(10), nullable=False, default="regular")


class InvigilationDuty(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("timetable_entry.id"), nullable=False)
    room_id = db.Column(db.String(20), nullable=False)
    faculty_id = db.Column(db.String(20), nullable=False)


class Notification(db.Model):
    """Written when a timetable is published; the Faculty/Student portals read these."""
    id = db.Column(db.Integer, primary_key=True)
    recipient_type = db.Column(db.String(10), nullable=False)   # FACULTY / STUDENT
    recipient_id = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_read = db.Column(db.Boolean, nullable=False, default=False)


class AuditLog(db.Model):
    """Every schedule-affecting action by the admin is recorded here (SRS 3.6 Security)."""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.now)
    username = db.Column(db.String(50))
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.String(255))
