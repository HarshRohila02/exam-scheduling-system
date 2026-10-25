"""SRS 3.2.1 - Data Management: validation, CRUD and CSV upload."""
import io

from app.extensions import db
from app.models import Registration, Room, Student


def post_student(client, **overrides):
    data = {"student_id": "S900", "name": "Test Student", "program": "B.Tech CSE", "section": "A",
            "semester": "5", "email": "s900@university.edu"}
    data.update(overrides)
    return client.post("/data/students/new", data=data, follow_redirects=True)


def upload(client, entity, text, filename="data.csv"):
    return client.post(f"/data/{entity}/upload", follow_redirects=True,
                       data={"file": (io.BytesIO(text.encode("utf-8")), filename)},
                       content_type="multipart/form-data")


def test_login_required(app):
    response = app.test_client().get("/data/students/")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_add_valid_student(client):
    response = post_student(client)
    assert b"Student added" in response.data
    assert db.session.get(Student, "S900").name == "Test Student"


def test_missing_required_field_is_rejected(client):
    response = post_student(client, name="")
    assert b"Name is required" in response.data
    assert db.session.get(Student, "S900") is None


def test_duplicate_id_is_rejected(client):
    post_student(client)
    response = post_student(client, name="Someone Else")
    assert b"Student ID S900 already exists" in response.data
    assert Student.query.count() == 1


def test_invalid_values_are_rejected(client):
    assert b"Semester must be between 1 and 8" in post_student(client, semester="9").data
    assert b"not a valid email" in post_student(client, email="nope").data
    assert b"may contain only letters" in post_student(client, student_id="S 9!").data
    assert Student.query.count() == 0


def test_edit_changes_name_but_not_id(client):
    post_student(client)
    client.post("/data/students/S900/edit", follow_redirects=True,
                data={"student_id": "HACK", "name": "Renamed", "program": "B.Tech CSE", "section": "B",
                      "semester": "6", "email": ""})
    student = db.session.get(Student, "S900")
    assert student.name == "Renamed" and student.section == "B" and student.semester == 6
    assert db.session.get(Student, "HACK") is None


def test_delete_student_removes_registrations(client, sample):
    before = Registration.query.filter_by(student_id="S001").count()
    assert before == 2                                   # CS301 and MA201
    response = client.post("/data/students/S001/delete", follow_redirects=True)
    assert b"Student deleted" in response.data
    assert db.session.get(Student, "S001") is None
    assert Registration.query.filter_by(student_id="S001").count() == 0


def test_csv_import_valid_file(client):
    text = ("room_id,building,capacity,is_available\n"
            "A1,North,40,yes\n"
            "A2,North,25,no\n")
    response = upload(client, "rooms", text)
    assert b"Imported 2 rooms" in response.data
    assert db.session.get(Room, "A2").is_available is False


def test_csv_import_reports_every_bad_row_and_saves_nothing(client):
    text = ("room_id,building,capacity,is_available\n"
            "A1,North,40,yes\n"
            "A1,North,30,yes\n"          # duplicate inside the file
            "A3,North,zero,yes\n"        # capacity not a number
            ",North,30,yes\n")           # missing ID
    response = upload(client, "rooms", text)
    assert b"nothing was saved" in response.data
    assert b"Row 3: Room ID A1 already exists" in response.data
    assert b"Row 4: Capacity must be a whole number" in response.data
    assert b"Row 5: Room ID is required" in response.data
    assert Room.query.count() == 0


def test_csv_import_rejects_missing_column(client):
    response = upload(client, "rooms", "room_id,building\nA1,North\n")
    assert b"Missing column(s): capacity" in response.data
    assert Room.query.count() == 0


def test_csv_import_rejects_non_csv_and_empty_file(client):
    assert b"Only .csv files" in upload(client, "rooms", "x", filename="rooms.xlsx").data
    assert b"header but no data rows" in upload(client, "rooms", "room_id,building,capacity\n").data


def test_registration_must_point_to_existing_student_and_course(client, sample):
    text = "student_id,course_code,exam_type\nNOPE,CS301,regular\nS001,XX999,regular\nS001,CS301,regular\n"
    response = upload(client, "registrations", text)
    assert b"Row 2: Student NOPE does not exist" in response.data
    assert b"Row 3: Course XX999 does not exist" in response.data
    assert b"Row 4: Student S001 is already registered for CS301" in response.data


def test_faculty_designation_must_have_duty_limits(client, sample):
    text = ("faculty_id,name,designation,department,email,max_duties_override\n"
            "F9,Dr. X,Lecturer,Physics,,\n")
    response = upload(client, "faculty", text)
    assert b"has no duty limits in the active constraints" in response.data


def test_template_download(client):
    response = client.get("/data/students/template.csv")
    assert response.status_code == 200
    assert response.data.decode().splitlines()[0] == "student_id,name,program,section,semester,email"
