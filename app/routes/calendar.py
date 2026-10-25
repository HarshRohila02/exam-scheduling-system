"""SRS 3.2.2 - University Calendar: exam period, sessions and holidays.

The valid exam slots (date + session) that the CSP model chooses from are worked out here from
the period, the sessions and the holidays.
"""
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import admin_required, log_action
from app.extensions import db
from app.models import ExamCalendar, ExamSession, FacultyUnavailability, Holiday
from app.scheduler.problem import build_slots

bp = Blueprint("calendar", __name__, url_prefix="/calendar")

MAX_PERIOD_DAYS = 120
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _parse_date(text):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def calendar_days(calendar, holidays, sessions):
    """Day-by-day preview: which days are exam days and why the others are skipped."""
    holiday_map = {h.date: h.reason or "Holiday" for h in holidays}
    days, day = [], calendar.start_date
    while day <= calendar.end_date:
        if day in holiday_map:
            days.append((day, "Holiday", holiday_map[day]))
        elif calendar.skip_sundays and day.weekday() == 6:
            days.append((day, "Sunday", "Sunday"))
        else:
            days.append((day, "Exam day", ", ".join(s.code for s in sessions)))
        day += timedelta(days=1)
    return days


@bp.route("/")
@admin_required
def index():
    calendar = ExamCalendar.query.first()
    sessions = ExamSession.query.order_by(ExamSession.start_time).all()
    holidays = Holiday.query.order_by(Holiday.date).all()
    days = calendar_days(calendar, holidays, sessions) if calendar else []
    exam_days = sum(1 for d in days if d[1] == "Exam day")
    return render_template("calendar.html", calendar=calendar, sessions=sessions, holidays=holidays,
                           days=days, exam_days=exam_days, slot_count=exam_days * len(sessions))


@bp.route("/period", methods=["POST"])
@admin_required
def save_period():
    start, end = _parse_date(request.form.get("start_date")), _parse_date(request.form.get("end_date"))
    if not start or not end:
        flash("Enter both a start date and an end date.", "danger")
    elif end < start:
        flash("The end date cannot be before the start date.", "danger")
    elif (end - start).days + 1 > MAX_PERIOD_DAYS:
        flash(f"The exam period can be at most {MAX_PERIOD_DAYS} days long.", "danger")
    else:
        calendar = ExamCalendar.query.first() or ExamCalendar(start_date=start, end_date=end)
        calendar.start_date, calendar.end_date = start, end
        calendar.skip_sundays = request.form.get("skip_sundays") == "yes"
        db.session.add(calendar)
        log_action("UPDATE_CALENDAR", f"Exam period {start} to {end}, skip Sundays: {calendar.skip_sundays}")
        db.session.commit()
        flash("Exam period saved.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/holidays/add", methods=["POST"])
@admin_required
def add_holiday():
    day = _parse_date(request.form.get("date"))
    if not day:
        flash("Choose a valid holiday date.", "danger")
    elif Holiday.query.filter_by(date=day).first():
        flash(f"{day} is already a holiday.", "warning")
    else:
        db.session.add(Holiday(date=day, reason=request.form.get("reason", "").strip()[:100]))
        log_action("ADD_HOLIDAY", str(day))
        db.session.commit()
        flash("Holiday added.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/holidays/<int:holiday_id>/delete", methods=["POST"])
@admin_required
def delete_holiday(holiday_id):
    holiday = db.get_or_404(Holiday, holiday_id)
    db.session.delete(holiday)
    log_action("REMOVE_HOLIDAY", str(holiday.date))
    db.session.commit()
    flash("Holiday removed.", "success")
    return redirect(url_for("calendar.index"))


def _check_session_fields(code, name, start, end):
    """Return an error message for bad session input, or None when it is fine."""
    if not re.match(r"^[A-Za-z0-9]{1,10}$", code):
        return "Session code must be 1-10 letters or digits (for example FN)."
    if not name:
        return "Session name is required."
    if not TIME_RE.match(start) or not TIME_RE.match(end):
        return "Times must look like 09:30 (24-hour clock)."
    if datetime.strptime(start, "%H:%M") >= datetime.strptime(end, "%H:%M"):
        return "A session must end after it starts."
    return None


@bp.route("/sessions/save", methods=["POST"])
@admin_required
def save_sessions():
    problems = []
    for session in ExamSession.query.all():
        name = request.form.get(f"name_{session.code}", "").strip()
        start = request.form.get(f"start_{session.code}", "").strip()
        end = request.form.get(f"end_{session.code}", "").strip()
        error = _check_session_fields(session.code, name, start, end)
        if error:
            problems.append(f"{session.code}: {error}")
        else:
            session.name, session.start_time, session.end_time = name, start, end
    if problems:
        db.session.rollback()
        for message in problems:
            flash(message, "danger")
    else:
        log_action("UPDATE_SESSIONS", "Session times changed")
        db.session.commit()
        flash("Session times saved.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/sessions/add", methods=["POST"])
@admin_required
def add_session():
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    start, end = request.form.get("start", "").strip(), request.form.get("end", "").strip()
    error = _check_session_fields(code, name, start, end)
    if not error and db.session.get(ExamSession, code):
        error = f"Session {code} already exists."
    if error:
        flash(error, "danger")
    else:
        db.session.add(ExamSession(code=code, name=name, start_time=start, end_time=end))
        log_action("ADD_SESSION", f"{code} {start}-{end}")
        db.session.commit()
        flash(f"Session {code} added.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/sessions/<code>/delete", methods=["POST"])
@admin_required
def delete_session(code):
    session = db.get_or_404(ExamSession, code)
    if ExamSession.query.count() <= 1:
        flash("At least one session must remain.", "danger")
    else:
        FacultyUnavailability.query.filter_by(session_code=code).delete()
        db.session.delete(session)
        log_action("REMOVE_SESSION", code)
        db.session.commit()
        flash(f"Session {code} removed.", "success")
    return redirect(url_for("calendar.index"))
