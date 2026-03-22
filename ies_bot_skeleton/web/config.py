from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


class BaseConfig:
    SECRET_KEY = os.getenv("IES_WEB_SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "IES_WEB_DATABASE_URL",
        f"sqlite:///{Path.cwd() / 'ies_web.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    LOG_LEVEL = os.getenv("IES_LOG_LEVEL", "INFO").upper()
    LOG_FORMAT = os.getenv(
        "IES_LOG_FORMAT",
        "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s user=%(user_id)s path=%(path)s] %(message)s",
    )
    LOG_TO_STDOUT = _env_bool("IES_LOG_TO_STDOUT", True)
    LOG_DIR = Path(os.getenv("IES_LOG_DIR", str(Path.cwd() / "logs")))
    APP_LOG_FILE = os.getenv("IES_APP_LOG_FILE", "ies_web.log")
    ERROR_LOG_FILE = os.getenv("IES_ERROR_LOG_FILE", "ies_web.error.log")
    ACCESS_LOG_ENABLED = _env_bool("IES_ACCESS_LOG_ENABLED", True)
    AUDIT_LOG_ENABLED = _env_bool("IES_AUDIT_LOG_ENABLED", True)
    ENABLE_GLOBAL_CHART_LIBS = _env_bool("IES_ENABLE_GLOBAL_CHART_LIBS", False)
    UI_ENABLE_GLOBAL_SEARCH = _env_bool("IES_UI_ENABLE_GLOBAL_SEARCH", False)


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    LOG_LEVEL = os.getenv("IES_LOG_LEVEL", "DEBUG").upper()


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite+pysqlite:///:memory:"
    LOG_LEVEL = "WARNING"
    ACCESS_LOG_ENABLED = False
    AUDIT_LOG_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
