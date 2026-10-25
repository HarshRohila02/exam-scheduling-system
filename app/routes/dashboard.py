"""Dashboard: counts, readiness checklist and the latest timetable status."""
from flask import Blueprint, render_template

from app.auth import admin_required
from app.models import Course, Faculty, Room, Student, Timetable
from app.services.constraint_versions import get_active_version
from app.services.timetable_service import readiness_checklist

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@admin_required
def index():
    counts = {"Students": Student.query.count(), "Courses": Course.query.count(),
              "Faculty": Faculty.query.count(), "Rooms": Room.query.count()}
    latest = Timetable.query.order_by(Timetable.id.desc()).first()
    published = Timetable.query.filter_by(status="PUBLISHED").first()
    return render_template("dashboard.html", counts=counts, checklist=readiness_checklist(), latest=latest,
                           published=published, version=get_active_version())
