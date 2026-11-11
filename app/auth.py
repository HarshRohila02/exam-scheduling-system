"""Login, logout, role check (RBAC), CSRF protection and the audit-log helper.

SRS 3.6 Security: only authenticated ADMIN users may reach the Admin Portal, a student may only see
their own data in the Student Portal, and every schedule-affecting action is written to the audit log.

How a login is linked to a person: a student's username is their enrollment number (User.username == Student.student_id).
"""
import secrets
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import AuditLog, Student, User

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
HOME_PAGES = {"ADMIN": ("dashboard.index", "/"), "STUDENT": ("student.dashboard", "/student/")}


def _find_user(username):
    """Usernames are matched exactly; enrollment numbers may also be typed in lower case (s001 -> S001)."""
    return User.query.filter_by(username=username).first() or User.query.filter_by(username=username.upper()).first()


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Enter both username and password.", "danger")
        else:
            user = _find_user(username)
            if user and user.role in HOME_PAGES and check_password_hash(user.password_hash, password):
                # a student account whose student record was deleted by the admin must not log in
                if user.role == "STUDENT" and db.session.get(Student, user.username) is None:
                    flash("No student record exists for this enrollment number. Contact the examination cell.", "danger")
                    return render_template("login.html")
                session.clear()
                session["username"], session["role"] = user.username, user.role
                if user.must_change_password:
                    session["must_change_password"] = True
                log_action("LOGIN", f"{user.role.title()} logged in")
                db.session.commit()
                home, allowed_prefix = HOME_PAGES[user.role]
                nxt = request.args.get("next", "")
                safe = nxt.startswith(allowed_prefix) and not nxt.startswith("//")
                return redirect(nxt if safe else url_for(home))
            flash("Wrong username or password.", "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
