"""Placeholder - replaced when the timetable generation step is built."""
from flask import Blueprint

from app.auth import admin_required

bp = Blueprint("timetable", __name__, url_prefix="/timetable")


@bp.route("/")
@admin_required
def index():
    return "Timetable generation is coming in a later build step."
