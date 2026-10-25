"""Application factory: builds the Flask app, connects the database and registers every page group."""
from flask import Flask, render_template

from app.auth import csrf_token, ensure_admin_user
from app.config import Config
from app.extensions import db
from app.services.constraint_versions import ensure_defaults


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    # Blueprints = groups of related pages (one file per group in app/routes)
    from app import auth
    from app.routes import audit, calendar, constraints, dashboard, data, timetable
    for module in (auth, dashboard, data, calendar, constraints, timetable, audit):
        app.register_blueprint(module.bp)

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.errorhandler(400)
    @app.errorhandler(404)
    def show_error(error):
        return render_template("error.html", error=error), error.code

    with app.app_context():
        db.create_all()
        ensure_defaults()
    ensure_admin_user(app)
    return app
