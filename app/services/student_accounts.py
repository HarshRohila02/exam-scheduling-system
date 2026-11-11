"""Login accounts for students (Student Portal, SRS 3.2.7 and 3.6).

Login ID: a student's username is their enrollment number (User.username == Student.student_id), so the portal
always knows whose data to show without an extra link column.

Passwords: every new account gets a temporary password, and the student must choose their own at first login.
- The admin's Student Logins page gives each student their OWN random temporary password, shown once on a
  printable / downloadable list that the exam cell hands out (create_logins_with_unique_passwords, reset_password).
- `python seed.py` (sample data for demos and tests) gives every student the same STUDENT_DEFAULT_PASSWORD from .env.
"""
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import Student, User

MIN_PASSWORD_LENGTH = 8
TEMPORARY_PASSWORD_LENGTH = 10
# letters and digits that cannot be confused when read from paper (no 0/O, 1/l/I)
PASSWORD_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
# Temporary passwords are long and random and must be changed at first login, so a lighter hash is enough
# and lets the admin create logins for a whole batch in seconds. A student's own password (set_password)
# uses Werkzeug's strong default.
TEMPORARY_HASH_METHOD = "pbkdf2:sha256:10000"


def make_temporary_password() -> str:
    return "".join(secrets.choice(PASSWORD_LETTERS) for _ in range(TEMPORARY_PASSWORD_LENGTH))


def students_without_login():
    taken = {name for (name,) in db.session.query(User.username)}
    return [s for s in Student.query.order_by(Student.student_id) if s.student_id not in taken]


def create_student_accounts(default_password: str) -> int:
    """seed.py: make an account with the shared demo password for every student who has none. Returns how many.

    Safe to run again after adding students: existing accounts (and changed passwords) are left alone."""
    shared_hash = generate_password_hash(default_password)   # one hash, because the password is the same for all
    missing = students_without_login()
    for student in missing:
        db.session.add(User(username=student.student_id, role="STUDENT",
                            password_hash=shared_hash, must_change_password=True))
    db.session.commit()
    return len(missing)


def create_logins_with_unique_passwords():
    """Admin page: a login with its own random temporary password for every student who has none.

    Returns [(student, temporary_password), ...]. The passwords are not stored anywhere in readable form,
    so the caller must show them to the admin now."""
    made = []
    for student in students_without_login():
        password = make_temporary_password()
        db.session.add(User(username=student.student_id, role="STUDENT", must_change_password=True,
                            password_hash=generate_password_hash(password, method=TEMPORARY_HASH_METHOD)))
        made.append((student, password))
    return made


def reset_password(student_id: str):
    """Admin page: give one student a new temporary password (for example after "I forgot my password").

    Makes the login if the student has none yet. Returns the new password, or None if there is no such student."""
    if db.session.get(Student, student_id) is None:
        return None
    user = User.query.filter_by(username=student_id).first()
    if user is None:
        user = User(username=student_id, role="STUDENT")
        db.session.add(user)
    elif user.role != "STUDENT":
        return None                                          # never touch an admin or faculty account
    password = make_temporary_password()
    user.password_hash = generate_password_hash(password, method=TEMPORARY_HASH_METHOD)
    user.must_change_password = True
    return password


def check_new_password(user: User, current: str, new: str, confirm: str, default_password: str) -> list:
    """Rules for a new password. Returns a list of messages; an empty list means the change is allowed."""
    errors = []
    if not check_password_hash(user.password_hash, current):
        errors.append("Your current password is wrong.")
    if len(new) < MIN_PASSWORD_LENGTH:
        errors.append(f"The new password must have at least {MIN_PASSWORD_LENGTH} characters.")
    if new != confirm:
        errors.append("The two new passwords do not match.")
    if new == current or new == default_password:
        errors.append("Choose a password different from your current and the temporary password.")
    if new.upper() == user.username.upper():
        errors.append("Your password cannot be your enrollment number.")
    return errors


def set_password(user: User, new: str) -> None:
    user.password_hash = generate_password_hash(new)
    user.must_change_password = False
