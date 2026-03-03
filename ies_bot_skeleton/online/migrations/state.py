from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_v1_to_v2(payload: Dict[str, Any], season: str) -> Dict[str, Any]:
    out = dict(payload or {})
    out["schema_version"] = 2
    out["season"] = str(out.get("season") or season or "unknown")
    created = out.get("created_at")
    if not isinstance(created, str) or not created:
        created = _utcnow()
    out["created_at"] = created
    out["updated_at"] = _utcnow()
    return out


MIGRATIONS = {
    1: migrate_v1_to_v2,
}


LATEST_SCHEMA_VERSION = 2


__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "migrate_v1_to_v2"]
