from .shared import api_bp, pages_bp

# Import route modules for blueprint registration side effects.
from . import api_admin, api_analysis, pages_admin, pages_analysis, pages_core  # noqa: F401

__all__ = ["api_bp", "pages_bp"]
