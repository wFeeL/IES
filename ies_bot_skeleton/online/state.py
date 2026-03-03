from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from .logging_utils import log_event
from .migrations import LATEST_SCHEMA_VERSION, MIGRATIONS

STATE_FILE = "state.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SolarLearn:
    best_angle: Dict[str, Dict[str, int]]
    best_power: Dict[str, Dict[str, float]]


@dataclass
class CalibState:
    wind_k: Dict[str, float]
    solar: SolarLearn
    last_solar_angle: Dict[str, int]
    printed_once: bool
    schema_version: int = LATEST_SCHEMA_VERSION
    season: str = "unknown"
    created_at: str = ""
    updated_at: str = ""


def default_state(season: str = "unknown") -> CalibState:
    now = _utcnow()
    return CalibState(
        wind_k={},
        solar=SolarLearn(best_angle={}, best_power={}),
        last_solar_angle={},
        printed_once=False,
        schema_version=LATEST_SCHEMA_VERSION,
        season=str(season or "unknown"),
        created_at=now,
        updated_at=now,
    )


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _apply_migrations(payload: Dict[str, Any], season: str, logger: Any) -> Dict[str, Any]:
    d = dict(payload)
    version = int(d.get("schema_version", 1) or 1)
    while version < LATEST_SCHEMA_VERSION:
        migrate = MIGRATIONS.get(version)
        if migrate is None:
            break
        old_version = version
        d = migrate(d, season)
        version = int(d.get("schema_version", old_version + 1))
        log_event(
            logger,
            "info",
            "STATE_MIGRATED",
            from_version=old_version,
            to_version=version,
            season=season,
        )
    return d


def _to_state(d: Dict[str, Any], default_season: str) -> CalibState:
    solar = d.get("solar", {}) or {}
    created = str(d.get("created_at", "") or "")
    updated = str(d.get("updated_at", "") or "")
    now = _utcnow()
    if not created:
        created = now
    if not updated:
        updated = now
    return CalibState(
        wind_k=d.get("wind_k", {}) or {},
        solar=SolarLearn(
            best_angle=solar.get("best_angle", {}) or {},
            best_power=solar.get("best_power", {}) or {},
        ),
        last_solar_angle=d.get("last_solar_angle", {}) or {},
        printed_once=bool(d.get("printed_once", False)),
        schema_version=int(d.get("schema_version", LATEST_SCHEMA_VERSION)),
        season=str(d.get("season", default_season) or default_season),
        created_at=created,
        updated_at=updated,
    )


def load_state(season: str = "unknown", logger: Any = None) -> CalibState:
    if not os.path.exists(STATE_FILE):
        return default_state(season=season)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        if not isinstance(raw, dict):
            return default_state(season=season)
        migrated = _apply_migrations(raw, season=season, logger=logger)
        state = _to_state(migrated, default_season=season)
        if migrated != raw:
            _atomic_write_json(STATE_FILE, asdict(state))
        return state
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger,
            "warning",
            "STATE_LOAD_FAILED",
            error_message=str(exc),
            state_file=STATE_FILE,
        )
        return default_state(season=season)


def save_state(st: CalibState) -> None:
    now = _utcnow()
    if not st.created_at:
        st.created_at = now
    st.updated_at = now
    st.schema_version = LATEST_SCHEMA_VERSION
    if not st.season:
        st.season = "unknown"
    _atomic_write_json(STATE_FILE, asdict(st))
