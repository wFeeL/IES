from __future__ import annotations

import logging
import os
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from flask import Flask, g, jsonify, request
from flask_wtf.csrf import CSRFError, generate_csrf
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.middleware.proxy_fix import ProxyFix

from .cli import init_cli
from .config import CONFIG_MAP
from .extensions import csrf, db, login_manager, migrate
from .models import User
from .routes import register_blueprints
from .services.navigation import (
    build_breadcrumbs,
    is_safe_internal_url,
    safe_back_url,
    safe_next_url,
)
from .services.formatting import display_or_dash, format_number, format_tick_range
from .services.ui_text import (
    CATEGORY_LABELS,
    SESSION_TERMS,
    category_label,
    lot_scope_label,
    lot_status_label,
    stale_reason_label,
    strategy_label,
)


def _is_local_sqlite_uri(uri: str) -> bool:
    normalized = str(uri or "").strip().lower()
    return normalized.startswith("sqlite:///") or normalized.startswith("sqlite+pysqlite:///")


def _bootstrap_local_sqlite(app: Flask) -> None:
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if app.config.get("TESTING") or not _is_local_sqlite_uri(database_uri):
        return
    with app.app_context():
        try:
            db.create_all()
            from .services.seed import ensure_seed_data
            ensure_seed_data()
            db.session.remove()
        except SQLAlchemyError:
            app.logger.exception("Database bootstrap failed.")
            db.session.remove()


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, "request_id", "-")
        record.user_id = getattr(getattr(g, "current_user", None), "id", "-")
        try:
            record.path = request.path
        except RuntimeError:
            record.path = "-"
        return True


def _configure_logging(app: Flask) -> None:
    level_name = str(app.config.get("LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(str(app.config.get("LOG_FORMAT")))
    request_filter = RequestContextFilter()

    app.logger.handlers.clear()
    app.logger.setLevel(level)
    app.logger.propagate = False

    handlers = []
    if app.config.get("LOG_TO_STDOUT", True):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(request_filter)
        handlers.append(stream_handler)

    log_dir = Path(app.config.get("LOG_DIR") or Path.cwd() / "logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        app_file = log_dir / str(app.config.get("APP_LOG_FILE", "ies_web.log"))
        err_file = log_dir / str(app.config.get("ERROR_LOG_FILE", "ies_web.error.log"))

        file_handler = RotatingFileHandler(app_file, maxBytes=1_500_000, backupCount=4, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        handlers.append(file_handler)

        error_handler = RotatingFileHandler(err_file, maxBytes=1_500_000, backupCount=4, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        error_handler.addFilter(request_filter)
        handlers.append(error_handler)
    except OSError:
        pass

    for handler in handlers:
        app.logger.addHandler(handler)

    logging.getLogger("werkzeug").setLevel(max(level, logging.INFO))


def create_app(config_name: Optional[str] = None) -> Flask:
    env_name = config_name or os.getenv("IES_WEB_ENV", "development")
    cfg = CONFIG_MAP.get(env_name, CONFIG_MAP["development"])

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(cfg)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.before_request
    def _prepare_request_context():
        g.request_id = str(uuid.uuid4())[:8]
        g.current_user = None

    @login_manager.user_loader
    def load_user(user_id: str):
        if not user_id:
            return None
        try:
            user = db.session.get(User, int(user_id))
            g.current_user = user
            return user
        except SQLAlchemyError:
            app.logger.warning("Failed to load user %s from database; resetting session lookup.", user_id)
            db.session.remove()
            return None

    @app.before_request
    def _access_log():
        if app.config.get("ACCESS_LOG_ENABLED", True):
            app.logger.info("request_started method=%s", request.method)

    @app.after_request
    def _response_log(response):
        if app.config.get("ACCESS_LOG_ENABLED", True):
            app.logger.info("request_finished status=%s", response.status_code)
        return response

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": generate_csrf,
            "build_breadcrumbs": build_breadcrumbs,
            "is_safe_internal_url": is_safe_internal_url,
            "safe_back_url": safe_back_url,
            "safe_next_url": safe_next_url,
            "strategy_label": strategy_label,
            "category_label": category_label,
            "lot_scope_label": lot_scope_label,
            "lot_status_label": lot_status_label,
            "stale_reason_label": stale_reason_label,
            "fmt_num": format_number,
            "fmt_tick_range": format_tick_range,
            "display_or_dash": display_or_dash,
            "category_labels": CATEGORY_LABELS,
            "session_terms": SESSION_TERMS,
            "breadcrumbs": [],
            "back_url": safe_back_url(req=request),
            "cancel_url": None,
            "stale_warning": None,
            "ui_enable_global_search": bool(app.config.get("UI_ENABLE_GLOBAL_SEARCH", False)),
            "enable_global_chart_libs": bool(app.config.get("ENABLE_GLOBAL_CHART_LIBS", False)),
        }

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc: CSRFError):
        app.logger.warning("csrf_failed")
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "csrf_failed",
                            "message": "Сессия или CSRF-токен устарели. Обновите страницу и повторите действие.",
                            "details": {},
                        },
                    }
                ),
                403,
            )
        return exc.description, getattr(exc, "code", 400) or 400

    register_blueprints(app)
    _bootstrap_local_sqlite(app)
    init_cli(app)
    return app
