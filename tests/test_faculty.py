from app.extensions import db
from app.models import DutySwap, Faculty, InvigilationDuty, Notification, Timetable
from app.services.timetable_service import validate_saved_timetable


def faculty_client(app, faculty_id):
    browser = app.test_client()
    browser.post("/login", data={"username": faculty_id, "password": "faculty123"})
    assert b"Faculty Dashboard" in browser.get("/faculty/").data
    browser.post("/faculty/password", data={"current_password": "faculty123", "new_password": "my-demo-pass",
                                            "confirm_password": "my-demo-pass"})
    return browser


def test_generate_publish_and_transfer(client, sample):
    client.post("/timetable/generate")
    table = Timetable.query.one()
    # Find a transfer allowed by the actual generated schedule, independent of solver tie breaks.
    for duty in InvigilationDuty.query.order_by(InvigilationDuty.id):
        owner = duty.faculty_id
        for person in Faculty.query:
            target = person.faculty_id
            if owner == target:
                continue
            duty.faculty_id = target
            valid = not validate_saved_timetable(table)
            duty.faculty_id = owner
            if valid:
                break
        if valid:
            break
    assert valid
    sender = faculty_client(sample, owner)
    recipient = faculty_client(sample, target)
    assert b"Draft duties" in sender.get("/faculty/timetable").data
    assert b"A published duty is required" in sender.get("/faculty/swaps").data
    assert sender.get("/exam-cell/faculty-requests").status_code == 302
    client.post(f"/timetable/{table.id}/publish")
    data = {"duty": duty.id, "recipient": target, "reason": "Department meeting"}
    sender.post("/faculty/swaps", data=data)
    sender.post("/faculty/swaps", data=data)
    swap = DutySwap.query.one()
    assert sender.post(f"/faculty/swaps/{swap.id}/approve").status_code == 403
    assert b"Department meeting" in recipient.get("/faculty/swaps").data
    recipient.post(f"/faculty/swaps/{swap.id}/reject")
    assert swap.status == "Rejected" and duty.faculty_id == owner
    sender.post("/faculty/swaps", data=data)
    swap = DutySwap.query.filter_by(status="Pending").one()
    recipient.post(f"/faculty/swaps/{swap.id}/approve")
    assert swap.status == "Approved" and duty.faculty_id == target
    assert validate_saved_timetable(table) == []
    assert b"approved" in client.get("/exam-cell/faculty-requests").data
    assert b"approved" in sender.get("/faculty/notifications").data
    assert b"Published duties" in recipient.get("/faculty/").data
    client.post("/exam-cell/faculty-requests/read")
    assert not Notification.query.filter_by(recipient_type="ADMIN", is_read=False).count()
    assert Notification.query.filter_by(recipient_type="FACULTY", is_read=False).count()


def test_block_unavailable_transfer_and_expire_on_archive(client, sample):
    from app.models import FacultyUnavailability
    client.post("/timetable/generate")
    table = Timetable.query.one()
    client.post(f"/timetable/{table.id}/publish")
    duty = InvigilationDuty.query.first()
    owner = duty.faculty_id
    target = Faculty.query.filter(Faculty.faculty_id != owner).first().faculty_id
    sender = faculty_client(sample, owner)
    recipient = faculty_client(sample, target)
    db.session.add(FacultyUnavailability(faculty_id=target, date=duty.entry.exam_date))
    db.session.commit()
    sender.post("/faculty/swaps", data={"duty": duty.id, "recipient": target, "reason": "Meeting"})
    swap = DutySwap.query.one()
    result = recipient.post(f"/faculty/swaps/{swap.id}/approve", follow_redirects=True)
    assert (b"Transfer blocked" in result.data or b"Clash: you already have a duty at that time" in result.data)
    assert duty.faculty_id == owner
    assert swap.status == "Pending"
    client.post(f"/timetable/{table.id}/archive")
    assert swap.status == "Expired"
