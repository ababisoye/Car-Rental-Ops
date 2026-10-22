import os
import secrets
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class BaseConfig:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOCAL_TIMEZONE = os.environ.get("LOCAL_TIMEZONE", "Africa/Lagos")
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 1024 * 1024


class DevelopmentConfig(BaseConfig):
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'instance' / 'car_rental.sqlite3'}"
    )
    DEBUG = False


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-only-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"


class ProductionConfig(BaseConfig):
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY or not cls.SQLALCHEMY_DATABASE_URI:
            raise RuntimeError("Production requires SECRET_KEY and DATABASE_URL environment variables")
