"""University Calendar - SRS 3.2.2 (MoSCoW R3).

The examination staff set:
  - the exam period (first and last day, and whether Sundays are allowed)
  - holidays inside that period
  - the sessions of a day (for example FN 09:30-12:30 and AN 14:00-17:00)

From these the timetable generator gets its valid (date, session) slots - the "domain" of the CSP model
(SRS 3.2.4). The live calendar is kept in the ExamCalendar, Holiday and ExamSession tables, which the
generator reads.

SRS 3.2.2 also asks for "the version history of all modifications made". After every change we save a
copy of the whole calendar as a new CalendarVersion row, so the staff can see what it looked like before,
who changed it and when.
"""
import re
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import ExamCalendar, ExamSession, Holiday

LONGEST_PERIOD_DAYS = 90
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")      # 24-hour HH:MM
CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,10}$")
DEFAULT_SESSIONS = [("FN", "Forenoon", "09:30", "12:30"), ("AN", "Afternoon", "14:00", "17:00")]


# ---------------------------------------------------------------- the history table
class CalendarVersion(db.Model):
    """A copy of the whole calendar, saved after each change. Only for the history;
    the generator never reads this table. It is defined here so the shared models file stays as it is."""
    id = db.Column(db.Integer, primary_key=True)
    version_no = db.Column(db.Integer, unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    created_by = db.Column(db.String(50))
    change = db.Column(db.String(200), nullable=False)          # what changed, in plain words
    start_date = db.Column(db.Date)                             # empty while no period is set
    end_date = db.Column(db.Date)
    skip_sundays = db.Column(db.Boolean)
    holidays = db.Column(db.Text, nullable=False, default="")   # "2026-11-04 (Diwali), ..."
    sessions = db.Column(db.Text, nullable=False, default="")   # "FN 09:30-12:30, AN 14:00-17:00"


def save_calendar_version(change, username):
    """Save the calendar as it is now as version N+1. Call it after making a change and before commit()."""
    db.session.flush()                                  # let the queries below see the change
    calendar = ExamCalendar.query.first()
    holidays = Holiday.query.order_by(Holiday.date).all()
    sessions = ExamSession.query.order_by(ExamSession.start_time).all()
    last = db.session.query(db.func.max(CalendarVersion.version_no)).scalar() or 0

    version = CalendarVersion(
        version_no=last + 1,
        created_by=username,
        change=change[:200],
        start_date=calendar.start_date if calendar else None,
        end_date=calendar.end_date if calendar else None,
        skip_sundays=calendar.skip_sundays if calendar else None,
        holidays=", ".join(str(h.date) + (f" ({h.reason})" if h.reason else "") for h in holidays),
        sessions=", ".join(f"{s.code} {s.start_time}-{s.end_time}" for s in sessions),
    )
    db.session.add(version)
    return version


def calendar_history():
    """Every saved version, newest first."""
    return CalendarVersion.query.order_by(CalendarVersion.version_no.desc()).all()


def ensure_default_sessions():
    """On the very first start there are no sessions yet: add FN and AN."""
    if ExamSession.query.count() == 0:
        for code, name, start, end in DEFAULT_SESSIONS:
            db.session.add(ExamSession(code=code, name=name, start_time=start, end_time=end))


# ---------------------------------------------------------------- checks
def read_date(text):
    """'2026-12-07' -> date(2026, 12, 7); anything else -> None."""
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def check_period(start, end):
    """Error message for a wrong exam period, or None if it is fine."""
    if start is None or end is None:
        return "Enter both a start date and an end date."
    if end < start:
        return "The end date cannot be before the start date."
    if (end - start).days + 1 > LONGEST_PERIOD_DAYS:
        return f"The exam period can be at most {LONGEST_PERIOD_DAYS} days long."
    return None


def check_holiday(day):
    """Error message for a wrong holiday, or None. A holiday only matters inside the exam period."""
    if day is None:
        return "Choose a valid holiday date."
    if Holiday.query.filter_by(date=day).first():
        return f"{day} is already a holiday."
    calendar = ExamCalendar.query.first()
    if calendar and not (calendar.start_date <= day <= calendar.end_date):
        return (f"{day} is outside the exam period ({calendar.start_date} to {calendar.end_date}), "
                f"so it would not change anything.")
    return None


def to_minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def check_session(code, name, start, end):
    """Error message for one wrong session, or None."""
    if not CODE_PATTERN.match(code):
        return "The session code must be 1-10 capital letters or digits, for example FN."
    if not name:
        return "The session name is required."
    if not TIME_PATTERN.match(start) or not TIME_PATTERN.match(end):
        return "Times must be written like 09:30 (24-hour clock)."
    if to_minutes(end) <= to_minutes(start):
        return f"Session {code} must end after it starts."
    if to_minutes(end) - to_minutes(start) < 30:
        return f"Session {code} must be at least 30 minutes long."
    return None


def check_overlap(sessions):
    """sessions = [(code, start, end), ...]. Two sessions must not overlap, otherwise a student could be
    given two exams at the same time. Returns an error message or None. Touching (12:30 / 12:30) is fine."""
    ordered = sorted(sessions, key=lambda s: to_minutes(s[1]))
    for first, second in zip(ordered, ordered[1:]):
        if to_minutes(second[1]) < to_minutes(first[2]):
            return (f"Sessions {first[0]} ({first[1]}-{first[2]}) and {second[0]} ({second[1]}-{second[2]}) "
                    f"overlap. A student could then have two exams at the same time.")
    return None


# ---------------------------------------------------------------- what the generator will get
def day_by_day(calendar, holidays, sessions):
    """Preview for the page: every day of the period, whether exams can be held, and why not."""
    holiday_reason = {h.date: (h.reason or "Holiday") for h in holidays}
    codes = ", ".join(s.code for s in sessions)
    days, day = [], calendar.start_date
    while day <= calendar.end_date:
        if day in holiday_reason:
            days.append({"date": day, "exam_day": False, "info": "Holiday: " + holiday_reason[day]})
        elif calendar.skip_sundays and day.weekday() == 6:
            days.append({"date": day, "exam_day": False, "info": "Sunday"})
        else:
            days.append({"date": day, "exam_day": True, "info": codes})
        day += timedelta(days=1)
    return days
