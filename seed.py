"""Load the sample data (5 courses, 165 students, 6 faculty, 3 rooms) into the database.

    python seed.py           add the sample data to any table that is still empty
    python seed.py --reset   wipe every table first, then load the sample data

Both also make a Student Portal login for every student who has none yet (username = enrollment number,
password = STUDENT_DEFAULT_PASSWORD from .env). Run `python seed.py` again after adding students.
"""
import sys
from datetime import date
from pathlib import Path

from app import create_app
from app.auth import ensure_admin_user
from app.extensions import db
from app.models import ExamCalendar, Faculty, User
from werkzeug.security import generate_password_hash
from app.services.constraint_versions import ensure_defaults
from app.services.csv_import import import_csv
from app.services.student_accounts import create_student_accounts
from app.services.validation import ENTITIES

SAMPLE_DIR = Path(__file__).parent / "sample_data"
LOAD_ORDER = ["students", "courses", "registrations", "faculty", "rooms"]   # registrations need the first two


def seed(app, reset=False):
    with app.app_context():
        if reset:
            db.drop_all()
            db.create_all()
            ensure_defaults()
        ensure_admin_user(app)

        for key in LOAD_ORDER:
            entity = ENTITIES[key]
            if entity.model.query.count() > 0:
                print(f"{entity.title}: already has data, skipped")
                continue
            saved, errors = import_csv(entity, (SAMPLE_DIR / f"{key}.csv").read_bytes())
            if errors:
                db.session.rollback()
                raise SystemExit(f"{entity.title}: sample file is invalid:\n  " + "\n  ".join(errors))
            db.session.commit()
            print(f"{entity.title}: loaded {saved}")

        if ExamCalendar.query.count() == 0:
            db.session.add(ExamCalendar(start_date=date(2026, 11, 2), end_date=date(2026, 11, 7), skip_sundays=True))
            db.session.commit()
            print("Calendar: exam period 02-Nov-2026 to 07-Nov-2026")

        for person in Faculty.query:
            if not User.query.filter_by(username=person.faculty_id).first():
                db.session.add(User(username=person.faculty_id, role="FACULTY", must_change_password=True,
                                    password_hash=generate_password_hash(app.config["FACULTY_DEFAULT_PASSWORD"],
                                                                         method="pbkdf2:sha256:10000")))
        db.session.commit()
        made = create_student_accounts(app.config["STUDENT_DEFAULT_PASSWORD"])
        print(f"Student logins: {made} new (username = enrollment number, temporary password from .env)")


if __name__ == "__main__":
    seed(create_app(), reset="--reset" in sys.argv)
    print("Done.")
