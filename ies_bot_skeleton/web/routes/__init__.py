from __future__ import annotations

import importlib

from flask import Flask

from .shared import api_bp, pages_bp

ROUTE_MODULES = (
    "ies_bot_skeleton.web.routes.pages_core",
    "ies_bot_skeleton.web.routes.pages_analysis",
    "ies_bot_skeleton.web.routes.pages_admin",
    "ies_bot_skeleton.web.routes.api_analysis",
    "ies_bot_skeleton.web.routes.api_admin",
)


def register_blueprints(app: Flask) -> None:
    for module in ROUTE_MODULES:
        importlib.import_module(module)
    app.register_blueprint(pages_bp.materialize())
    app.register_blueprint(api_bp.materialize())


__all__ = ["register_blueprints"]
