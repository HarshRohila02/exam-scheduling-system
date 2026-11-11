"""Settings read from the .env file (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///aemss.db")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    # first password of every student account (python seed.py); students must change it at first login
    STUDENT_DEFAULT_PASSWORD = os.getenv("STUDENT_DEFAULT_PASSWORD", "student123")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024   # CSV uploads up to 2 MB
