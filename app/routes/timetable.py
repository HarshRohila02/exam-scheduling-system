"""SRS 3.2.3 - Admin Portal: generate the timetable, review the draft, publish it.

The solving and saving work is done in app/services/timetable_service.py; these functions only
connect it to web pages.
"""
from collections import Counter, defaultdict

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.auth import admin_required, current_username, log_action
from app.extensions import db
from app.models import Course, ExamSession, Faculty, Student, Timetable
from app.scheduler.problem import InputValidationError
from app.services.constraint_versions import get_active_version
from app.services.timetable_service import (archive_timetable, generate_timetable, get_report, publish_timetable,
                                            readiness_checklist)

bp = Blueprint("timetable", __name__, url_prefix="/timetable")


@bp.route("/")
@admin_required
def index():
    checklist = readiness_checklist()
    timetables = Timetable.query.order_by(Timetable.id.desc()).all()
    return render_template(
        "timetable_index.html", checklist=checklist, ready=all(item[1] for item in checklist),
        timetables=timetables, version=get_active_version(),
        working=next((t for t in timetables if t.status in ("DRAFT", "CONFLICT")), None),
        published=next((t for t in timetables if t.status == "PUBLISHED"), None))


@bp.route("/generate", methods=["POST"])
@admin_required
def generate():
    try:
        timetable = generate_timetable(current_username())
    except InputValidationError as error:
        flash(str(error), "danger")
        return redirect(url_for("timetable.index"))
    if timetable.status == "DRAFT":
        flash(f"Draft timetable generated in {timetable.solve_seconds} seconds with no rule violations. "
              f"Review it, then publish.", "success")
    else:
        flash("No valid timetable could be generated. See the conflicts below.", "warning")
    return redirect(url_for("timetable.detail", timetable_id=timetable.id))


def build_view(timetable):
    """Arrange the saved rows into what the page shows: a date/session grid, seating lists, duty loads."""
    faculty = {f.faculty_id: f for f in Faculty.query.order_by(Faculty.faculty_id)}
    students = {s.student_id: s.name for s in Student.query}
    courses = {c.course_code: c.name for c in Course.query}

    exams, grid, load = [], defaultdict(lambda: defaultdict(list)), Counter()
    for entry in sorted(timetable.entries, key=lambda e: (e.exam_date, e.session_code, e.course_code)):
        rooms = []
        for room in sorted(entry.rooms, key=lambda r: r.room_id):
            staff = [d.faculty_id for d in entry.duties if d.room_id == room.room_id]
            load.update(staff)
            seats = [s for s in sorted(entry.seats, key=lambda s: s.id) if s.room_id == room.room_id]
            rooms.append({"room_id": room.room_id, "count": room.students_count,
                          "invigilators": [(f, faculty[f].name if f in faculty else f) for f in staff],
                          "seats": [(s.seat_no, s.student_id, students.get(s.student_id, ""), s.exam_type)
                                    for s in seats]})
        exam = {"entry": entry, "name": courses.get(entry.course_code, ""), "rooms": rooms,
                "total": sum(r["count"] for r in rooms),
                "backlog": sum(1 for r in rooms for s in r["seats"] if s[3] == "backlog")}
        exams.append(exam)
        grid[entry.exam_date][entry.session_code].append(exam)

    duty_rows = [(f.faculty_id, f.name, f.designation, load.get(f.faculty_id, 0)) for f in faculty.values()]
    return {"exams": exams, "grid": dict(sorted(grid.items())), "duty_rows": duty_rows,
            "sessions": ExamSession.query.order_by(ExamSession.start_time).all(), "report": get_report(timetable)}


@bp.route("/<int:timetable_id>")
@admin_required
def detail(timetable_id):
    timetable = db.get_or_404(Timetable, timetable_id)
    return render_template("timetable_detail.html", timetable=timetable, **build_view(timetable))


@bp.route("/<int:timetable_id>/publish", methods=["POST"])
@admin_required
def publish(timetable_id):
    timetable = db.get_or_404(Timetable, timetable_id)
    try:
        publish_timetable(timetable, request.form.get("instructions", ""))
        flash("Timetable published. Faculty and students can now see their schedules.", "success")
    except (ValueError, InputValidationError) as error:
        flash(str(error), "danger")
    return redirect(url_for("timetable.detail", timetable_id=timetable_id))


@bp.route("/<int:timetable_id>/archive", methods=["POST"])
@admin_required
def archive(timetable_id):
    timetable = db.get_or_404(Timetable, timetable_id)
    try:
        archive_timetable(timetable)
        flash("Timetable archived. It is no longer visible to faculty and students.", "info")
    except ValueError as error:
        flash(str(error), "danger")
    return redirect(url_for("timetable.index"))


@bp.route("/<int:timetable_id>/discard", methods=["POST"])
@admin_required
def discard(timetable_id):
    timetable = db.get_or_404(Timetable, timetable_id)
    if timetable.status not in ("DRAFT", "CONFLICT"):
        abort(400, "Only a draft or conflict result can be discarded.")
    db.session.delete(timetable)
    log_action("DISCARD_TIMETABLE", f"Timetable {timetable_id} discarded")
    db.session.commit()
    flash("Draft discarded.", "info")
    return redirect(url_for("timetable.index"))
