from __future__ import annotations

from flask import Blueprint


pages_bp = Blueprint("pages", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")
