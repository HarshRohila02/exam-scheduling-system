"""Adds columns that were introduced after a database was first created.

db.create_all() makes missing TABLES but never adds a missing COLUMN to a table that already exists, so
someone who created their database before a column existed would get errors. Each entry below says
"this column must exist; if it does not, add it". Existing rows are kept (the new column is empty for them).
"""
from sqlalchemy import inspect, text

from app.extensions import db

# (table, column, SQL type) - plain ALTER TABLE works the same on SQLite and MySQL
NEW_COLUMNS = [
    ("course", "exam_instructions", "TEXT"),
]


def add_missing_columns():
    inspector = inspect(db.engine)
    for table, column, sql_type in NEW_COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
    db.session.commit()
