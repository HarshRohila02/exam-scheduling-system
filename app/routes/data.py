"""Admin Portal - Data Management pages (SRS 3.2.1, MoSCoW R2).

Use cases: Manage Student / Faculty / Course / Room Records, Import Records from CSV / Excel File,
Validate Record Data. DFD Level 1 process 2.0 "Manage Master Data" (stores D2 - D5).

One set of pages serves every record type in app/services/validation.py:
    /data/<key>/                 list + search            (key = students, courses, registrations, faculty, rooms)
    /data/<key>/add              add a record
    /data/<key>/<id>/edit        edit a record (the ID itself cannot change)
    /data/<key>/<id>/delete      delete a record
    /data/<key>/upload           upload a CSV or Excel file
    /data/<key>/template.csv     download an empty file with the right columns
    /data/faculty/<id>/unavailability    dates on which a faculty member cannot invigilate
Only logged-in examination staff (ADMIN) can open these pages, and every change goes into the audit log.
"""
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from app.auth import admin_required, log_action
from app.extensions import db
from app.models import (ExamRoom, ExamSession, Faculty, FacultyUnavailability, InvigilationDuty, SeatAssignment,
                        Timetable, TimetableEntry)
from app.services.calendar_service import read_date
from app.services.csv_import import ALLOWED_ENDINGS, import_file, template_csv
from app.services.validation import ENTITIES, check_record, collect_known

bp = Blueprint("data", __name__, url_prefix="/data")

TIMETABLES_IN_USE = ("DRAFT", "PUBLISHED")


# ---------------------------------------------------------------- helpers
def find_type(entity_key):
    if entity_key not in ENTITIES:
        abort(404)
    return ENTITIES[entity_key]


def find_record(record_type, record_id):
    """Load one record from the ID in the web address (registrations use a number)."""
    if record_type.id_field == "id":
        if not record_id.isdigit():
            abort(404)
        record_id = int(record_id)
    record = db.session.get(record_type.model, record_id)
    if record is None:
        abort(404)
    return record


def used_by_timetable(record_type, record):
    """True if a draft or published timetable depends on this record (then it must not be deleted)."""
    def in_live_timetable(query):
        return (query.join(Timetable, TimetableEntry.timetable_id == Timetable.id)
                .filter(Timetable.status.in_(TIMETABLES_IN_USE)).first() is not None)

    if record_type.key in ("courses", "registrations"):
        return in_live_timetable(TimetableEntry.query.filter(TimetableEntry.course_code == record.course_code))
    if record_type.key == "students":
        table, column = SeatAssignment, SeatAssignment.student_id == record.student_id
    elif record_type.key == "faculty":
        table, column = InvigilationDuty, InvigilationDuty.faculty_id == record.faculty_id
    else:
        table, column = ExamRoom, ExamRoom.room_id == record.room_id
    return in_live_timetable(table.query.join(TimetableEntry, table.entry_id == TimetableEntry.id).filter(column))


def show_form(record_type, values, editing, record_id=None):
    return render_template("data_form.html", entity=record_type, values=values, editing=editing,
                           record_id=record_id, designations=collect_known()["designations"])


# ---------------------------------------------------------------- list and search
@bp.route("/<entity_key>/")
@admin_required
def list_view(entity_key):
    record_type = find_type(entity_key)
    search = request.args.get("q", "").strip()
    query = record_type.model.query
    if search:
        # look for the text in every text-like column
        columns = [getattr(record_type.model, f["name"]) for f in record_type.fields
                   if f["type"] in ("id", "text", "email", "designation")]
        query = query.filter(db.or_(*[column.ilike(f"%{search}%") for column in columns]))
    records = query.order_by(getattr(record_type.model, record_type.id_field)).all()
    return render_template("data_list.html", entity=record_type, records=records, search=search,
                           total=record_type.model.query.count())


# ---------------------------------------------------------------- add
@bp.route("/<entity_key>/add", methods=["GET", "POST"])
@admin_required
def add(entity_key):
    record_type = find_type(entity_key)
    if request.method == "GET":
        return show_form(record_type, {}, editing=False)

    values, errors = check_record(record_type, request.form, collect_known(), is_new=True)
    if errors:
        for error in errors:
            flash(error, "danger")
        return show_form(record_type, request.form, editing=False)       # keep what was typed

    db.session.add(record_type.model(**values))
    first_value = values[record_type.fields[0]["name"]]
    log_action("CREATE", f"{record_type.singular} {first_value}")
    db.session.commit()
    flash(f"{record_type.singular} {first_value} added.", "success")
    return redirect(url_for("data.list_view", entity_key=entity_key))


# ---------------------------------------------------------------- edit
@bp.route("/<entity_key>/<record_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(entity_key, record_id):
    record_type = find_type(entity_key)
    record = find_record(record_type, record_id)
    if request.method == "GET":
        current = {name: getattr(record, name) for name in record_type.field_names()}
        return show_form(record_type, current, editing=True, record_id=record_id)

    form = request.form.to_dict()
    # The identifying fields cannot be changed: always use the stored values
    locked = ["student_id", "course_code"] if entity_key == "registrations" else [record_type.id_field]
    for name in locked:
        form[name] = getattr(record, name)

    values, errors = check_record(record_type, form, collect_known(), is_new=False)
    if errors:
        for error in errors:
            flash(error, "danger")
        return show_form(record_type, form, editing=True, record_id=record_id)

    for name, value in values.items():
        setattr(record, name, value)
    log_action("UPDATE", f"{record_type.singular} {record_id}")
    db.session.commit()
    flash(f"{record_type.singular} {record_id} updated.", "success")
    return redirect(url_for("data.list_view", entity_key=entity_key))


# ---------------------------------------------------------------- delete
@bp.route("/<entity_key>/<record_id>/delete", methods=["POST"])
@admin_required
def delete(entity_key, record_id):
    record_type = find_type(entity_key)
    record = find_record(record_type, record_id)
    if used_by_timetable(record_type, record):
        flash(f"This {record_type.singular.lower()} cannot be deleted: a draft or published timetable uses it. "
              f"Archive that timetable or generate a new one first.", "danger")
    else:
        db.session.delete(record)            # registrations / unavailable dates of the record go with it
        log_action("DELETE", f"{record_type.singular} {record_id}")
        db.session.commit()
        flash(f"{record_type.singular} {record_id} deleted.", "success")
    return redirect(url_for("data.list_view", entity_key=entity_key))


# ---------------------------------------------------------------- upload and template
@bp.route("/<entity_key>/upload", methods=["POST"])
@admin_required
def upload(entity_key):
    record_type = find_type(entity_key)
    back_to_list = redirect(url_for("data.list_view", entity_key=entity_key))
    file = request.files.get("file")
    if file is None or file.filename == "":
        flash("Choose a CSV or Excel file first.", "danger")
        return back_to_list
    if not file.filename.lower().endswith(ALLOWED_ENDINGS):
        flash("Only .csv and .xlsx files can be uploaded (for an old .xls file use Save As .xlsx in Excel).", "danger")
        return back_to_list

    saved, problems = import_file(record_type, file.filename, file.read())
    if problems:
        db.session.rollback()
        flash("Nothing was saved. Correct these problems and upload the file again:", "danger")
        for problem in problems:
            flash(problem, "danger")
    else:
        log_action("IMPORT", f"{saved} {record_type.title.lower()} from {file.filename}")
        db.session.commit()
        flash(f"{saved} {record_type.title.lower()} imported from {file.filename}.", "success")
    return back_to_list


@bp.route("/<entity_key>/template.csv")
@admin_required
def download_template(entity_key):
    record_type = find_type(entity_key)
    return Response(template_csv(record_type), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={entity_key}_template.csv"})


# ---------------------------------------------------------------- faculty availability
@bp.route("/faculty/<faculty_id>/unavailability", methods=["GET", "POST"])
@admin_required
def unavailability(faculty_id):
    """Dates (whole day or one session) on which this faculty member must not get a duty (SRS: availability)."""
    member = db.get_or_404(Faculty, faculty_id)
    sessions = ExamSession.query.order_by(ExamSession.start_time).all()

    if request.method == "POST":
        day = read_date(request.form.get("date"))
        session_code = request.form.get("session_code") or None          # empty = whole day
        if day is None:
            flash("Choose a valid date.", "danger")
        elif session_code and session_code not in [s.code for s in sessions]:
            flash("Unknown session.", "danger")
        elif FacultyUnavailability.query.filter_by(faculty_id=faculty_id, date=day,
                                                   session_code=session_code).first():
            flash("This date is already in the list.", "warning")
        else:
            db.session.add(FacultyUnavailability(faculty_id=faculty_id, date=day, session_code=session_code))
            log_action("ADD_UNAVAILABILITY", f"{faculty_id} on {day} {session_code or 'whole day'}")
            db.session.commit()
            flash("Unavailable date added.", "success")
        return redirect(url_for("data.unavailability", faculty_id=faculty_id))

    dates = sorted(member.unavailability, key=lambda u: (u.date, u.session_code or ""))
    return render_template("data_unavailability.html", member=member, dates=dates, sessions=sessions)


@bp.route("/faculty/<faculty_id>/unavailability/<int:entry_id>/delete", methods=["POST"])
@admin_required
def unavailability_delete(faculty_id, entry_id):
    entry = db.get_or_404(FacultyUnavailability, entry_id)
    if entry.faculty_id != faculty_id:
        abort(404)
    db.session.delete(entry)
    log_action("REMOVE_UNAVAILABILITY", f"{faculty_id} on {entry.date}")
    db.session.commit()
    flash("Unavailable date removed.", "success")
    return redirect(url_for("data.unavailability", faculty_id=faculty_id))
