"""Admin Portal - Student Logins: give every student a login (enrollment number + temporary password).

The admin creates logins for all students who do not have one yet, or resets one student's password.
Each time, the new temporary passwords are shown ONCE on a printable list (with a CSV download) that the
examination cell hands out; only their hashes are saved. Students choose their own password at first login.
"""
import csv
import io

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for

from app.auth import admin_required, log_action
from app.extensions import db
from app.models import Student, User
from app.services.student_accounts import create_logins_with_unique_passwords, reset_password

bp = Blueprint("student_logins", __name__, url_prefix="/student-logins")


def login_status(user):
    if user is None:
        return "none"
    return "temporary" if user.must_change_password else "active"


def password_sheet(made, title):
    """The one-time list of new passwords. no-store: the browser must not keep a copy of this page."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Enrollment number", "Name", "Program", "Section", "Temporary password"])
    for student, password in made:
        writer.writerow([student.student_id, student.name, student.program, student.section, password])
    response = make_response(render_template("student_logins_result.html", made=made, title=title,
                                             csv_text=buffer.getvalue()))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/")
@admin_required
def index():
    search = request.args.get("q", "").strip()
    users = {u.username: u for u in User.query.filter_by(role="STUDENT")}
    query = Student.query
    if search:
        query = query.filter(db.or_(Student.student_id.ilike(f"%{search}%"), Student.name.ilike(f"%{search}%")))
    rows = [(s, login_status(users.get(s.student_id))) for s in query.order_by(Student.student_id)]

    everyone = [login_status(users.get(sid)) for (sid,) in db.session.query(Student.student_id)]
    counts = {"students": len(everyone), "none": everyone.count("none"),
              "temporary": everyone.count("temporary"), "active": everyone.count("active")}
    return render_template("student_logins.html", rows=rows, counts=counts, search=search)


@bp.route("/create", methods=["POST"])
@admin_required
def create():
    made = create_logins_with_unique_passwords()
    if not made:
        flash("Every student already has a login.", "info")
        return redirect(url_for("student_logins.index"))
    log_action("CREATE_LOGINS", f"{len(made)} student logins created")
    db.session.commit()
    return password_sheet(made, f"{len(made)} new student logins")


@bp.route("/<student_id>/reset", methods=["POST"])
@admin_required
def reset(student_id):
    password = reset_password(student_id)
    if password is None:
        abort(404, "No such student.")
    log_action("RESET_PASSWORD", f"Student {student_id}")
    db.session.commit()
    return password_sheet([(db.session.get(Student, student_id), password)], f"New password for {student_id}")
