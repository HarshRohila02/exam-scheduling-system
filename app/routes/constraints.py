"""SRS 3.2.2 - Scheduling constraints with version history."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import admin_required, current_username, log_action
from app.extensions import db
from app.models import ConstraintVersion
from app.services.constraint_versions import (activate_version, create_version, get_active_version,
                                              read_constraint_form)

bp = Blueprint("constraints", __name__, url_prefix="/constraints")


@bp.route("/")
@admin_required
def index():
    active = get_active_version()
    versions = ConstraintVersion.query.order_by(ConstraintVersion.version_no.desc()).all()
    return render_template("constraints.html", active=active, versions=versions, form_values=None)


@bp.route("/save", methods=["POST"])
@admin_required
def save():
    values, limits, errors = read_constraint_form(request.form)
    if errors:
        for message in errors:
            flash(message, "danger")
        # show the form again with what the admin typed
        active = get_active_version()
        versions = ConstraintVersion.query.order_by(ConstraintVersion.version_no.desc()).all()
        typed = dict(values)
        typed["limits"] = [{"designation": d, "min_duties": lo, "max_duties": hi} for d, lo, hi in limits]
        typed["note"] = request.form.get("note", "")
        return render_template("constraints.html", active=active, versions=versions, form_values=typed)
    version = create_version(values, limits, current_username(), request.form.get("note", "").strip()[:200])
    log_action("SAVE_CONSTRAINTS", f"Created constraint version {version.version_no}")
    db.session.commit()
    flash(f"Saved as version {version.version_no} and made active.", "success")
    return redirect(url_for("constraints.index"))


@bp.route("/<int:version_id>/activate", methods=["POST"])
@admin_required
def activate(version_id):
    version = db.get_or_404(ConstraintVersion, version_id)
    activate_version(version)
    log_action("ACTIVATE_CONSTRAINTS", f"Version {version.version_no} made active")
    db.session.commit()
    flash(f"Version {version.version_no} is now the active constraint set.", "success")
    return redirect(url_for("constraints.index"))
