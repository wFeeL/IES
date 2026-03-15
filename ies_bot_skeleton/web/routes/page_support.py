from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

from flask import url_for

from ..models import GameSession, Lot
from ...application.portfolio import portfolio_summary
from ...application.context import (
    resolve_session_analysis_context,
    session_analysis_settings_for_session,
)
from ..services.navigation import build_breadcrumbs, safe_back_url
from ..services.stale import stale_summary_for_session


def parse_json(raw: str, *, field_name: str, default: Any) -> Any:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Некорректный JSON в поле {field_name}: {exc}") from exc


def breadcrumbs(
    items: Iterable[Tuple[str, str | None, Dict[str, Any] | None]],
) -> List[Dict[str, Any]]:
    return build_breadcrumbs(items)


def session_stale_ctx(session: GameSession) -> Dict[str, Any]:
    return {"stale_warning": stale_summary_for_session(session.id)}


def nav(
    *,
    breadcrumb_items: Iterable[Tuple[str, str | None, Dict[str, Any] | None]],
    fallback_endpoint: str,
    fallback_values: Dict[str, Any] | None = None,
    cancel_url: str | None = None,
) -> Dict[str, Any]:
    return {
        "breadcrumbs": breadcrumbs(breadcrumb_items),
        "back_url": safe_back_url(
            fallback_endpoint=fallback_endpoint,
            **(fallback_values or {}),
        ),
        "cancel_url": cancel_url,
    }


def session_analysis_view(session: GameSession) -> Dict[str, Any]:
    settings = session_analysis_settings_for_session(session)
    analysis_ctx = resolve_session_analysis_context(session)
    return {
        "analysis_settings": settings,
        "forecast_summary": analysis_ctx["forecast_summary"],
        "forecast_context": analysis_ctx["forecast_context"],
        "analysis_source": analysis_ctx["source"],
        "analysis_source_label": analysis_ctx["source_label"],
    }


def session_shell_view(
    session: GameSession,
    *,
    analytics_by_lot: Dict[int, Any] | None = None,
) -> Dict[str, Any]:
    return {
        **session_analysis_view(session),
        "portfolio": portfolio_summary(session, analytics_by_lot=analytics_by_lot),
    }


def forecast_compatibility_guidance(
    report: Dict[str, Any] | None,
    *,
    session: GameSession | None = None,
    lot: Lot | None = None,
) -> Dict[str, Any]:
    compatibility_report = dict(report or {})
    missing_types = sorted(set(compatibility_report.get("missing_types") or []))
    partial_types = sorted(set(compatibility_report.get("partial_types") or []))
    problem_types = sorted(set([*missing_types, *partial_types]))

    lot_problem_types: list[str] = []
    if lot is not None:
        lot_types = sorted(
            {
                str(item.object_type.code or "")
                for item in lot.items
                if item.object_type is not None and item.object_type.code
            }
        )
        lot_problem_types = [code for code in problem_types if code in lot_types]

    actions: list[str] = []
    if problem_types:
        actions.append(
            "Добавьте в активный прогноз покрытие для типов объектов: "
            + ", ".join(problem_types)
            + "."
        )
    else:
        actions.append("Исправьте активный прогноз: ему не хватает обязательных рядов.")

    if lot is not None:
        if lot_problem_types:
            actions.append(
                "Либо удалите из этого лота объекты типов: " + ", ".join(lot_problem_types) + "."
            )
        else:
            actions.append("Либо удалите из этого лота объект, который не покрывается прогнозом.")
    else:
        actions.append("Либо удалите несовместимые объекты из проблемных лотов.")

    primary_message = (
        "Прогноз не покрывает все типы объектов, поэтому оценка и стратегия заблокированы."
    )
    if problem_types:
        primary_message = "Прогноз не покрывает типы объектов: " + ", ".join(problem_types) + "."

    return {
        "primary_message": primary_message,
        "actions": actions,
        "problem_types": problem_types,
        "lot_problem_types": lot_problem_types,
        "forecast_url": (
            url_for("pages.forecast_page", session_id=session.id) if session is not None else None
        ),
        "lot_edit_url": (
            url_for("pages.lot_edit_page", lot_id=lot.id) if lot is not None else None
        ),
        "lots_url": (
            url_for("pages.lots_page", session_id=session.id) if session is not None else None
        ),
    }


def admin_links() -> List[Dict[str, str]]:
    return [
        {"label": "Наборы правил", "url": url_for("pages.settings_model_page")},
        {"label": "Стартовые пакеты", "url": url_for("pages.settings_start_packs_page")},
        {"label": "Типы объектов", "url": url_for("pages.settings_object_types_page")},
    ]
