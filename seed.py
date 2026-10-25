"""Load the sample data (5 courses, 165 students, 6 faculty, 3 rooms) into the database.

    python seed.py           add the sample data to any table that is still empty
    python seed.py --reset   wipe every table first, then load the sample data
"""
import sys
from datetime import date
from pathlib import Path

from app import create_app
from app.auth import ensure_admin_user
from app.extensions import db
from app.models import ExamCalendar
from app.services.constraint_versions import ensure_defaults
from app.services.csv_import import import_csv
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


if __name__ == "__main__":
    seed(create_app(), reset="--reset" in sys.argv)
    print("Done.")
