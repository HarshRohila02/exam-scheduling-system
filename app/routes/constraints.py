"""Admin Portal - Scheduling Constraints page (SRS 3.2.2, MoSCoW R4).

Use cases: Configure Scheduling Constraints, Maintain Constraint Version History.
DFD Level 1 process 3.0 (store D7 Constraint Configurations, versioned).

    /constraints/                    the form (filled with the active version) and the version history
    /constraints/save                save the form as a new version and make it active
    /constraints/<id>/activate       make an older version active again
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import admin_required, current_username, log_action
from app.extensions import db
from app.models import ConstraintVersion
from app.services.constraint_versions import (NUMBER_SETTINGS, get_active_version, make_active,
                                              read_constraint_form, save_new_version, version_history)

bp = Blueprint("constraints", __name__, url_prefix="/constraints")


def show_page(typed=None):
    """typed = what the admin entered (shown again after a mistake); otherwise the active version is shown."""
    active = get_active_version()
    if typed is None:
        typed = {name: getattr(active, name) for name, *_ in NUMBER_SETTINGS}
        typed["no_same_day_exams"] = active.no_same_day_exams
        typed["limits"] = [(l.designation, l.min_duties, l.max_duties) for l in active.limits]
        typed["note"] = ""
    return render_template("constraints.html", active=active, form=typed, history=version_history())


@bp.route("/")
@admin_required
def index():
    return show_page()


@bp.route("/save", methods=["POST"])
@admin_required
def save():
    values, limits, errors = read_constraint_form(request.form)
    if errors:
        for error in errors:
            flash(error, "danger")
        typed = {name: request.form.get(name, "") for name, *_ in NUMBER_SETTINGS}
        typed["no_same_day_exams"] = request.form.get("no_same_day_exams") == "yes"
        rows = zip(request.form.getlist("designation"), request.form.getlist("min_duties"),
                   request.form.getlist("max_duties"))
        typed["limits"] = [row for row in rows if any(part.strip() for part in row)]   # leave out empty rows
        typed["note"] = request.form.get("note", "")
        return show_page(typed)                      # nothing saved, the form keeps what was typed

    note = request.form.get("note", "").strip()[:200]
    version = save_new_version(values, limits, current_username(), note)
    log_action("SAVE_CONSTRAINTS", f"Constraint version {version.version_no} saved")
    db.session.commit()
    flash(f"Saved as version {version.version_no}. It is now the active configuration.", "success")
    return redirect(url_for("constraints.index"))


@bp.route("/<int:version_id>/activate", methods=["POST"])
@admin_required
def activate(version_id):
    version = db.get_or_404(ConstraintVersion, version_id)
    make_active(version)
    log_action("ACTIVATE_CONSTRAINTS", f"Constraint version {version.version_no} made active")
    db.session.commit()
    flash(f"Version {version.version_no} is the active configuration again.", "success")
    return redirect(url_for("constraints.index"))
