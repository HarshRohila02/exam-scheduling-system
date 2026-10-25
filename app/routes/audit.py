"""Audit log page (SRS 3.6): who did what, newest first."""
from flask import Blueprint, render_template, request

from app.auth import admin_required
from app.models import AuditLog

bp = Blueprint("audit", __name__, url_prefix="/audit")


@bp.route("/")
@admin_required
def index():
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.id.desc()).paginate(page=page, per_page=50, error_out=False)
    return render_template("audit.html", logs=logs)
