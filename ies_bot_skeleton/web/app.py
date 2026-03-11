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
from .routes import api_bp, pages_bp
from .services.navigation import (
    build_breadcrumbs,
    is_safe_internal_url,
    safe_back_url,
    safe_next_url,
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
            "breadcrumbs": [],
            "back_url": safe_back_url(req=request),
            "cancel_url": None,
            "stale_warning": None,
        }

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    init_cli(app)
    return app


def main() -> int:
    app = create_app()
    host = os.getenv("IES_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("IES_WEB_PORT", "5000"))
    app.run(host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
