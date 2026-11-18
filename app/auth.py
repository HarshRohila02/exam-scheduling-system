"""Login, logout, role check (RBAC), CSRF protection and the audit-log helper.

SRS 3.6 Security: only authenticated ADMIN users may reach the Admin Portal, a student may only see
their own data in the Student Portal, and every schedule-affecting action is written to the audit log.

How a login is linked to a person: a student's username is their enrollment number (User.username == Student.student_id).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import AuditLog, Faculty, FacultyBrowserLogin, Student, User

bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------- helpers used everywhere
def current_username():
    return session.get("username")


def admin_required(view):
    """Decorator: the page can only be opened by a logged-in ADMIN."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("role") != "ADMIN":
            flash("Please log in as examination staff to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def student_required(view):
    """Decorator: the page can only be opened by a logged-in STUDENT.

    A student who still has the temporary password is sent to the change-password page first."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("role") != "STUDENT":
            flash("Please log in with your enrollment number to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        if session.get("must_change_password") and request.endpoint != "student.change_password":
            flash("Please choose your own password before continuing.", "warning")
            return redirect(url_for("student.change_password"))
        return view(*args, **kwargs)
    return wrapper


def log_action(action, details=""):
    """Add an audit-log row. The caller commits it together with its own change."""
    db.session.add(AuditLog(username=current_username(), action=action, details=details[:255]))


def ensure_admin_user(app):
    """Create the first admin account from the .env settings if it does not exist yet."""
    with app.app_context():
        username = app.config["ADMIN_USERNAME"]
        if not User.query.filter_by(username=username).first():
            db.session.add(User(username=username, role="ADMIN",
                                password_hash=generate_password_hash(app.config["ADMIN_PASSWORD"])))
            db.session.commit()


# ---------------------------------------------------------------- CSRF protection
def csrf_token():
    """A random token stored in the session; every POST form must send it back."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if current_app.config.get("CSRF_ENABLED", True) and request.method == "POST":
        expected = session.get("csrf", "")
        sent = request.form.get("csrf_token", "")
        # compare as bytes: compare_digest raises an error for text with non-ASCII characters
        if not expected or not secrets.compare_digest(sent.encode(), expected.encode()):
            abort(400, "Invalid or missing form token. Reload the page and try again.")


# ---------------------------------------------------------------- login / logout
# Where each role lands after logging in, and which addresses it may be sent back to (?next=...).
# The Faculty Portal adds "FACULTY" here.
HOME_PAGES = {"FACULTY": ("faculty.dashboard", "/faculty/"), "ADMIN": ("dashboard.index", "/"), "STUDENT": ("student.dashboard", "/student/")}


def _find_user(username):
    """Usernames are matched exactly; enrollment numbers may also be typed in lower case (s001 -> S001)."""
    return User.query.filter_by(username=username).first() or User.query.filter_by(username=username.upper()).first()


def remember_faculty(user, browser):
    db.session.merge(FacultyBrowserLogin(
        browser=browser, user_id=user.id,
        credential_version=hashlib.sha256(user.password_hash.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=31)))


def signed_in_faculty(browser):
    if not browser:
        return []
    rows = (db.session.query(User, Faculty, FacultyBrowserLogin)
            .join(Faculty, Faculty.faculty_id == User.username)
            .join(FacultyBrowserLogin, FacultyBrowserLogin.user_id == User.id)
            .filter(FacultyBrowserLogin.browser == browser, User.role == "FACULTY",
                    FacultyBrowserLogin.expires_at > datetime.now(timezone.utc).replace(tzinfo=None))
            .order_by(Faculty.name).all())
    return [(user, person) for user, person, grant in rows
            if grant.credential_version == hashlib.sha256(user.password_hash.encode()).hexdigest()]


def activate_user(user, browser=None):
    session.clear()
    session["username"], session["role"] = user.username, user.role
    if browser:
        session["faculty_browser"] = browser
    if user.must_change_password:
        session["must_change_password"] = True


@bp.route("/switch-faculty", methods=["GET", "POST"])
def switch_faculty():
    browser = session.get("faculty_browser")
    # Keep a faculty session opened before the switcher was introduced.
    if not browser and session.get("role") == "FACULTY":
        user = _find_user(current_username())
        if user and user.role == "FACULTY" and db.session.get(Faculty, user.username):
            browser = secrets.token_hex(32)
            remember_faculty(user, browser)
            db.session.commit()
            session["faculty_browser"] = browser
    accounts = signed_in_faculty(browser)
    if request.method == "POST":
        selected = request.form.get("faculty_id")
        user = next((user for user, _ in accounts if user.username == selected), None)
        if user is None:
            abort(403)
        activate_user(user, browser)
        log_action("SWITCH_USER", "Switched to signed-in faculty account")
        db.session.commit()
        return redirect(url_for("faculty.dashboard"))
    if not accounts:
        return redirect(url_for("auth.login", portal="faculty"))
    return render_template("faculty/switch.html", people=[person for _, person in accounts])


@bp.route("/login", methods=["GET", "POST"])
def login():
    faculty_login = request.args.get("portal") == "faculty"
    template = "faculty/login.html" if faculty_login else "login.html"
    people = (Faculty.query.join(User, User.username == Faculty.faculty_id)
              .filter(User.role == "FACULTY").order_by(Faculty.name).all()) if faculty_login else []
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username and not password:
            flash("Please enter password", "danger")
        elif username and len(password) < 3:
            flash("Password must be at least 3 characters", "danger")
        elif not username:
            flash("Enter both username and password.", "danger")
        else:
            user = _find_user(username)
            if (user and user.role in HOME_PAGES and (not faculty_login or user.role == "FACULTY")
                    and check_password_hash(user.password_hash, password)):
                # a student account whose student record was deleted by the admin must not log in
                if user.role == "STUDENT" and db.session.get(Student, user.username) is None:
                    flash("No student record exists for this enrollment number. Contact the examination cell.", "danger")
                    return render_template(template, faculty_login=faculty_login, people=people)
                if user.role == "FACULTY" and db.session.get(Faculty, user.username) is None:
                    flash("Faculty record unavailable. Contact the examination cell.", "danger")
                    return render_template(template, faculty_login=faculty_login, people=people)
                browser = session.get("faculty_browser")
                if user.role == "FACULTY":
                    browser = browser or secrets.token_hex(32)
                    remember_faculty(user, browser)
                elif browser:
                    FacultyBrowserLogin.query.filter_by(browser=browser).delete()
                    browser = None
                activate_user(user, browser)
                log_action("LOGIN", f"{user.role.title()} logged in")
                db.session.commit()
                home, allowed_prefix = HOME_PAGES[user.role]
                nxt = "" if user.role == "FACULTY" else request.args.get("next", "")
                safe = nxt.startswith(allowed_prefix) and not nxt.startswith("//")
                return redirect(nxt if safe else url_for(home))
            flash("Invalid password" if user and user.role == "FACULTY" else "Wrong username or password.", "danger")
    return render_template(template, faculty_login=faculty_login, people=people)


@bp.route("/logout", methods=["POST"])
def logout():
    switch_faculty = session.get("role") == "FACULTY" and request.form.get("switch") == "faculty"
    browser = session.get("faculty_browser")
    if browser and session.get("role") == "FACULTY":
        user = _find_user(current_username())
        if user:
            FacultyBrowserLogin.query.filter_by(browser=browser, user_id=user.id).delete()
        db.session.commit()
    session.clear()
    if signed_in_faculty(browser):
        session["faculty_browser"] = browser
        return redirect(url_for("auth.switch_faculty"))
    if switch_faculty:
        return redirect(url_for("auth.login", portal="faculty"))
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
