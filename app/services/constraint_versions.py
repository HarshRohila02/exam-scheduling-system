"""Scheduling constraints with version history - SRS 3.2.2 (MoSCoW R4, R18).

The examination staff fill in a simple form (no programming needed, R18):
  hard rules   students per invigilator, room fill %, no two exams on one day for a student,
               minimum and maximum invigilation duties per designation
  soft rules   how much the solver should care about an even spread of duties and about gaps
               between a student's exams (these become the objective of the CSP model)
  solver       how long the solver may search

Saving NEVER changes an old row. It always adds version N+1 and makes it the active one, so the
history of every change stays visible and an older version can be made active again.
The timetable generator always uses the active version: get_active_version().
"""
from app.extensions import db
from app.models import ConstraintVersion, DesignationLimit, Faculty
from app.services.calendar_service import ensure_default_sessions

# (form field / column, label shown to the admin, smallest allowed, largest allowed)
NUMBER_SETTINGS = [
    ("students_per_invigilator", "Students per invigilator", 1, 200),
    ("room_fill_percent", "Room fill percentage", 10, 100),
    ("duty_balance_weight", "Even spread of duties (weight)", 0, 100),
    ("exam_spacing_weight", "Gap between a student's exams (weight)", 0, 100),
    ("solver_time_limit_s", "Solver time limit (seconds)", 1, 600),
]

# Values of version 1, created on the very first start
FIRST_VERSION = {"students_per_invigilator": 30, "room_fill_percent": 100, "no_same_day_exams": True,
                 "duty_balance_weight": 10, "exam_spacing_weight": 1, "solver_time_limit_s": 30}
FIRST_LIMITS = [("Professor", 0, 2), ("Associate Professor", 1, 3), ("Assistant Professor", 1, 4)]


def get_active_version():
    """The constraint version the timetable generator must use (None only before the first start)."""
    return ConstraintVersion.query.filter_by(is_active=True).first()


def save_new_version(values, limits, username, note):
    """Store the settings as version N+1 and make it the only active version."""
    last = db.session.query(db.func.max(ConstraintVersion.version_no)).scalar() or 0
    for old in ConstraintVersion.query.filter_by(is_active=True):
        old.is_active = False
    version = ConstraintVersion(version_no=last + 1, is_active=True, created_by=username, note=note, **values)
    for designation, low, high in limits:
        version.limits.append(DesignationLimit(designation=designation, min_duties=low, max_duties=high))
    db.session.add(version)
    return version


def make_active(version):
    """Use an older version again. Nothing is copied or deleted; only the 'active' mark moves."""
    for old in ConstraintVersion.query.filter_by(is_active=True):
        old.is_active = False
    version.is_active = True


def ensure_defaults():
    """Called when the app starts: make sure the default sessions and constraint version 1 exist."""
    ensure_default_sessions()
    if ConstraintVersion.query.count() == 0:
        save_new_version(dict(FIRST_VERSION), FIRST_LIMITS, "system", "Default rules")
    db.session.commit()


# ---------------------------------------------------------------- reading the form
def read_constraint_form(form):
    """Check the submitted form. Returns (values, limits, errors); nothing may be saved if errors is not empty."""
    values, errors = {}, []
    for name, label, low, high in NUMBER_SETTINGS:
        text = (form.get(name) or "").strip()
        try:
            number = int(text)
        except ValueError:
            errors.append(f"{label} must be a whole number (you entered '{text}')")
            continue
        if number < low or number > high:
            errors.append(f"{label} must be between {low} and {high}")
            continue
        values[name] = number
    values["no_same_day_exams"] = form.get("no_same_day_exams") == "yes"

    # one table row per designation: designation[i], min_duties[i], max_duties[i]
    limits, seen = [], set()
    rows = zip(form.getlist("designation"), form.getlist("min_duties"), form.getlist("max_duties"))
    for designation, low_text, high_text in rows:
        designation = designation.strip()
        if not designation and not low_text.strip() and not high_text.strip():
            continue                                    # an empty spare row
        if not designation:
            errors.append("Each duty-limit row needs a designation")
            continue
        if designation in seen:
            errors.append(f"{designation} is listed twice")
            continue
        seen.add(designation)
        try:
            low, high = int(low_text), int(high_text)
        except ValueError:
            errors.append(f"{designation}: minimum and maximum duties must be whole numbers")
            continue
        if low < 0 or high < 1 or low > high:
            errors.append(f"{designation}: the minimum must be 0 or more, the maximum 1 or more, "
                          f"and the minimum cannot be above the maximum")
            continue
        limits.append((designation, low, high))

    if not limits:
        errors.append("Give duty limits for at least one designation")
    # a designation that faculty members have must keep its limits, or the generator cannot use them
    used = {row[0] for row in db.session.query(Faculty.designation).distinct()}
    for designation in sorted(used - seen):
        errors.append(f"{designation} is used by faculty records, so it must keep its duty limits")
    return values, limits, errors


# ---------------------------------------------------------------- history
def what_changed(older, newer):
    """Plain-word list of the differences between two versions, e.g. 'Students per invigilator: 30 -> 25'."""
    changes = []
    for name, label, _low, _high in NUMBER_SETTINGS:
        if getattr(older, name) != getattr(newer, name):
            changes.append(f"{label}: {getattr(older, name)} -> {getattr(newer, name)}")
    if older.no_same_day_exams != newer.no_same_day_exams:
        changes.append("Two exams on one day: " + ("not allowed" if newer.no_same_day_exams else "allowed"))

    before = {l.designation: (l.min_duties, l.max_duties) for l in older.limits}
    after = {l.designation: (l.min_duties, l.max_duties) for l in newer.limits}
    for designation in sorted(set(before) | set(after)):
        if designation not in after:
            changes.append(f"{designation} removed")
        elif designation not in before:
            changes.append(f"{designation} added ({after[designation][0]}-{after[designation][1]} duties)")
        elif before[designation] != after[designation]:
            changes.append(f"{designation} duties: {before[designation][0]}-{before[designation][1]} -> "
                           f"{after[designation][0]}-{after[designation][1]}")
    return changes


def version_history():
    """[(version, list_of_changes), ...] newest first. Each version is compared with the one saved before it."""
    versions = ConstraintVersion.query.order_by(ConstraintVersion.version_no).all()
    history = []
    for position, version in enumerate(versions):
        if position == 0:
            changes = ["First version"]
        else:
            changes = what_changed(versions[position - 1], version) or ["Same values as the version before"]
        history.append((version, changes))
    history.reverse()
    return history
