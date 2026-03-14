from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def role_required(*roles: str):
    role_set = set(roles)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in role_set:
                abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def is_admin() -> bool:
    return bool(current_user.is_authenticated and current_user.role == "admin")
