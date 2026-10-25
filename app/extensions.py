"""Shared Flask extensions (created here so every module can import them without cycles)."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
