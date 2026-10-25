"""Versioned scheduling constraints (SRS 3.2.2: "maintains the version history of all modifications").

Saving the constraints form never edits an old row. It always adds version N+1 and makes it the
active one, so any earlier version can be looked at - or re-activated - later.
"""
from typing import List, Tuple

from app.extensions import db
from app.models import ConstraintVersion, DesignationLimit, ExamSession, Faculty

DEFAULT_LIMITS = [("Professor", 0, 2), ("Associate Professor", 1, 3), ("Assistant Professor", 1, 4)]
DEFAULT_SESSIONS = [("FN", "Forenoon", "09:30", "12:30"), ("AN", "Afternoon", "14:00", "17:00")]

# (field name, label, minimum, maximum) for the whole-number settings on the form
NUMBER_FIELDS = [
    ("students_per_invigilator", "Students per invigilator", 1, 200),
    ("room_fill_percent", "Room fill percentage", 10, 100),
    ("duty_balance_weight", "Duty balance weight", 0, 100),
    ("exam_spacing_weight", "Exam spacing weight", 0, 100),
    ("solver_time_limit_s", "Solver time limit (seconds)", 1, 600),
]


def get_active_version():
    return ConstraintVersion.query.filter_by(is_active=True).first()


def create_version(values: dict, limits: List[Tuple[str, int, int]], username: str, note: str) -> ConstraintVersion:
    """Add the next version, copy the designation limits into it and make it the active one."""
    latest = db.session.query(db.func.max(ConstraintVersion.version_no)).scalar() or 0
    ConstraintVersion.query.update({ConstraintVersion.is_active: False})
    version = ConstraintVersion(version_no=latest + 1, is_active=True, created_by=username,
                                note=note or "", **values)
    version.limits = [DesignationLimit(designation=d, min_duties=lo, max_duties=hi) for d, lo, hi in limits]
    db.session.add(version)
    return version


def activate_version(version: ConstraintVersion) -> None:
    ConstraintVersion.query.update({ConstraintVersion.is_active: False})
    version.is_active = True


def read_constraint_form(form):
    """Turn the submitted form into (values, limits, errors)."""
    values, errors = {}, []
    for name, label, low, high in NUMBER_FIELDS:
        try:
            number = int(form.get(name, "").strip())
        except ValueError:
            errors.append(f"{label} must be a whole number")
            continue
        if not low <= number <= high:
            errors.append(f"{label} must be between {low} and {high}")
        values[name] = number
    values["no_same_day_exams"] = form.get("no_same_day_exams") == "yes"

    limits, seen = [], set()
    names, mins, maxs = (form.getlist("designation"), form.getlist("min_duties"), form.getlist("max_duties"))
    for name, lo, hi in zip(names, mins, maxs):
        name = name.strip()
        if not name and not lo.strip() and not hi.strip():
            continue                                    # an unused blank row
        if not name:
            errors.append("Every duty-limit row needs a designation name")
            continue
        if name in seen:
            errors.append(f"Designation '{name}' appears twice")
            continue
        seen.add(name)
        try:
            lo_n, hi_n = int(lo), int(hi)
        except ValueError:
            errors.append(f"{name}: minimum and maximum duties must be whole numbers")
            continue
        if lo_n < 0 or hi_n < 1 or lo_n > hi_n:
            errors.append(f"{name}: need 0 <= minimum <= maximum and maximum >= 1")
            continue
        limits.append((name, lo_n, hi_n))
    if not limits:
        errors.append("Add at least one designation with duty limits")

    # every designation already used by a faculty member must keep a limit
    in_use = {d for (d,) in db.session.query(Faculty.designation).distinct()}
    for designation in sorted(in_use - seen):
        errors.append(f"Designation '{designation}' is used by faculty records and must keep its duty limits")
    return values, limits, errors


def ensure_defaults() -> None:
    """First start: create the FN/AN sessions and constraint version 1 so the portal is usable."""
    if ExamSession.query.count() == 0:
        db.session.add_all(ExamSession(code=c, name=n, start_time=s, end_time=e)
                           for c, n, s, e in DEFAULT_SESSIONS)
    if ConstraintVersion.query.count() == 0:
        create_version(
            {"students_per_invigilator": 30, "no_same_day_exams": True, "room_fill_percent": 100,
             "duty_balance_weight": 10, "exam_spacing_weight": 1, "solver_time_limit_s": 30},
            DEFAULT_LIMITS, "system", "Initial default constraints")
    db.session.commit()
