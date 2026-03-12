from __future__ import annotations

import os
from typing import Optional

from flask import Flask, request
from flask_wtf.csrf import generate_csrf
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
    analysis_mode_label,
    category_label,
    lot_scope_label,
    lot_status_label,
    strategy_label,
)


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
        return db.session.get(User, int(user_id))

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
            "analysis_mode_ui_label": analysis_mode_label,
            "lot_scope_label": lot_scope_label,
            "lot_status_label": lot_status_label,
            "category_labels": CATEGORY_LABELS,
            "session_terms": SESSION_TERMS,
            "breadcrumbs": [],
            "back_url": safe_back_url(req=request),
            "cancel_url": None,
            "stale_warning": None,
        }

    register_blueprints(app)

    init_cli(app)
    return app
