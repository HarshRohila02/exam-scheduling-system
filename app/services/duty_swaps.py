from app.auth import log_action
from app.extensions import db
from app.models import DutySwap, InvigilationDuty, Notification, TimetableEntry

CELL = "EXAM_CELL"


def record_event(swap, action):
    entry = swap.duty.entry
    message = (f"{swap.sender} → {swap.recipient}: {entry.course_code}, {entry.exam_date} "
               f"{entry.session_code}, room {swap.duty.room_id}. Request #{swap.id}: {action}.")
    for role, recipient in [("ADMIN", CELL), ("FACULTY", swap.sender), ("FACULTY", swap.recipient)]:
        db.session.add(Notification(recipient_type=role, recipient_id=recipient, message=message))
    log_action("DUTY_SWAP", message)


def close_requests(timetable):
    pending = (DutySwap.query.join(DutySwap.duty).join(InvigilationDuty.entry)
               .filter(TimetableEntry.timetable_id == timetable.id, DutySwap.status == "Pending")
               .with_for_update().all())
    for swap in pending:
        swap.status, swap.pending_duty = "Expired", None
        record_event(swap, "expired when timetable was archived")
