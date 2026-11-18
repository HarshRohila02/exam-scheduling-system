"""Student Portal test cases TC01 - TC32 (one test per row of the test-case document).

The SP_ prefix keeps these IDs apart from the TC numbers the other modules already use.
Each test records what it actually saw with record("SP_TCnn", ...), which ends up in tests/test_results.json.
"""
import re
import sqlite3
import time
from datetime import timedelta

from sqlalchemy import func

from app import create_app
from app.extensions import db
from app.models import Course, ExamCalendar, Registration, SeatAssignment, Student, Timetable, TimetableEntry
from app.services.published_view import get_student_exams
from app.services.student_accounts import create_student_accounts
from app.services.timetable_service import validate_saved_timetable
from tests.conftest import record
from tests.test_student_portal import login, publish, student

NOT_YET = b"Seating information not yet available"


# ---------------------------------------------------------------- helpers
def register_for_three(roll="S002"):
    """Give a sample student (who has 1-2 exams) a third registration, before the timetable is made."""
    have = {r.course_code for r in Registration.query.filter_by(student_id=roll)}
    for course in Course.query.order_by(Course.course_code):
        if len(have) == 3:
            break
        if course.course_code not in have:
            db.session.add(Registration(student_id=roll, course_code=course.course_code))
            have.add(course.course_code)
    db.session.commit()
    return roll


def students_with_two_exams():
    return [r[0] for r in db.session.query(Registration.student_id).group_by(Registration.student_id)
            .having(func.count() == 2).order_by(Registration.student_id)]


def publish_with_moved_exam(admin, roll, course):
    """The admin generates a new draft, moves one course's exam to a different day (editing the draft directly),
    and publishes it. Returns (timetable, old_date, new_date). Only a move that still passes the validator is
    used, because publishing re-checks the timetable."""
    admin.post("/timetable/generate")
    draft = Timetable.query.order_by(Timetable.id.desc()).first()
    entry = next(e for e in draft.entries if e.course_code == course)
    old = entry.exam_date
    others = {e.exam_date for e in draft.entries
              if e.course_code != course and SeatAssignment.query.filter_by(entry_id=e.id, student_id=roll).first()}
    taken = {(e.exam_date, e.session_code) for e in draft.entries}
    calendar = ExamCalendar.query.one()
    day = calendar.start_date
    while day <= calendar.end_date:
        if day != old and day not in others and (day, entry.session_code) not in taken and day.weekday() != 6:
            entry.exam_date = day
            db.session.commit()
            if not validate_saved_timetable(draft):
                admin.post(f"/timetable/{draft.id}/publish", data={"instructions": "Bring your ID card."})
                assert db.session.get(Timetable, draft.id).status == "PUBLISHED"
                return draft, old, day
            entry.exam_date = old
            db.session.commit()
        day += timedelta(days=1)
    raise AssertionError("no valid new date found for the exam")


def shown(day):
    """How the student pages write a date, e.g. '05 Nov 2026'."""
    return day.strftime("%d %b %Y")


def set_instructions(admin, course_code, text):
    """What the admin does: edit the course and type its exam instructions."""
    course = db.session.get(Course, course_code)
    response = admin.post(f"/data/courses/{course_code}/edit", data={
        "course_code": course_code, "name": course.name, "semester": course.semester,
        "exam_duration_minutes": course.exam_duration_minutes, "exam_instructions": text})
    assert response.status_code == 302, response.data
    db.session.refresh(course)


def page_of(browser, url):
    response = browser.get(url)
    assert response.status_code == 200, url
    return response.data.decode()


def cards(page):
    """The timetable page split into one text block per exam card (in page order)."""
    return re.findall(r'<a class="exam-tile.*?</a>', page, flags=re.S)


# ================================================================ login (TC01 - TC04)
def test_SP_TC01_login_with_valid_credentials(sample):
    browser, response = login(sample, "S001")
    assert response.status_code == 302 and response.headers["Location"].endswith("/student/")
    browser.post("/student/password", data={"current_password": sample.config["STUDENT_DEFAULT_PASSWORD"],
                                            "new_password": "my-own-pass-1", "confirm_password": "my-own-pass-1"})
    assert browser.get("/student/").status_code == 200
    record("SP_TC01", "login redirects to /student/ and the dashboard opens (200)")


def test_SP_TC02_login_with_wrong_password(sample):
    browser, response = login(sample, "S001", "not-the-password")
    assert b"Wrong username or password" in response.data
    assert browser.get("/student/").status_code == 302          # still not logged in
    record("SP_TC02", "'Wrong username or password.' shown, dashboard stays closed")


def test_SP_TC03_login_with_empty_credentials(sample):
    _, response = login(sample, "", "")
    assert b"Enter both username and password" in response.data
    record("SP_TC03", "'Enter both username and password.' shown")


def test_SP_TC04_dashboard_without_login(sample):
    response = sample.test_client().get("/student/")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    record("SP_TC04", f"redirected to {response.headers['Location']}")


# ================================================================ viewing exams (TC05 - TC08)
def test_SP_TC05_all_registered_exams_are_shown(client, sample):
    roll = register_for_three()
    publish(client)
    page = page_of(student(sample, roll), "/student/timetable")
    codes = {r.course_code for r in Registration.query.filter_by(student_id=roll)}
    assert len(codes) == 3 and all(code in page for code in codes)
    assert len(cards(page)) == 3
    record("SP_TC05", f"{roll} sees {len(codes)} exams: {sorted(codes)}")


def test_SP_TC06_student_without_registrations(client, sample):
    publish(client)
    db.session.add(Student(student_id="S901", name="No Exams", program="B.Tech CSE", section="A", semester=5))
    db.session.commit()
    create_student_accounts(sample.config["STUDENT_DEFAULT_PASSWORD"])
    browser = student(sample, "S901")
    assert b"no exams in the published timetable" in browser.get("/student/timetable").data
    assert b"no exams in the published timetable" in browser.get("/student/").data
    record("SP_TC06", "'You have no exams in the published timetable.' on the timetable and the dashboard")


def test_SP_TC07_exam_info_is_against_the_right_course(client, sample):
    publish(client)
    roll = students_with_two_exams()[0]
    browser = student(sample, roll)
    for exam in get_student_exams(roll):
        detail = page_of(browser, f"/student/exam/{exam['course']}")
        assert exam["course_name"] in detail
        entry = TimetableEntry.query.filter_by(course_code=exam["course"]).join(Timetable) \
            .filter(Timetable.status == "PUBLISHED").one()
        assert str(entry.exam_date.day).zfill(2) in detail and entry.session_code in detail
    names = [e["course_name"] for e in get_student_exams(roll)]
    record("SP_TC07", f"{roll}: each detail page shows its own course name and slot ({names})")


def test_SP_TC08_only_own_exam_info_is_shown(client, sample):
    publish(client)
    a = "S001"
    courses_a = {r.course_code for r in Registration.query.filter_by(student_id=a)}
    b = next(r.student_id for r in Registration.query.all() if r.course_code not in courses_a)
    only_b = {r.course_code for r in Registration.query.filter_by(student_id=b)} - courses_a
    page_a = page_of(student(sample, a), "/student/timetable")
    assert all(course in page_a for course in courses_a)
    assert not any(course in page_a for course in only_b) and f"({b})" not in page_a
    record("SP_TC08", f"S001's page shows {sorted(courses_a)} only; {b}'s {sorted(only_b)} absent")


# ================================================================ published timetable (TC09 - TC12)
def test_SP_TC09_published_timetable_is_shown(client, sample):
    publish(client)
    page = page_of(student(sample), "/student/timetable")
    assert all(e["course"] in page for e in get_student_exams("S001")) and "has not been published yet" not in page
    record("SP_TC09", "timetable page lists every exam of S001")


def test_SP_TC10_correct_date_and_session(client, sample):
    publish(client)
    browser = student(sample)
    for exam in get_student_exams("S001"):
        detail = page_of(browser, f"/student/exam/{exam['course']}")
        day, month = exam["date"][8:10], exam["date"][5:7]
        assert f"{day} Nov 2026" in detail and month == "11"
        assert f"{exam['session']} session" in detail
        assert f"{exam['start_time']}&ndash;{exam['end_time']}" in detail
    record("SP_TC10", "date, session and times on every detail page match the published entry")


def test_SP_TC11_exams_are_in_chronological_order(client, sample):
    roll = register_for_three()
    publish(client)
    exams = get_student_exams(roll)
    dates = [e["date"] for e in exams]
    assert dates == sorted(dates) and len(set(dates)) == 3     # three different days
    page = page_of(student(sample, roll), "/student/timetable")
    positions = [page.index(f'student/exam/{e["course"]}') for e in exams]
    assert positions == sorted(positions)
    record("SP_TC11", f"{roll}: {dates} shown in that order")


def test_SP_TC12_revised_timetable_shows_latest_published_dates(client, sample):
    first = publish(client)
    course = get_student_exams("S001")[0]["course"]
    browser = student(sample)                                                # stays logged in across the republish
    old_page = page_of(browser, "/student/timetable")
    revised, old, new = publish_with_moved_exam(client, "S001", course)      # the admin publishes a revised date
    assert db.session.get(Timetable, first.id).status == "ARCHIVED" and old != new
    assert shown(old) in old_page                                            # the student saw the old date before
    page = page_of(browser, "/student/timetable")
    assert shown(new) in page and shown(old) not in page
    assert shown(new) in page_of(browser, f"/student/exam/{course}")
    record("SP_TC12", f"{course} moved {shown(old)} -> {shown(new)}; the student page shows only the new date")


# ================================================================ room and seat (TC13 - TC16)
def test_SP_TC13_allocated_room_is_shown(client, sample):
    publish(client)
    browser = student(sample)
    for exam in get_student_exams("S001"):
        seat = SeatAssignment.query.filter_by(student_id="S001").join(TimetableEntry) \
            .filter(TimetableEntry.course_code == exam["course"]).one()
        assert seat.room_id == exam["room"] and exam["room"] in page_of(browser, f"/student/exam/{exam['course']}")
    record("SP_TC13", f"rooms {[e['room'] for e in get_student_exams('S001')]} match the allocation")


def test_SP_TC14_allocated_seat_is_shown(client, sample):
    publish(client)
    browser = student(sample)
    for seat in SeatAssignment.query.filter_by(student_id="S001"):
        assert seat.seat_no in page_of(browser, f"/student/exam/{seat.entry.course_code}")
    record("SP_TC14", f"seats {[e['seat'] for e in get_student_exams('S001')]} match the allocation")


def test_SP_TC15_each_exam_shows_its_own_room_and_seat(client, sample):
    publish(client)
    roll = next(r for r in students_with_two_exams()
                if len({e["seat"] for e in get_student_exams(r)}) == 2)   # two exams, two different seats
    browser = student(sample, roll)
    first, second = get_student_exams(roll)
    for exam, other in ((first, second), (second, first)):
        detail = page_of(browser, f"/student/exam/{exam['course']}")
        assert exam["seat"] in detail and exam["room"] in detail and other["seat"] not in detail
    record("SP_TC15", f"{roll}: {first['course']}={first['seat']}, {second['course']}={second['seat']}")


def test_SP_TC16_missing_seat_shows_a_message(client, sample):
    publish(client)
    before = len(get_student_exams("S001"))
    seat = SeatAssignment.query.filter_by(student_id="S001").first()
    code = seat.entry.course_code
    db.session.delete(seat)                                    # the exam exists but this student has no seat
    db.session.commit()
    exams = get_student_exams("S001")
    assert code in {e["course"] for e in exams} and len(exams) == before
    browser = student(sample)
    assert NOT_YET in browser.get("/student/timetable").data
    assert NOT_YET in browser.get(f"/student/exam/{code}").data
    assert browser.get("/student/").status_code == 200
    record("SP_TC16", f"{code} still listed, 'Seating information not yet available' shown")


# ================================================================ publication (TC17 - TC20)
def test_SP_TC17_nothing_before_publication(client, sample):
    client.post("/timetable/generate")                          # a draft exists, nothing is published
    assert Timetable.query.one().status == "DRAFT"
    browser = student(sample)
    page = page_of(browser, "/student/timetable")
    assert "has not been published yet" in page and "CS30" not in page
    assert browser.get("/student/exam/CS301").status_code == 302
    record("SP_TC17", "'not published yet' page, no exam details")


def test_SP_TC18_details_after_publication(client, sample):
    publish(client)
    page = page_of(student(sample), "/student/timetable")
    assert "has not been published yet" not in page and get_student_exams("S001")[0]["seat"] in page
    record("SP_TC18", "after publishing, exams, rooms and seats are available")


def test_SP_TC19_seating_stays_hidden_before_publication(client, sample):
    client.post("/timetable/generate")
    seat = SeatAssignment.query.filter_by(student_id="S001").first().seat_no   # seating exists in the draft
    browser = student(sample)
    for url in ("/student/", "/student/timetable", "/student/exam/CS301"):
        response = browser.get(url, follow_redirects=True)
        assert seat.encode() not in response.data, url
    record("SP_TC19", f"draft seat {seat} never appears on any student page")


def test_SP_TC20_latest_published_schedule_is_shown(client, sample):
    first = publish(client)
    course = get_student_exams("S001")[0]["course"]
    latest, old, new = publish_with_moved_exam(client, "S001", course)       # a new validated version goes out
    assert Timetable.query.filter_by(status="PUBLISHED").one().id == latest.id != first.id
    page = page_of(student(sample), "/student/timetable")
    assert shown(new) in page and shown(old) not in page                      # the superseded schedule is gone
    seats = {s.seat_no for s in SeatAssignment.query.join(TimetableEntry)
             .filter(TimetableEntry.timetable_id == latest.id, SeatAssignment.student_id == "S001")}
    assert seats and all(seat in page for seat in seats)
    record("SP_TC20", f"S001 sees timetable #{latest.id} ({course} on {shown(new)}), not #{first.id} ({shown(old)})")


# ================================================================ instructions (TC21 - TC24)
def test_SP_TC21_configured_instructions_are_shown(client, sample):
    set_instructions(client, "CS301", "Calculators are allowed.")
    publish(client)
    browser = student(sample)
    code = next(e["course"] for e in get_student_exams("S001") if e["course"] == "CS301")
    assert "Calculators are allowed." in page_of(browser, f"/student/exam/{code}")
    assert "Calculators are allowed." in page_of(browser, "/student/timetable")
    record("SP_TC21", "CS301's instructions appear on its card and on its detail page")


def test_SP_TC22_no_instructions_is_not_an_error(client, sample):
    publish(client, instructions="")
    browser = student(sample)
    for url in ("/student/", "/student/timetable", f"/student/exam/{get_student_exams('S001')[0]['course']}"):
        response = browser.get(url)
        assert response.status_code == 200 and b"Remember on exam day" not in response.data, url
    record("SP_TC22", "no instructions anywhere: pages load (200) and the empty box is not drawn")


def two_courses_with_instructions(admin):
    """A student with two exams, and each of those two courses given its own instruction text."""
    roll = students_with_two_exams()[0]
    first, second = sorted(r.course_code for r in Registration.query.filter_by(student_id=roll))
    set_instructions(admin, first, f"{first}-ONLY: open book.")
    set_instructions(admin, second, f"{second}-ONLY: no calculators.")
    return roll, first, second


def test_SP_TC23_each_course_shows_its_own_instructions(client, sample):
    roll, first, second = two_courses_with_instructions(client)
    publish(client)
    page = page_of(student(sample, roll), "/student/timetable")
    by_course = {card.split("student/exam/")[1].split('"')[0]: card for card in cards(page)}
    assert f"{first}-ONLY: open book." in by_course[first] and f"{second}-ONLY" not in by_course[first]
    assert f"{second}-ONLY: no calculators." in by_course[second] and f"{first}-ONLY" not in by_course[second]
    record("SP_TC23", f"{roll}: the {first} card and the {second} card each carry only their own instructions")


def test_SP_TC24_detail_page_shows_only_that_exams_instructions(client, sample):
    roll, first, second = two_courses_with_instructions(client)
    publish(client)
    detail = page_of(student(sample, roll), f"/student/exam/{first}")
    assert f"{first}-ONLY: open book." in detail and f"{second}-ONLY" not in detail
    record("SP_TC24", f"{first} detail page shows {first}'s text, not {second}'s")


# ================================================================ own data only (TC25 - TC28)
def test_SP_TC25_another_students_timetable_is_not_available(client, sample):
    publish(client)
    browser = student(sample, "S001")
    mine = {e["seat"] for e in get_student_exams("S001")}
    theirs = [e["seat"] for e in get_student_exams("S100") if e["seat"] not in mine]
    for url in ("/student/timetable?student_id=S100", "/student/?student=S100", "/student/timetable/S100"):
        response = browser.get(url)
        assert response.status_code in (200, 404), url
        assert not any(seat.encode() in response.data for seat in theirs), url
    assert browser.get("/student/timetable/S100").status_code == 404
    record("SP_TC25", "no address or parameter shows S100's timetable to S001")


def test_SP_TC26_another_students_seat_is_not_disclosed(client, sample):
    publish(client)
    browser = student(sample, "S001")
    mine = get_student_exams("S001")[0]
    other = next(e for e in get_student_exams("S100") if e["course"] == mine["course"]) \
        if any(e["course"] == mine["course"] for e in get_student_exams("S100")) else None
    detail = browser.get(f"/student/exam/{mine['course']}?student_id=S100").data.decode()
    assert mine["seat"] in detail
    if other:
        assert other["seat"] not in detail
    assert browser.get("/student/exam/CS303").status_code == 404      # an exam S001 is not registered for
    record("SP_TC26", "?student_id=S100 is ignored; only S001's own seat is shown")


def _snapshot():
    return ([(e.id, str(e.exam_date), e.session_code) for e in TimetableEntry.query.order_by(TimetableEntry.id)],
            [(s.id, s.room_id, s.seat_no) for s in SeatAssignment.query.order_by(SeatAssignment.id)])


def test_SP_TC27_student_cannot_change_the_exam_date(client, sample):
    publish(client)
    browser = student(sample)
    before = _snapshot()
    course = get_student_exams("S001")[0]["course"]
    for method in ("post", "put", "patch", "delete"):
        for url in (f"/student/exam/{course}", "/student/timetable"):
            response = getattr(browser, method)(url, data={"date": "2026-12-25", "exam_date": "2026-12-25"})
            assert response.status_code == 405, (method, url)
    assert _snapshot() == before
    record("SP_TC27", "POST/PUT/PATCH/DELETE on exam pages -> 405, dates unchanged")


def test_SP_TC28_student_cannot_change_the_seat(client, sample):
    publish(client)
    browser = student(sample)
    before = _snapshot()
    course = get_student_exams("S001")[0]["course"]
    for method in ("post", "put", "patch", "delete"):
        response = getattr(browser, method)(f"/student/exam/{course}", data={"seat": "R101-S99", "room": "R999"})
        assert response.status_code == 405, method
    assert _snapshot() == before
    assert get_student_exams("S001")[0]["seat"] != "R101-S99"
    record("SP_TC28", "seat/room change attempts -> 405, assignments unchanged")


# ================================================================ non-functional (TC29 - TC32)
def test_SP_TC29_pages_are_mobile_friendly(client, sample):
    publish(client)
    browser = student(sample)
    for url in ("/student/", "/student/timetable", "/student/notifications"):
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in page_of(browser, url), url
    css = browser.get("/static/css/student.css").data.decode()
    assert "@media (max-width: 767.98px)" in css and "@media" in css
    record("SP_TC29", "viewport meta on every student page; student.css has @media rules for small screens")


def test_SP_TC30_dashboard_loads_within_three_seconds(client, sample):
    publish(client)
    browser = student(sample)
    started = time.perf_counter()
    response = browser.get("/student/")
    seconds = time.perf_counter() - started
    assert response.status_code == 200 and seconds < 3
    record("SP_TC30", f"dashboard answered in {seconds:.2f} s")


def test_SP_TC31_network_problems_show_a_proper_message(client, sample):
    publish(client)
    browser = student(sample)
    # (a) the page itself warns when the browser goes offline, and says when it is back
    page = page_of(browser, "/student/timetable")
    assert 'id="connection-alert"' in page and "'offline'" in page and "'online'" in page

    # (b) a missing page renders the friendly error page inside the student layout
    missing = browser.get("/student/exam/CS303")
    assert missing.status_code == 404 and b"Back to dashboard" in missing.data and b"student-nav" in missing.data

    # (c) a crash while loading shows an error page, not a stack trace
    sample.config["PROPAGATE_EXCEPTIONS"] = False
    import app.routes.student as portal
    original = portal.get_student_exams
    portal.get_student_exams = lambda student_id: 1 / 0
    try:
        crashed = browser.get("/student/timetable")
    finally:
        portal.get_student_exams = original
    assert crashed.status_code == 500 and b"Something went wrong" in crashed.data
    assert b"Traceback" not in crashed.data and b"ZeroDivisionError" not in crashed.data
    assert browser.get("/student/timetable").status_code == 200       # and the site keeps working
    record("SP_TC31", "offline alert present; 404 and 500 render error.html with a way back")


def test_SP_TC32_refresh_shows_the_latest_published_info(client, sample):
    publish(client)
    browser = student(sample)
    first = browser.get("/student/timetable")
    assert "no-store" in first.headers["Cache-Control"]        # the browser may not reuse an old copy
    for url in ("/student/", "/student/exam/CS301", "/student/notifications"):
        assert "no-store" in browser.get(url).headers["Cache-Control"], url

    latest = publish(client)                                    # a new version goes out, then the student refreshes
    seats = {s.seat_no for s in SeatAssignment.query.join(TimetableEntry)
             .filter(TimetableEntry.timetable_id == latest.id, SeatAssignment.student_id == "S001")}
    refreshed = browser.get("/student/timetable").data.decode()
    assert all(seat in refreshed for seat in seats)
    record("SP_TC32", "Cache-Control: no-store on student pages; a refresh shows the newest publication")


# ================================================================ extra: existing databases keep working
def test_course_instructions_column_is_added_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)                          # a database made before exam_instructions existed
    connection.execute("CREATE TABLE course (course_code VARCHAR(20) PRIMARY KEY, name VARCHAR(120) NOT NULL, "
                       "semester INTEGER NOT NULL, exam_duration_minutes INTEGER NOT NULL)")
    connection.execute("INSERT INTO course VALUES ('CS301', 'Software Engineering', 5, 180)")
    connection.commit()
    connection.close()
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "TESTING": True, "CSRF_ENABLED": False})
    with app.app_context():
        course = db.session.get(Course, "CS301")
        assert course.name == "Software Engineering" and course.exam_instructions is None
        db.session.remove()
        db.engine.dispose()
