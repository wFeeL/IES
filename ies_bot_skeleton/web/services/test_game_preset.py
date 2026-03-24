from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..extensions import db
from ..models import GameSession, ObjectType, Ruleset

# Compatibility-only module. Test game bootstrap is disabled in the production-only 2026 flow.
TEST_GAME_RULESET_CODE = "ies_test_game_2026_disabled"
TEST_GAME_RULESET_VERSION = "0"
TEST_GAME_RULESET_NAME = "Тестовая игра отключена"
TEST_GAME_START_PACK_CODE = "disabled"
TEST_GAME_START_PACK_NAME = "Тестовая игра отключена"
TEST_GAME_START_PACK_DESCRIPTION = "Тестовый стартовый пакет отключён в боевом режиме 2026."
TEST_GAME_DEFAULT_SESSION_TITLE = "Боевая сессия 2026"
TEST_GAME_BUNDLED_FORECAST_NAME = "Встроенный прогноз отключён"
TEST_GAME_FORECAST_SOURCE_LABEL = "Встроенный прогноз отключён"
TEST_GAME_UPLOAD_FORECAST_DEFAULT_NAME = "Боевой прогноз 2026"
TEST_GAME_OBJECT_CODES: tuple[str, ...] = ()
# Legacy import expects this mapping to exist. Keep it empty while test game is disabled.
LOT_KIND_TO_OBJECT_CODE: Dict[str, str] = {}


def is_test_game_ruleset(ruleset: Ruleset | None) -> bool:
    return False


def preferred_default_ruleset() -> Ruleset | None:
    rows = db.session.query(Ruleset).filter_by(is_active=True).order_by(Ruleset.created_at.desc(), Ruleset.id.desc()).all()
    for row in rows:
        if 'test' in str(row.code or '').lower():
            continue
        return row
    return rows[0] if rows else None


def load_lot_payloads_from_dir(path) -> List[Dict[str, Any]]:  # compatibility shim
    return []


def load_test_game_lot_payloads() -> List[Dict[str, Any]]:
    return []


def add_lot_payloads_to_session(
    *,
    session: GameSession,
    payloads: Iterable[Dict[str, Any]],
    type_map: Dict[str, ObjectType] | None = None,
) -> Dict[str, Any]:
    del session, payloads, type_map
    return {"session_id": None, "lots_created": 0, "lot_items_created": 0, "skipped": ["test_game_disabled"]}


def bootstrap_test_game_session(session: GameSession, *, commit: bool = True) -> Dict[str, Any]:
    del commit
    return {
        "session_id": int(session.id),
        "objects_created": 0,
        "lots_created": 0,
        "lot_items_created": 0,
        "skipped": ["test_game_disabled"],
    }
