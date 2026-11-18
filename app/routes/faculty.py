"""Personal duties, one-way duty transfers and the Exam Cell inbox."""

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from sqlalchemy.exc import IntegrityError

from app.auth import admin_required, current_username, log_action, remember_faculty
from app.extensions import db
from app.models import DutySwap, ExamSession, Faculty, InvigilationDuty, Notification, Timetable, User
from app.scheduler.problem import InputValidationError
from app.services.duty_swaps import CELL, record_event
from app.services.published_view import get_faculty_duties, get_notifications
from app.services.student_accounts import check_new_password, set_password
from app.services.timetable_service import validate_saved_timetable

bp = Blueprint("faculty", __name__)



@bp.before_request
def require_faculty():
    if request.endpoint in ("faculty.cell", "faculty.cell_read"):
        return  # Both Exam Cell views use admin_required.
    if session.get("role") != "FACULTY":
        return redirect(url_for("auth.login", next=request.path))
    g.person = db.session.get(Faculty, current_username())
    if g.person is None:
        session.clear()
        return redirect(url_for("auth.login"))


def inbox(role, recipient):
    return Notification.query.filter_by(recipient_type=role, recipient_id=recipient)


@bp.app_context_processor
def badges():
    if session.get("role") == "ADMIN":
        return {"cell_unread": inbox("ADMIN", CELL).filter_by(is_read=False).count()}
    if session.get("role") == "FACULTY":
        return {"faculty_pending": DutySwap.query.filter_by(recipient=current_username(), status="Pending").count(),
                "faculty_unread": inbox("FACULTY", current_username()).filter_by(is_read=False).count()}
    return {}


def schedule_page(page):
    schedules = []
    for status in ("PUBLISHED", "DRAFT"):
        table = Timetable.query.filter_by(status=status).order_by(Timetable.id.desc()).first()
        if table:
            rows = get_faculty_duties(current_username(), table)
            schedules.append((table, rows))
    next_duty = None
    maximum = None
    if schedules:
        table, duties = schedules[0]
        next_duty = duties[0] if duties else None
        limit = table.constraint_version.limit_for(g.person.designation)
        maximum = g.person.max_duties_override or (limit.max_duties if limit else None)
    return render_template("faculty/schedule.html", person=g.person, schedules=schedules,
                           next_duty=next_duty, maximum=maximum, page=page)


@bp.get("/faculty/")
def dashboard():
    return schedule_page("Dashboard")


@bp.get("/faculty/timetable")
def timetable():
    return schedule_page("My Timetable")


@bp.route("/faculty/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        user = User.query.filter_by(username=current_username(), role="FACULTY").one()
        new = request.form.get("new_password", "")
        errors = check_new_password(user, request.form.get("current_password", ""), new,
                                    request.form.get("confirm_password", ""),
                                    current_app.config["FACULTY_DEFAULT_PASSWORD"])
        if errors:
            flash(" ".join(errors), "danger")
        else:
            set_password(user, new)
            if session.get("faculty_browser"):
                remember_faculty(user, session["faculty_browser"])
            log_action("PASSWORD_CHANGE", "Faculty changed their password")
            db.session.commit()
            session.pop("must_change_password", None)
            return redirect(url_for("faculty.dashboard"))
    return render_template("student/password.html", first_time=session.get("must_change_password"))


def lock_published():
    # Start a fresh transaction after the identity lookup. Serialize all duty changes
    # on this timetable before reading loads, clashes or pending requests.
    db.session.rollback()
    matched = Timetable.query.filter_by(status="PUBLISHED").update(
        {"status": "PUBLISHED"}, synchronize_session=False)
    if matched != 1:
        raise ValueError("A published timetable is required.")


def request_transfer():
    lock_published()
    duty = db.get_or_404(InvigilationDuty, request.form.get("duty", type=int) or 0)
    if duty.faculty_id != current_username() or duty.entry.timetable.status != "PUBLISHED":
        abort(403)
    recipient = request.form.get("recipient", "")
    reason = request.form.get("reason", "").strip()
    if not reason:
        raise ValueError("Please add a reason")
    if len(reason) > 500:
        raise ValueError("Enter a reason between 1 and 500 characters.")
    if (recipient == current_username() or db.session.get(Faculty, recipient) is None
            or not User.query.filter_by(username=recipient, role="FACULTY").first()):
        raise ValueError("Select another faculty member with an active login.")
    if DutySwap.query.filter_by(pending_duty=duty.id).first():
        raise ValueError("Request already pending for this duty")
    swap = DutySwap(duty=duty, sender=current_username(), recipient=recipient, reason=reason, pending_duty=duty.id)
    db.session.add(swap)
    db.session.flush()
    record_event(swap, "requested")


@bp.route("/faculty/swaps", methods=["GET", "POST"])
def swaps():
    if request.method == "POST":
        try:
            request_transfer()
            db.session.commit()
            flash("Request sent. The Exam Cell has been notified.", "success")
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else "A request already exists. Reload the page.", "warning")
        return redirect(url_for("faculty.swaps"))
    published = Timetable.query.filter_by(status="PUBLISHED").first()
    people = Faculty.query.join(User, User.username == Faculty.faculty_id).filter(
        User.role == "FACULTY", Faculty.faculty_id != current_username()).order_by(Faculty.name)
    history = DutySwap.query.order_by(DutySwap.id.desc())
    return render_template("faculty/swaps.html", people=people, duties=get_faculty_duties(current_username(), published)
                           if published else [], incoming=history.filter_by(recipient=current_username()),
                           sent=history.filter_by(sender=current_username()))


@bp.post("/faculty/swaps/<int:swap_id>/<action>")
def decide(swap_id, action):
    if action not in ("approve", "reject"):
        abort(404)
    try:
        lock_published()
        swap = db.get_or_404(DutySwap, swap_id)
        if swap.recipient != current_username():
            abort(403)
        if (swap.status != "Pending" or swap.duty.faculty_id != swap.sender
                or swap.duty.entry.timetable.status != "PUBLISHED"):
            raise ValueError("This request is no longer pending on the published timetable.")
        if action == "approve":
            entry = swap.duty.entry
            session_times = {s.code: s for s in ExamSession.query}
            requested = session_times.get(entry.session_code)
            for other in get_faculty_duties(swap.recipient, entry.timetable):
                if other["date"] != entry.exam_date.isoformat():
                    continue
                same_slot = other["session"] == entry.session_code
                overlaps = (requested and other["start_time"] < requested.end_time
                            and requested.start_time < other["end_time"])
                if same_slot or overlaps:
                    raise ValueError("Clash: you already have a duty at that time")
            swap.duty.faculty_id = swap.recipient
            errors = validate_saved_timetable(swap.duty.entry.timetable)
            if errors:
                raise ValueError("Transfer blocked: " + " ".join(errors[:3]))
        swap.status = "Approved" if action == "approve" else "Rejected"
        swap.pending_duty = None
        record_event(swap, swap.status.lower())
        db.session.commit()
        flash("Decision saved and the Exam Cell notified.", "success")
    except (ValueError, InputValidationError) as error:
        db.session.rollback()
        flash(str(error), "warning")
    return redirect(url_for("faculty.swaps"))


@bp.get("/faculty/notifications")
def notifications():
    return render_template("faculty/inbox.html", notes=get_notifications("FACULTY", current_username()))


def mark_read(role, recipient, endpoint):
    inbox(role, recipient).filter_by(is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for(endpoint))


@bp.post("/faculty/notifications/read")
def notifications_read():
    return mark_read("FACULTY", current_username(), "faculty.notifications")


@bp.get("/exam-cell/faculty-requests")
@admin_required
def cell():
    return render_template("faculty/inbox.html", cell=True, notes=get_notifications("ADMIN", CELL),
                           history=DutySwap.query.order_by(DutySwap.id.desc()))


@bp.post("/exam-cell/faculty-requests/read")
@admin_required
def cell_read():
    return mark_read("ADMIN", CELL, "faculty.cell")
