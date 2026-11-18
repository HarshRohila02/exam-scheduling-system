"""Faculty Portal acceptance cases TC01–TC20 from Himanshi's test sheet."""
from datetime import date
from html import unescape
import re

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (Course, DutySwap, ExamCalendar, ExamRoom, Faculty, InvigilationDuty,
                        Registration, Room, SeatAssignment, Student, Timetable, TimetableEntry, User)
from app.services.constraint_versions import get_active_version

PASSWORD = "faculty-test-123"


def sign_in(browser, user, password=PASSWORD):
    return browser.post("/login?portal=faculty", data={"username": user, "password": password}, follow_redirects=True)


@pytest.fixture
def portal(app, client):
    """A has two duties. B can take A's first duty, but already works during the second."""
    pw = generate_password_hash(PASSWORD, method="pbkdf2:sha256:10000")
    for user in ("FA", "FB"):
        db.session.add(Faculty(faculty_id=user, name=user, designation="Professor", department="CSE"))
        db.session.add(User(username=user, role="FACULTY", password_hash=pw, must_change_password=True))
    db.session.add(ExamCalendar(start_date=date(2026, 11, 2), end_date=date(2026, 11, 5)))
    table = Timetable(status="DRAFT", constraint_version=get_active_version())
    db.session.add(table)
    for code, day, owner in [("LATE", 4, "FA"), ("EARLY", 2, "FA"), ("OTHER", 4, "FB")]:
        db.session.add(Course(course_code=code, name=code, semester=1))
        db.session.add(Student(student_id=code, name=code, program="CSE", section="A", semester=1))
        db.session.add(Room(room_id=code, capacity=30))
        db.session.flush()
        db.session.add(Registration(student_id=code, course_code=code))
        entry = TimetableEntry(course_code=code, exam_date=date(2026, 11, day), session_code="FN")
        entry.rooms.append(ExamRoom(room_id=code, students_count=1))
        entry.seats.append(SeatAssignment(student_id=code, room_id=code, seat_no="1"))
        entry.duties.append(InvigilationDuty(faculty_id=owner, room_id=code))
        table.entries.append(entry)
    db.session.commit()
    client.post(f"/timetable/{table.id}/publish")
    assert table.status == "PUBLISHED"
    a, b = app.test_client(), app.test_client()
    sign_in(a, "FA")
    sign_in(b, "FB")
    return a, b


def send(a, room="EARLY", reason="Department meeting"):
    duty = InvigilationDuty.query.filter_by(room_id=room).one()
    return a.post("/faculty/swaps", data={"duty": duty.id, "recipient": "FB", "reason": reason},
                  follow_redirects=True).get_data(as_text=True)


def decide(b, action):
    swap = DutySwap.query.one()
    return b.post(f"/faculty/swaps/{swap.id}/{action}", follow_redirects=True).get_data(as_text=True)


def page(browser, path):
    result = browser.get("/faculty/" + path)
    assert result.status_code == 200
    return result.get_data(as_text=True)


def alerts(html):
    blocks = re.findall(r'<div class="alert [^"]*flash"[^>]*>(.*?)<button', html, re.S)
    return [unescape(block.strip()) for block in blocks]


def badge(html, count):
    link = re.search(r'<a[^>]*href="/faculty/notifications"[^>]*>Notifications(.*?)</a>', html, re.S)
    assert link
    return f'>{count}</span>' in link[1] if count else '<span' not in link[1]


def test_faculty_TC01_valid_login(portal):
    a, _ = portal
    a.post("/logout", follow_redirects=True)
    choices = a.get("/login?portal=faculty").data
    assert b'<select class="form-select" id="username"' in choices and b'value="FA"' in choices
    response = sign_in(a, "FA")
    assert response.request.path == "/faculty/" and b"Faculty Dashboard" in response.data


@pytest.mark.parametrize(("password", "message"), [
    pytest.param("ab", "Password must be at least 3 characters", id="TC02"),
    pytest.param("", "Please enter password", id="TC03"),
    pytest.param("incorrect", "Invalid password", id="TC04")])
def test_invalid_faculty_login(portal, password, message):
    a, _ = portal
    a.post("/logout", follow_redirects=True)
    assert alerts(sign_in(a, "FA", password).get_data(as_text=True)) == [message]
    assert a.get("/faculty/").status_code == 302


def test_faculty_TC05_dashboard(portal):
    html = page(portal[0], "")
    assert "Total duties" in html and "2 / 2" in html and "All my duties" in html
    assert "EARLY" in html.split("Next duty</h2>")[1].split("</p>")[0]
    assert "EARLY" in html.split("<tbody>")[1] and "LATE" in html.split("<tbody>")[1]


def test_faculty_TC06_timetable(portal):
    html = page(portal[0], "timetable")
    assert re.findall(r"<th>(.*?)</th>", html) == ["Date", "Time", "Course", "Room"]
    assert html.index("EARLY") < html.index("LATE") and "<th>Role</th>" not in html


def test_faculty_TC07_switch_account(portal):
    a, _ = portal
    response = a.post("/logout", data={"switch": "faculty"})
    assert response.location.endswith("/login?portal=faculty")
    assert a.get("/faculty/").status_code == 302
    sign_in(a, "FB")
    for path in ("", "timetable"):
        html = page(a, path)
        assert "OTHER" in html and "EARLY" not in html and "LATE" not in html


def test_faculty_TC08_print_action(portal):
    html = page(portal[0], "timetable")
    assert re.search(r'<button[^>]+onclick="window.print\(\)"[^>]*>Print / Save as PDF</button>', html)


def test_faculty_TC09_empty_reason(portal):
    assert alerts(send(portal[0], reason=" ")) == ["Please add a reason"]
    assert DutySwap.query.count() == 0


def test_faculty_TC10_valid_request(portal):
    sent = send(portal[0]).split("My sent requests</h2>")[1]
    assert ">pending</span>" in sent and "Department meeting" in sent


def test_faculty_TC11_duplicate(portal):
    send(portal[0])
    assert alerts(send(portal[0])) == ["Request already pending for this duty"]
    assert DutySwap.query.count() == 1


def test_faculty_TC12_swap_partners(portal):
    html = page(portal[0], "swaps").split('name="recipient"', 1)[1].split("</select>")[0]
    assert 'value="FB"' in html and 'value="FA"' not in html


def test_faculty_TC13_incoming_controls(portal):
    a, b = portal
    send(a)
    html = page(b, "swaps").split("Incoming requests</h2>")[1].split("My sent requests")[0]
    assert ">Approve</button>" in html and ">Reject</button>" in html


def test_faculty_TC14_approve(portal):
    a, b = portal
    send(a)
    assert ">approved</span>" in decide(b, "approve")
    assert "EARLY" not in page(a, "timetable") and "EARLY" in page(b, "timetable")


def test_faculty_TC15_reject(portal):
    a, b = portal
    send(a)
    assert ">rejected</span>" in decide(b, "reject")
    assert "EARLY" in page(a, "timetable") and "EARLY" not in page(b, "timetable")


def test_faculty_TC16_clash(portal):
    a, b = portal
    send(a, room="LATE")
    assert alerts(decide(b, "approve")) == ["Clash: you already have a duty at that time"]
    assert DutySwap.query.one().status == "Pending"
    assert "LATE" in page(a, "timetable") and "LATE" not in page(b, "timetable")


def test_faculty_TC17_unread_assignment(portal):
    html = page(portal[0], "notifications")
    assert "published" in html and "bg-primary-subtle" in html and badge(html, 1)


def test_faculty_TC18_new_request_notice(portal):
    a, b = portal
    send(a)
    html = page(b, "notifications")
    assert "requested." in html and badge(html, 2)


@pytest.mark.parametrize("action,decision", [("approve", "approved"), ("reject", "rejected")])
def test_faculty_TC19_decision_notice(portal, action, decision):
    a, b = portal
    send(a)
    decide(b, action)
    assert decision + "." in page(a, "notifications")


def test_faculty_TC20_mark_read(portal):
    a, b = portal
    a.post("/faculty/notifications/read")
    html = page(a, "notifications")
    assert badge(html, 0) and "bg-primary-subtle" not in html and ">New</span>" not in html
    assert "published" in html and badge(page(b, "notifications"), 1)
