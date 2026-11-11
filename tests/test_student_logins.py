"""Admin Portal - Student Logins page: the admin gives every student a login (enrollment number + their own
temporary password), and can reset one student's password."""
import re

from app.extensions import db
from app.models import AuditLog, Student, User


def drop_student_logins():
    User.query.filter_by(role="STUDENT").delete()
    db.session.commit()


def passwords_on(page):
    """{enrollment number: temporary password} read from the one-time password list."""
    return dict(re.findall(r"<tr><td>([A-Z]\d+)</td>.*?<code[^>]*>([^<]+)</code>", page.data.decode(), re.S))


def login(app, username, password):
    return app.test_client().post("/login", data={"username": username, "password": password})


def test_page_shows_login_status_of_every_student(client, sample):
    page = client.get("/student-logins/").data.decode()
    assert "Student Logins" in page and "S001" in page and "Aarav Sharma" in page
    assert "Temporary password" in page                      # seed.py gave everyone the demo password
    assert "Create logins for" not in page                    # nobody is missing a login
    found = client.get("/student-logins/?q=Aarav").data.decode()
    assert "S001" in found and "S002" not in found


def test_create_logins_gives_each_student_their_own_password(client, sample):
    drop_student_logins()
    assert b"Create logins for 165 students without one" in client.get("/student-logins/").data

    response = client.post("/student-logins/create")
    assert response.headers["Cache-Control"] == "no-store"     # the browser must not keep the password list
    passwords = passwords_on(response)
    assert len(passwords) == 165 and len(set(passwords.values())) == 165   # all different
    assert b"Download CSV" in response.data and b"shown only once" in response.data
    assert User.query.filter_by(role="STUDENT", must_change_password=True).count() == 165
    assert AuditLog.query.filter_by(action="CREATE_LOGINS").count() == 1

    # the student logs in with their enrollment number and the password from the list, then must change it
    ok = login(sample, "S001", passwords["S001"])
    assert ok.headers["Location"].endswith("/student/")
    assert b"Wrong username or password" in login(sample, "S001", passwords["S002"]).data

    again = client.post("/student-logins/create", follow_redirects=True)
    assert b"Every student already has a login." in again.data   # running it twice makes nothing new


def test_new_students_added_later_get_logins(client, sample):
    db.session.add(Student(student_id="S900", name="New Student", program="B.Tech CSE", section="A", semester=5))
    db.session.commit()
    assert b"Create logins for 1 student without one" in client.get("/student-logins/").data
    assert list(passwords_on(client.post("/student-logins/create"))) == ["S900"]


def test_reset_password_replaces_the_old_one(client, sample):
    student = sample.test_client()
    student.post("/login", data={"username": "S001", "password": sample.config["STUDENT_DEFAULT_PASSWORD"]})
    student.post("/student/password", data={"current_password": sample.config["STUDENT_DEFAULT_PASSWORD"],
                                             "new_password": "forgotten-pass-1", "confirm_password": "forgotten-pass-1"})
    assert User.query.filter_by(username="S001").one().must_change_password is False

    new_password = passwords_on(client.post("/student-logins/S001/reset"))["S001"]
    assert b"Wrong username or password" in login(sample, "S001", "forgotten-pass-1").data
    assert login(sample, "S001", new_password).headers["Location"].endswith("/student/")
    assert User.query.filter_by(username="S001").one().must_change_password is True
    assert AuditLog.query.filter_by(action="RESET_PASSWORD", details="Student S001").count() == 1
    assert client.post("/student-logins/NOBODY/reset").status_code == 404


def test_only_the_admin_can_manage_logins(sample):
    student = sample.test_client()
    student.post("/login", data={"username": "S001", "password": sample.config["STUDENT_DEFAULT_PASSWORD"]})
    for response in (student.get("/student-logins/"), student.post("/student-logins/S002/reset"),
                     student.post("/student-logins/create")):
        assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert b"Wrong username or password" not in login(sample, "S002", sample.config["STUDENT_DEFAULT_PASSWORD"]).data
