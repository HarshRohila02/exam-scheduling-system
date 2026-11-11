"""SRS 3.2.7 - 3.2.8 - Student Portal: the logged-in student's exam timetable, room and seat, instructions
and notifications.

Rules this file keeps:
- Timetable data comes only from app/services/published_view.py, so nothing shows before the admin publishes.
- The student is always taken from the login session, never from the address or a form (SRS 3.6: own data only).
"""
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from app.auth import log_action, student_required
from app.extensions import db
from app.models import Notification, Room, Student, User
from app.scheduler.problem import NotPublishedError
from app.services.published_view import get_exam_instructions, get_notifications, get_student_exams
from app.services.student_accounts import check_new_password, set_password

bp = Blueprint("student", __name__, url_prefix="/student")


# ---------------------------------------------------------------- helpers
def my_id():
    """The enrollment number of the logged-in student (their username)."""
    return session["username"]


def add_day_info(exams, today):
    """Add what the pages need to show each exam: weekday, and whether it is over, today or upcoming."""
    for exam in exams:
        exam_day = date.fromisoformat(exam["date"])
        exam["weekday"] = exam_day.strftime("%A")
        exam["date_text"] = exam_day.strftime("%d %b %Y")
        exam["days_left"] = (exam_day - today).days
        exam["state"] = "done" if exam_day < today else "today" if exam_day == today else "upcoming"
    return exams


def load_published(student_id):
    """(exams, instructions), or (None, "") while no timetable is published."""
    try:
        exams = add_day_info(get_student_exams(student_id), date.today())
        return exams, get_exam_instructions()
    except NotPublishedError:
        return None, ""


def greeting_for(hour):
    """A friendly hello for the dashboard, based on the time of day."""
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


@bp.app_context_processor
def unread_notifications():
    """The red number next to "Notifications" in the student menu, available on every page."""
    if session.get("role") != "STUDENT":
        return {}
    count = Notification.query.filter_by(recipient_type="STUDENT", recipient_id=session["username"],
                                         is_read=False).count()
    return {"student_unread": count}


# ---------------------------------------------------------------- pages
@bp.route("/")
@student_required
def dashboard():
    student = db.session.get(Student, my_id())
    exams, instructions = load_published(my_id())
    upcoming = [e for e in exams or [] if e["state"] != "done"]
    return render_template("student/dashboard.html", student=student, exams=exams, instructions=instructions,
                           greeting=greeting_for(datetime.now().hour),
                           next_exam=upcoming[0] if upcoming else None, upcoming_count=len(upcoming),
                           notifications=get_notifications("STUDENT", my_id())[:3])


@bp.route("/timetable")
@student_required
def timetable():
    """My exam timetable (SRS 3.2.7): every exam with date, session, time, room and seat. Printable."""
    exams, instructions = load_published(my_id())
    return render_template("student/timetable.html", student=db.session.get(Student, my_id()),
                           exams=exams, instructions=instructions)


@bp.route("/exam/<course_code>")
@student_required
def exam(course_code):
    """Room, seat and instructions for one exam (SRS 3.2.8). Only the student's own exams can be opened."""
    exams, instructions = load_published(my_id())
    if exams is None:
        return redirect(url_for("student.timetable"))
    mine = next((e for e in exams if e["course"] == course_code), None)
    if mine is None:
        abort(404, "You are not registered for this exam.")
    return render_template("student/exam.html", exam=mine, instructions=instructions,
                           room=db.session.get(Room, mine["room"]))


@bp.route("/notifications")
@student_required
def notifications():
    return render_template("student/notifications.html", notifications=get_notifications("STUDENT", my_id()))


@bp.route("/notifications/read", methods=["POST"])
@student_required
def mark_read():
    """Mark all of the student's notifications as read (only their own rows are touched)."""
    Notification.query.filter_by(recipient_type="STUDENT", recipient_id=my_id(), is_read=False) \
        .update({"is_read": True})
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("student.notifications"))


@bp.route("/password", methods=["GET", "POST"])
@student_required
def change_password():
    first_time = bool(session.get("must_change_password"))
    if request.method == "POST":
        user = User.query.filter_by(username=my_id()).one()
        new = request.form.get("new_password", "")
        errors = check_new_password(user, request.form.get("current_password", ""), new,
                                    request.form.get("confirm_password", ""),
                                    current_app.config["STUDENT_DEFAULT_PASSWORD"])
        if errors:
            flash("\n".join(errors), "danger")
        else:
            set_password(user, new)
            log_action("PASSWORD_CHANGE", "Student changed their password")
            db.session.commit()
            session.pop("must_change_password", None)
            flash("Your password has been changed.", "success")
            return redirect(url_for("student.dashboard"))
    return render_template("student/password.html", first_time=first_time)
