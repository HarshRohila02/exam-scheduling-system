"""Upload many records at once from a CSV or Excel file.

SRS 3.2.1: data "can be inputted or uploaded in forms/files".
Use case: "Import Records from CSV / Excel File" (extends Manage Student Records).

How it works:
  1. read the file into a header + a list of rows (CSV and Excel end up looking the same)
  2. check every row with check_record() - the same rules as the Add form
  3. if ANY row is wrong, save nothing and return one message per problem ("Row 4: Capacity is required")
     so the admin can fix the file and upload it again; otherwise add all rows to the session.
The caller (the upload page, or seed.py) commits.
"""
import csv
import io
from datetime import date, datetime

from app.extensions import db
from app.services.validation import check_record, collect_known, remember

ALLOWED_ENDINGS = (".csv", ".xlsx")
MAX_MESSAGES = 50                       # do not flood the page if a whole file is wrong


def template_csv(record_type):
    """The text of a CSV file with the right column names and one example row."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(record_type.field_names())
    writer.writerow([record_type.example.get(name, "") for name in record_type.field_names()])
    return out.getvalue()


# ---------------------------------------------------------------- reading the files
def read_csv(file_bytes):
    """Return (header, rows) from CSV bytes, or raise ValueError with a message for the admin."""
    try:
        text = file_bytes.decode("utf-8-sig")      # utf-8-sig also removes the mark Excel adds
    except UnicodeDecodeError:
        raise ValueError("The file is not UTF-8 text. In Excel choose Save As > CSV UTF-8")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty")
    return reader.fieldnames, list(reader)


def cell_to_text(value):
    """Write an Excel cell the way it would look in a CSV file."""
    if value is None:
        return ""
    if value is True or value is False:
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))                     # Excel keeps 60 as 60.0
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def read_excel(file_bytes):
    """Return (header, rows) from the first sheet of an .xlsx file, or raise ValueError."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError("Excel upload needs the openpyxl package (pip install openpyxl). Upload a CSV file instead")
    try:
        book = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception:
        raise ValueError("The file could not be opened as an Excel (.xlsx) workbook")

    lines = [[cell_to_text(v) for v in row] for row in book.worksheets[0].iter_rows(values_only=True)]
    book.close()
    if not lines or not any(lines[0]):
        raise ValueError("The file is empty")
    header = lines[0]
    return header, [dict(zip(header, line)) for line in lines[1:]]


# ---------------------------------------------------------------- checking and saving
def import_rows(record_type, header, rows):
    """Check all rows. Returns (number_saved, messages). Nothing is added if there is any message."""
    header = [(name or "").strip() for name in header]
    missing = [f["name"] for f in record_type.fields if f["required"] and f["name"] not in header]
    if missing:
        return 0, [f"Missing column(s): {', '.join(missing)}. "
                   f"The columns must be: {', '.join(record_type.field_names())}"]

    known = collect_known()
    good_rows, messages = [], []
    for row_number, row in enumerate(rows, start=2):        # row 1 is the header
        row = {(name or "").strip(): value for name, value in row.items()}
        if all(not (value or "").strip() for value in row.values()):
            continue                                         # ignore empty lines
        values, errors = check_record(record_type, row, known)
        if errors:
            messages += [f"Row {row_number}: {error}" for error in errors]
        else:
            remember(record_type, values, known)            # so a duplicate later in the file is caught
            good_rows.append(values)

    if messages:
        if len(messages) > MAX_MESSAGES:
            messages = messages[:MAX_MESSAGES] + [f"... and {len(messages) - MAX_MESSAGES} more problems"]
        return 0, messages
    if not good_rows:
        return 0, ["The file has column names but no data rows"]

    for values in good_rows:
        db.session.add(record_type.model(**values))
    return len(good_rows), []


def import_csv(record_type, file_bytes):
    """Import a CSV file (also used by seed.py). Returns (number_saved, messages)."""
    try:
        header, rows = read_csv(file_bytes)
    except ValueError as problem:
        return 0, [str(problem)]
    return import_rows(record_type, header, rows)


def import_file(record_type, filename, file_bytes):
    """Import an uploaded .csv or .xlsx file. Returns (number_saved, messages)."""
    if filename.lower().endswith(".xlsx"):
        try:
            header, rows = read_excel(file_bytes)
        except ValueError as problem:
            return 0, [str(problem)]
        return import_rows(record_type, header, rows)
    return import_csv(record_type, file_bytes)
