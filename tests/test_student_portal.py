"""SRS 3.2.7 - 3.2.8 Student Portal: student login, own exam timetable, room and seat, instructions,
notifications, and the SRS 3.6 rule that a student only ever sees their own data."""
from datetime import date

from app.extensions import db
from app.models import AuditLog, Notification, Registration, Student, Timetable, User
from app.routes.student import add_day_info
from app.services.published_view import get_student_exams
from app.services.student_accounts import create_student_accounts
from seed import seed

NEW_PASSWORD = "my-own-pass-1"


def publish(admin, instructions="Bring your ID card."):
    """Generate a draft as the admin and publish it."""
    admin.post("/timetable/generate")
    timetable = Timetable.query.order_by(Timetable.id.desc()).first()
    admin.post(f"/timetable/{timetable.id}/publish", data={"instructions": instructions})
    assert db.session.get(Timetable, timetable.id).status == "PUBLISHED"
    return timetable


def login(app, username, password=None):
    browser = app.test_client()
    response = browser.post("/login", data={"username": username,
                                            "password": password or app.config["STUDENT_DEFAULT_PASSWORD"]})
    return browser, response


def student(app, roll="S001"):
    """A browser logged in as a student who has already chosen their own password."""
    browser, _ = login(app, roll)
    browser.post("/student/password", data={"current_password": app.config["STUDENT_DEFAULT_PASSWORD"],
                                             "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    return browser


# ---------------------------------------------------------------- accounts and login
def test_seed_makes_one_login_per_student_and_is_safe_to_rerun(sample):
    accounts = User.query.filter_by(role="STUDENT")
    assert accounts.count() == Student.query.count() == 165
    assert all(u.must_change_password for u in accounts)
    assert create_student_accounts("anything") == 0            # nobody gets a second account
    db.session.add(Student(student_id="S900", name="New Student", program="B.Tech CSE", section="A", semester=5))
    db.session.commit()
    seed(sample)                                                # running seed again adds the new student's login
    assert User.query.filter_by(username="S900", role="STUDENT").count() == 1


def test_first_login_forces_a_password_change(sample):
    browser, response = login(sample, "S001")
    assert response.headers["Location"].endswith("/student/")
    page = browser.get("/student/timetable")
    assert page.status_code == 302 and page.headers["Location"].endswith("/student/password")

    browser.post("/student/password", data={"current_password": sample.config["STUDENT_DEFAULT_PASSWORD"],
                                            "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
    assert browser.get("/student/timetable").status_code == 200
    assert User.query.filter_by(username="S001").one().must_change_password is False
    assert AuditLog.query.filter_by(username="S001", action="PASSWORD_CHANGE").count() == 1

    _, again = login(sample, "S001")                            # the temporary password no longer works
    assert b"Wrong username or password" in again.data
    _, again = login(sample, "S001", NEW_PASSWORD)
    assert again.status_code == 302


def test_password_rules(sample):
    browser, _ = login(sample, "S001")
    temporary = sample.config["STUDENT_DEFAULT_PASSWORD"]
    cases = [("wrong-pass", "good-pass-123", "good-pass-123", b"current password is wrong"),
             (temporary, "short", "short", b"at least 8 characters"),
             (temporary, "good-pass-123", "other-pass-123", b"do not match"),
             (temporary, temporary, temporary, b"different from your current"),
             (temporary, "s001", "s001", b"cannot be your enrollment number")]
    for current, new, confirm, message in cases:
        page = browser.post("/student/password", follow_redirects=True,
                            data={"current_password": current, "new_password": new, "confirm_password": confirm})
        assert message in page.data, message
    assert User.query.filter_by(username="S001").one().must_change_password is True


def test_roll_number_can_be_typed_in_lower_case(sample):
    _, response = login(sample, "s001")
    assert response.status_code == 302 and response.headers["Location"].endswith("/student/")


def test_wrong_password_and_deleted_student_cannot_log_in(sample):
    _, response = login(sample, "S001", "not-the-password")
    assert b"Wrong username or password" in response.data
    db.session.delete(db.session.get(Student, "S002"))           # account left behind after the record is deleted
    db.session.commit()
    _, response = login(sample, "S002")
    assert b"No student record exists" in response.data


# ---------------------------------------------------------------- SRS 3.6: own data only
def test_student_cannot_open_admin_pages_and_admin_cannot_open_student_pages(client, sample):
    browser = student(sample)
    for url in ("/", "/timetable/", "/data/students/", "/audit/", "/constraints/"):
        response = browser.get(url)
        assert response.status_code == 302 and "/login" in response.headers["Location"], url
    assert browser.post("/timetable/generate").status_code == 302
    assert Timetable.query.count() == 0                          # the student could not generate anything

    for url in ("/student/", "/student/timetable", "/student/notifications"):
        response = client.get(url)                               # client = logged-in admin
        assert response.status_code == 302 and "/login" in response.headers["Location"], url


def test_student_login_ignores_a_next_address_outside_the_portal(sample):
    browser = sample.test_client()
    response = browser.post("/login?next=/timetable/", data={"username": "S001",
                                                             "password": sample.config["STUDENT_DEFAULT_PASSWORD"]})
    assert response.headers["Location"].endswith("/student/")


def test_timetable_hidden_until_published(client, sample):
    browser = student(sample)
    for url in ("/student/", "/student/timetable"):
        assert b"has not been published yet" in browser.get(url).data, url
    assert browser.get("/student/exam/CS301").status_code == 302   # back to the "not published" page


def test_student_sees_own_exams_rooms_seats_and_instructions(client, sample):
    publish(client, "Bring your ID card.\nReach 30 minutes early.")
    browser = student(sample)
    mine = get_student_exams("S001")
    page = browser.get("/student/timetable").data.decode()
    for exam in mine:
        assert exam["course"] in page and exam["seat"] in page and exam["room"] in page
    assert "CS303" not in page                                  # a course S001 does not take
    assert "Reach 30 minutes early." in page

    other = get_student_exams("S100")                           # another student's seats never appear
    assert not any(e["seat"] in page for e in other if e["seat"] not in {m["seat"] for m in mine})

    detail = browser.get(f"/student/exam/{mine[0]['course']}")
    assert detail.status_code == 200 and mine[0]["seat"].encode() in detail.data
    assert browser.get("/student/exam/CS303").status_code == 404   # not registered
    assert browser.get("/student/").status_code == 200


def test_backlog_exam_is_labelled(client, sample):
    publish(client)
    backlog = Registration.query.filter_by(exam_type="backlog").first()
    page = student(sample, backlog.student_id).get("/student/timetable").data
    assert b"Backlog" in page


def test_instructions_are_escaped(client, sample):
    publish(client, "<script>alert(1)</script>")
    page = student(sample).get("/student/timetable").data
    assert b"<script>alert(1)</script>" not in page and b"&lt;script&gt;" in page


def test_archived_timetable_is_hidden_from_students_again(client, sample):
    timetable = publish(client)
    client.post(f"/timetable/{timetable.id}/archive")
    assert b"has not been published yet" in student(sample).get("/student/timetable").data


# ---------------------------------------------------------------- notifications
def test_notifications_badge_and_mark_all_read_touches_only_own_rows(client, sample):
    publish(client)
    browser = student(sample)
    page = browser.get("/student/notifications").data
    assert b"Your examination timetable, rooms and seats have been published." in page
    assert b"Mark all as read" in page

    browser.post("/student/notifications/read")
    assert Notification.query.filter_by(recipient_type="STUDENT", recipient_id="S001", is_read=False).count() == 0
    assert Notification.query.filter_by(recipient_type="STUDENT", recipient_id="S002", is_read=False).count() == 1
    assert Notification.query.filter_by(recipient_type="FACULTY", is_read=False).count() == 6
    assert b"Mark all as read" not in browser.get("/student/notifications").data


# ---------------------------------------------------------------- small helper
def test_exam_states_relative_to_today():
    rows = [{"date": "2026-11-02"}, {"date": "2026-11-04"}, {"date": "2026-11-07"}]
    add_day_info(rows, date(2026, 11, 4))
    assert [r["state"] for r in rows] == ["done", "today", "upcoming"]
    assert rows[2]["days_left"] == 3 and rows[0]["weekday"] == "Monday"
