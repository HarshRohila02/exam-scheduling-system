"""Bulk upload of master data from a CSV file (SRS 3.2.1: "can be inputted or uploaded in forms/files").

The import is all-or-nothing: if any row is wrong, nothing is saved and the admin gets one message
per problem ("Row 5: Student ID S001 already exists") so the file can be fixed and re-uploaded.
"""
import csv
import io
from typing import List, Tuple

from app.extensions import db
from app.services.validation import Entity, load_context, remember, validate_record

MAX_ERRORS_SHOWN = 50


def template_csv(entity: Entity) -> str:
    """A header row plus one example row, for the admin to download and fill in."""
    out = io.StringIO()
    names = [f.name for f in entity.fields]
    writer = csv.DictWriter(out, fieldnames=names)
    writer.writeheader()
    writer.writerow({n: entity.example.get(n, "") for n in names})
    return out.getvalue()


def import_csv(entity: Entity, file_bytes: bytes) -> Tuple[int, List[str]]:
    """Validate every row of the file and save them all. Returns (rows_saved, error_messages)."""
    try:
        text = file_bytes.decode("utf-8-sig")        # utf-8-sig also handles Excel's BOM
    except UnicodeDecodeError:
        return 0, ["The file is not a UTF-8 text file. In Excel use Save As > CSV UTF-8"]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return 0, ["The file is empty"]
    header = [h.strip() for h in reader.fieldnames]
    missing = [f.name for f in entity.fields if f.required and f.name not in header]
    if missing:
        return 0, [f"Missing column(s): {', '.join(missing)}. Expected columns: "
                   f"{', '.join(f.name for f in entity.fields)}"]

    ctx = load_context()
    clean_rows, errors = [], []
    for line_no, row in enumerate(reader, start=2):          # line 1 is the header
        row = {(k or "").strip(): v for k, v in row.items()}
        if not any((v or "").strip() for v in row.values()):
            continue                                          # skip blank lines
        clean, row_errors = validate_record(entity, row, ctx)
        if row_errors:
            errors.extend(f"Row {line_no}: {e}" for e in row_errors)
        else:
            remember(entity, clean, ctx)
            clean_rows.append(clean)

    if not clean_rows and not errors:
        return 0, ["The file has a header but no data rows"]
    if errors:
        if len(errors) > MAX_ERRORS_SHOWN:
            errors = errors[:MAX_ERRORS_SHOWN] + [f"... and {len(errors) - MAX_ERRORS_SHOWN} more problems"]
        return 0, errors

    db.session.add_all(entity.model(**clean) for clean in clean_rows)
    return len(clean_rows), []
