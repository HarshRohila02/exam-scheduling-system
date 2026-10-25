"""Login, logout, role check (RBAC), CSRF protection and the audit-log helper.

SRS 3.6 Security: only authenticated ADMIN users may reach the Admin Portal, and every
schedule-affecting action is written to the audit log.
"""
import secrets
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import AuditLog, User

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
        sent = request.form.get("csrf_token", "")
        if not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing form token. Reload the page and try again.")


# ---------------------------------------------------------------- login / logout
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Enter both username and password.", "danger")
        else:
            user = User.query.filter_by(username=username).first()
            if user and user.role == "ADMIN" and check_password_hash(user.password_hash, password):
                session.clear()
                session["username"], session["role"] = user.username, user.role
                log_action("LOGIN", "Admin logged in")
                db.session.commit()
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard.index"))
            flash("Wrong username or password.", "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
