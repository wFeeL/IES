from __future__ import annotations

import os
from typing import Optional

from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError, generate_csrf
from sqlalchemy import inspect
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

    required_tables = {"users", "rulesets", "object_types", "start_pack_templates"}
    with app.app_context():
        try:
            existing_tables = set(inspect(db.engine).get_table_names())
        except SQLAlchemyError:
            app.logger.warning("Could not inspect database schema during startup bootstrap.")
            db.session.remove()
            return

        if required_tables.issubset(existing_tables):
            return

        app.logger.warning(
            "Database is missing core tables (%s). Bootstrapping local SQLite schema.",
            ", ".join(sorted(required_tables - existing_tables)),
        )
        db.create_all()
        from .services.seed import ensure_seed_data

        ensure_seed_data()
        db.session.remove()


def create_app(config_name: Optional[str] = None) -> Flask:
    env_name = config_name or os.getenv("IES_WEB_ENV", "development")
    cfg = CONFIG_MAP.get(env_name, CONFIG_MAP["development"])

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(cfg)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        if not user_id:
            return None
        try:
            return db.session.get(User, int(user_id))
        except SQLAlchemyError:
            app.logger.warning(
                "Failed to load user %s from database; resetting session lookup.", user_id
            )
            db.session.remove()
            return None

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
            "category_labels": CATEGORY_LABELS,
            "session_terms": SESSION_TERMS,
            "breadcrumbs": [],
            "back_url": safe_back_url(req=request),
            "cancel_url": None,
            "stale_warning": None,
        }

    @app.errorhandler(CSRFError)
    def handle_csrf_error(exc: CSRFError):
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "csrf_failed",
                            "message": (
                                "Сессия или CSRF-токен устарели. "
                                "Обновите страницу и повторите действие."
                            ),
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
