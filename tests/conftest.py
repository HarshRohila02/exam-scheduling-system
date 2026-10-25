"""Shared test setup: a fresh in-memory database per test, a logged-in admin client, sample data,
and a small recorder that writes each test case's actual output to tests/test_results.json
(handy for filling the Actual Output / Status columns of the test-case document)."""
import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.extensions import db
from seed import seed

RESULTS = {}


def record(tc_id, actual):
    """Remember what a test case actually produced (called from inside the test)."""
    RESULTS[tc_id] = {"actual": str(actual), "status": "Pass"}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    match = re.match(r"test_(TC\d+)", item.name)
    if match and report.when == "call" and report.failed:
        RESULTS.setdefault(match.group(1), {"actual": "", "status": "Fail"})["status"] = "Fail"


def pytest_sessionfinish(session, exitstatus):
    if RESULTS:
        path = Path(__file__).parent / "test_results.json"
        path.write_text(json.dumps(dict(sorted(RESULTS.items())), indent=2), encoding="utf-8")


@pytest.fixture
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True,
                      "CSRF_ENABLED": False, "SECRET_KEY": "test-key"})
    with app.app_context():
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    """A browser logged in as the admin."""
    c = app.test_client()
    c.post("/login", data={"username": app.config["ADMIN_USERNAME"], "password": app.config["ADMIN_PASSWORD"]})
    return c


@pytest.fixture
def sample(app):
    """Load the sample CSV data (165 students, 5 courses, 6 faculty, 3 rooms, exam week 2-7 Nov)."""
    seed(app)
    return app
