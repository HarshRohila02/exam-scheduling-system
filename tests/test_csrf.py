"""CSRF protection (SRS 3.6 Security). The shared test client switches it off, so these tests
build an app with the protection on and talk to the login form, which is the first POST anyone makes."""
import re

import pytest

from app import create_app


@pytest.fixture
def secure():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True, "SECRET_KEY": "test-key"})
    return app, app.test_client()


def login_data(app, token):
    return {"username": app.config["ADMIN_USERNAME"], "password": app.config["ADMIN_PASSWORD"],
            "csrf_token": token}


def test_post_without_any_token_is_rejected(secure):
    app, client = secure                                         # no page was opened, so there is no session token
    assert client.post("/login", data=login_data(app, "")).status_code == 400


def test_wrong_empty_or_non_ascii_token_is_rejected(secure):
    app, client = secure
    client.get("/login")                                         # now the session has a token
    for bad in ("", "wrong", "é"):                          # the last one used to cause a server error
        assert client.post("/login", data=login_data(app, bad)).status_code == 400


def test_the_right_token_is_accepted(secure):
    app, client = secure
    page = client.get("/login").data.decode()
    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', page).group(1)
    assert client.post("/login", data=login_data(app, token)).status_code == 302
