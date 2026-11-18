from collections import Counter
from html.parser import HTMLParser

from app.models import Faculty, Timetable, ExamSession, Course
from app.extensions import db


class DutyRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.cell = None
        self.row = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag == 'td':
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr' and self.row:
            self.rows.append(tuple(self.row))


def test_each_signed_in_faculty_receives_their_published_room_assignments(client, sample):
    browsers = {}
    for person in Faculty.query:
        browser = sample.test_client()
        browser.post('/login', data={'username': person.faculty_id, 'password': 'faculty123'})
        browsers[person.faculty_id] = browser

    for _ in range(2):
        client.post('/timetable/generate')
        table = Timetable.query.filter_by(status='DRAFT').one()
        client.post(f'/timetable/{table.id}/publish')
        assert table.status == 'PUBLISHED'
        for faculty_id, browser in browsers.items():
            assigned = []
            for exam in table.entries:
                slot = db.session.get(ExamSession, exam.session_code)
                course = db.session.get(Course, exam.course_code)
                for duty in exam.duties:
                    if duty.faculty_id == faculty_id:
                        assigned.append((str(exam.exam_date), f'{slot.start_time}–{slot.end_time}',
                                         f'{exam.course_code} · {course.name}', duty.room_id))
            for path in ('/faculty/', '/faculty/timetable'):
                page = browser.get(path)
                assert page.status_code == 200
                html = page.get_data(as_text=True)
                assert f'timetable #{table.id}' in html
                parser = DutyRows()
                parser.feed(html)
                assert Counter(parser.rows) == Counter(assigned)
