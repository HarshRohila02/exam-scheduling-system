"""SRS 3.2.1 - Admin Portal Data Management.

One set of routes serves Students, Courses, Faculty, Rooms and Registrations. Which fields exist and
how they are checked is described in app/services/validation.py (ENTITIES), so adding a field
there is enough to get it into the form, the table and the CSV upload.
"""
from datetime import date

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request, url_for)

from app.auth import admin_required, log_action
from app.extensions import db
from app.models import (ExamRoom, ExamSession, Faculty, FacultyUnavailability, InvigilationDuty,
                        SeatAssignment, Timetable, TimetableEntry)
from app.services.csv_import import import_csv, template_csv
from app.services.validation import ENTITIES, load_context, validate_record

bp = Blueprint("data", __name__, url_prefix="/data")

LIVE_STATUSES = ("DRAFT", "PUBLISHED")      # timetables that still depend on the master data
PER_PAGE = 25


# ---------------------------------------------------------------- small helpers
def get_entity(entity_key):
    entity = ENTITIES.get(entity_key)
    if entity is None:
        abort(404)
    return entity


def get_row(entity, key):
    """Find one record by the value in the URL (registrations use a numeric id)."""
    if entity.pk_attr == "id":
        if not key.isdigit():
            abort(404)
        key = int(key)
    row = entity.model.query.filter(getattr(entity.model, entity.pk_attr) == key).first()
    if row is None:
        abort(404)
    return row


def in_use_message(entity, row):
    """If a draft/published timetable uses this record, return why it cannot be deleted."""
    def live(model, condition):
        query = (model.query.join(TimetableEntry, model.entry_id == TimetableEntry.id)
                 .join(Timetable, TimetableEntry.timetable_id == Timetable.id)
                 .filter(Timetable.status.in_(LIVE_STATUSES), condition))
        return query.first() is not None

    used = False
    if entity.key == "courses":
        used = (TimetableEntry.query.join(Timetable).filter(
            Timetable.status.in_(LIVE_STATUSES), TimetableEntry.course_code == row.course_code).first())
    elif entity.key == "registrations":
        used = (TimetableEntry.query.join(Timetable).filter(
            Timetable.status.in_(LIVE_STATUSES), TimetableEntry.course_code == row.course_code).first())
    elif entity.key == "students":
        used = live(SeatAssignment, SeatAssignment.student_id == row.student_id)
    elif entity.key == "faculty":
        used = live(InvigilationDuty, InvigilationDuty.faculty_id == row.faculty_id)
    elif entity.key == "rooms":
        used = live(ExamRoom, ExamRoom.room_id == row.room_id)
    if used:
        return (f"Cannot delete this {entity.singular.lower()}: a draft or published timetable uses it. "
                f"Archive the timetable or generate a new one first.")
    return None


# ---------------------------------------------------------------- list + search
@bp.route("/<entity_key>/")
@admin_required
def list_view(entity_key):
    entity = get_entity(entity_key)
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    query = entity.model.query
    if search:
        text_columns = [getattr(entity.model, f.name) for f in entity.fields
                        if f.kind in ("id", "text", "email", "designation")]
        query = query.filter(db.or_(*[col.ilike(f"%{search}%") for col in text_columns]))
    rows = query.order_by(getattr(entity.model, entity.pk_attr)).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template("data_list.html", entity=entity, rows=rows, search=search)


# ---------------------------------------------------------------- add
@bp.route("/<entity_key>/new", methods=["GET", "POST"])
@admin_required
def create(entity_key):
    entity = get_entity(entity_key)
    ctx = load_context()
    values = {}
    if request.method == "POST":
        values = request.form
        clean, errors = validate_record(entity, request.form, ctx)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            row = entity.model(**clean)
            db.session.add(row)
            log_action("CREATE", f"{entity.singular}: {', '.join(str(v) for v in list(clean.values())[:2])}")
            db.session.commit()
            flash(f"{entity.singular} added.", "success")
            return redirect(url_for("data.list_view", entity_key=entity.key))
    return render_template("data_form.html", entity=entity, values=values, editing=False,
                           designations=ctx["designations"])


# ---------------------------------------------------------------- edit
@bp.route("/<entity_key>/<key>/edit", methods=["GET", "POST"])
@admin_required
def edit(entity_key, key):
    entity = get_entity(entity_key)
    row = get_row(entity, key)
    ctx = load_context()
    if request.method == "POST":
        raw = request.form.to_dict()
        for name in entity.locked_on_edit:               # IDs cannot be changed
            raw[name] = getattr(row, name)
        clean, errors = validate_record(entity, raw, ctx, editing=True)
        if errors:
            for message in errors:
                flash(message, "danger")
            values = raw
        else:
            for name, value in clean.items():
                if name not in entity.locked_on_edit:
                    setattr(row, name, value)
            log_action("UPDATE", f"{entity.singular} {key}")
            db.session.commit()
            flash(f"{entity.singular} updated.", "success")
            return redirect(url_for("data.list_view", entity_key=entity.key))
    else:
        values = {f.name: getattr(row, f.name) for f in entity.fields}
    return render_template("data_form.html", entity=entity, values=values, editing=True,
                           designations=ctx["designations"])


# ---------------------------------------------------------------- delete
@bp.route("/<entity_key>/<key>/delete", methods=["POST"])
@admin_required
def delete(entity_key, key):
    entity = get_entity(entity_key)
    row = get_row(entity, key)
    blocked = in_use_message(entity, row)
    if blocked:
        flash(blocked, "danger")
    else:
        db.session.delete(row)
        log_action("DELETE", f"{entity.singular} {key}")
        db.session.commit()
        flash(f"{entity.singular} deleted.", "success")
    return redirect(url_for("data.list_view", entity_key=entity.key))


# ---------------------------------------------------------------- CSV upload + template
@bp.route("/<entity_key>/upload", methods=["POST"])
@admin_required
def upload(entity_key):
    entity = get_entity(entity_key)
    file = request.files.get("file")
    back = redirect(url_for("data.list_view", entity_key=entity.key))
    if not file or not file.filename:
        flash("Choose a CSV file first.", "danger")
        return back
    if not file.filename.lower().endswith(".csv"):
        flash("Only .csv files can be uploaded. In Excel use Save As > CSV.", "danger")
        return back
    saved, errors = import_csv(entity, file.read())
    if errors:
        db.session.rollback()
        flash("Upload rejected - nothing was saved. Fix these problems and upload again:\n" + "\n".join(errors),
              "danger")
    else:
        log_action("IMPORT_CSV", f"{saved} {entity.title.lower()} from {file.filename}")
        db.session.commit()
        flash(f"Imported {saved} {entity.title.lower()} from {file.filename}.", "success")
    return back


@bp.route("/<entity_key>/template.csv")
@admin_required
def template(entity_key):
    entity = get_entity(entity_key)
    return Response(template_csv(entity), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={entity.key}_template.csv"})


# ---------------------------------------------------------------- faculty availability
@bp.route("/faculty/<faculty_id>/unavailability", methods=["GET", "POST"])
@admin_required
def unavailability(faculty_id):
    fac = db.get_or_404(Faculty, faculty_id)
    sessions = ExamSession.query.order_by(ExamSession.code).all()
    if request.method == "POST":
        try:
            day = date.fromisoformat(request.form.get("date", ""))
        except ValueError:
            flash("Choose a valid date.", "danger")
            return redirect(url_for("data.unavailability", faculty_id=faculty_id))
        code = request.form.get("session_code", "") or None
        if code and code not in {s.code for s in sessions}:
            flash("Unknown session.", "danger")
        elif FacultyUnavailability.query.filter_by(faculty_id=faculty_id, date=day, session_code=code).first():
            flash("That unavailability is already recorded.", "warning")
        else:
            db.session.add(FacultyUnavailability(faculty_id=faculty_id, date=day, session_code=code))
            log_action("ADD_UNAVAILABILITY", f"{faculty_id} on {day} {code or 'all day'}")
            db.session.commit()
            flash("Unavailability added.", "success")
        return redirect(url_for("data.unavailability", faculty_id=faculty_id))
    slots = sorted(fac.unavailability, key=lambda u: (u.date, u.session_code or ""))
    return render_template("data_unavailability.html", fac=fac, slots=slots, sessions=sessions)


@bp.route("/faculty/<faculty_id>/unavailability/<int:slot_id>/delete", methods=["POST"])
@admin_required
def unavailability_delete(faculty_id, slot_id):
    slot = db.get_or_404(FacultyUnavailability, slot_id)
    if slot.faculty_id != faculty_id:
        abort(404)
    db.session.delete(slot)
    log_action("REMOVE_UNAVAILABILITY", f"{faculty_id} on {slot.date}")
    db.session.commit()
    flash("Unavailability removed.", "success")
    return redirect(url_for("data.unavailability", faculty_id=faculty_id))
