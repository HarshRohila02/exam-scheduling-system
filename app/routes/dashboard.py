"""Dashboard: counts, readiness checklist and the latest timetable status."""
from flask import Blueprint, render_template

from app.auth import admin_required
from app.models import Course, Faculty, Room, Student

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@admin_required
def index():
    counts = {"Students": Student.query.count(), "Courses": Course.query.count(),
              "Faculty": Faculty.query.count(), "Rooms": Room.query.count()}
    return render_template("dashboard.html", counts=counts)
