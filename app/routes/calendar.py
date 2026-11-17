"""Admin Portal - University Calendar page (SRS 3.2.2, MoSCoW R3).

Use case: Configure Examination Calendar. DFD Level 1 process 3.0 (store D6 University Calendar).

    /calendar/                          the page: period, sessions, holidays, day-by-day preview, history
    /calendar/period                    save the exam period
    /calendar/holidays                  add a holiday
    /calendar/holidays/<id>/delete      remove a holiday
    /calendar/sessions                  change the times of the existing sessions
    /calendar/sessions/new              add a session
    /calendar/sessions/<code>/delete    remove a session

Every change that is accepted is also saved as a calendar version (history).
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import admin_required, current_username, log_action
from app.extensions import db
from app.models import ExamCalendar, ExamSession, FacultyUnavailability, Holiday
from app.services.calendar_service import (calendar_history, check_holiday, check_overlap, check_period,
                                           check_session, day_by_day, read_date, save_calendar_version)

bp = Blueprint("calendar", __name__, url_prefix="/calendar")


def back():
    return redirect(url_for("calendar.index"))


def show_errors(messages):
    for message in messages:
        flash(message, "danger")


@bp.route("/")
@admin_required
def index():
    calendar = ExamCalendar.query.first()
    sessions = ExamSession.query.order_by(ExamSession.start_time).all()
    holidays = Holiday.query.order_by(Holiday.date).all()
    days = day_by_day(calendar, holidays, sessions) if calendar else []
    exam_days = len([d for d in days if d["exam_day"]])
    return render_template("calendar.html", calendar=calendar, sessions=sessions, holidays=holidays, days=days,
                           exam_days=exam_days, slots=exam_days * len(sessions), history=calendar_history())


# ---------------------------------------------------------------- exam period
@bp.route("/period", methods=["POST"])
@admin_required
def save_period():
    start, end = read_date(request.form.get("start_date")), read_date(request.form.get("end_date"))
    problem = check_period(start, end)
    if problem:
        show_errors([problem])
        return back()

    calendar = ExamCalendar.query.first()
    if calendar is None:
        calendar = ExamCalendar(start_date=start, end_date=end)
        db.session.add(calendar)
    calendar.start_date, calendar.end_date = start, end
    calendar.skip_sundays = request.form.get("skip_sundays") == "yes"
    log_action("UPDATE_CALENDAR", f"Exam period {start} to {end}")
    version = save_calendar_version(f"Exam period set to {start} - {end}", current_username())
    db.session.commit()
    flash(f"Exam period saved (calendar version {version.version_no}). The timetable generator will use it.",
          "success")

    left_out = Holiday.query.filter((Holiday.date < start) | (Holiday.date > end)).count()
    if left_out:
        flash(f"{left_out} holiday(s) are now outside the exam period and have no effect.", "warning")
    return back()


# ---------------------------------------------------------------- holidays
@bp.route("/holidays", methods=["POST"])
@admin_required
def add_holiday():
    day = read_date(request.form.get("date"))
    problem = check_holiday(day)
    if problem:
        show_errors([problem])
        return back()
    db.session.add(Holiday(date=day, reason=request.form.get("reason", "").strip()[:100]))
    log_action("ADD_HOLIDAY", str(day))
    save_calendar_version(f"Holiday added: {day}", current_username())
    db.session.commit()
    flash(f"Holiday {day} added.", "success")
    return back()


@bp.route("/holidays/<int:holiday_id>/delete", methods=["POST"])
@admin_required
def delete_holiday(holiday_id):
    holiday = db.get_or_404(Holiday, holiday_id)
    day = holiday.date
    db.session.delete(holiday)
    log_action("REMOVE_HOLIDAY", str(day))
    save_calendar_version(f"Holiday removed: {day}", current_username())
    db.session.commit()
    flash(f"Holiday {day} removed.", "success")
    return back()


# ---------------------------------------------------------------- sessions
@bp.route("/sessions", methods=["POST"])
@admin_required
def save_sessions():
    """Change name and times of every existing session in one go."""
    sessions = ExamSession.query.all()
    new_values, problems = [], []
    for s in sessions:
        name = request.form.get(f"name_{s.code}", "").strip()
        start = request.form.get(f"start_{s.code}", "").strip()
        end = request.form.get(f"end_{s.code}", "").strip()
        problem = check_session(s.code, name, start, end)
        if problem:
            problems.append(problem)
        new_values.append((s, name, start, end))

    if not problems:
        overlap = check_overlap([(s.code, start, end) for s, _name, start, end in new_values])
        if overlap:
            problems.append(overlap)
    if problems:
        show_errors(problems)                       # nothing is changed
        return back()

    for s, name, start, end in new_values:
        s.name, s.start_time, s.end_time = name, start, end
    log_action("UPDATE_SESSIONS", ", ".join(f"{s.code} {start}-{end}" for s, _n, start, end in new_values))
    save_calendar_version("Session times changed", current_username())
    db.session.commit()
    flash("Session times saved.", "success")
    return back()


@bp.route("/sessions/new", methods=["POST"])
@admin_required
def add_session():
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    start, end = request.form.get("start", "").strip(), request.form.get("end", "").strip()

    problem = check_session(code, name, start, end)
    if not problem and db.session.get(ExamSession, code):
        problem = f"Session {code} already exists."
    if not problem:
        existing = [(s.code, s.start_time, s.end_time) for s in ExamSession.query.all()]
        problem = check_overlap(existing + [(code, start, end)])
    if problem:
        show_errors([problem])
        return back()

    db.session.add(ExamSession(code=code, name=name, start_time=start, end_time=end))
    log_action("ADD_SESSION", f"{code} {start}-{end}")
    save_calendar_version(f"Session added: {code} {start}-{end}", current_username())
    db.session.commit()
    flash(f"Session {code} added.", "success")
    return back()


@bp.route("/sessions/<code>/delete", methods=["POST"])
@admin_required
def delete_session(code):
    session = db.get_or_404(ExamSession, code)
    if ExamSession.query.count() == 1:
        show_errors(["At least one session must remain."])
        return back()
    # unavailable dates recorded for this session no longer mean anything
    FacultyUnavailability.query.filter_by(session_code=code).delete()
    db.session.delete(session)
    log_action("REMOVE_SESSION", code)
    save_calendar_version(f"Session removed: {code}", current_username())
    db.session.commit()
    flash(f"Session {code} removed.", "success")
    return back()
