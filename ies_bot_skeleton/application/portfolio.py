from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from .budget import (
    budget_snapshot,
    purchase_spent_total as _purchase_spent_total,
    allpay_spent_total as _allpay_spent_total,
    spent_total as _spent_total,
    remaining_budget as _remaining_budget,
)
from ..web.extensions import db
from ..web.models import GameSession, Lot, ObjectInstance
from ..web.services.analysis_context import resolve_analysis_context
from ..web.services.evaluation import ForecastCompatibilityError
from ..web.services.purchased_objects import mark_generated_from_lot
from ..web.services.network_readiness import network_readiness_summary
from ..web.services.stale import mark_results_stale
from ..web.services.ui_text import lot_status_label


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def purchase_spent_total(session: GameSession) -> float:
    return _purchase_spent_total(session)


def allpay_spent_total(session: GameSession) -> float:
    return _allpay_spent_total(session)


def spent_total(session: GameSession) -> float:
    return _spent_total(session)


def remaining_budget(session: GameSession) -> float:
    return _remaining_budget(session)


def _merge_item_parameters(lot_item) -> Dict[str, Any]:
    params = (
        dict(lot_item.object_type.default_parameters_json or {}) if lot_item.object_type else {}
    )
    params.update(dict(lot_item.overrides_json or {}))
    params.setdefault("qty", max(1, int(lot_item.quantity or 1)))
    return params


def _portfolio_objects_for_lot(lot: Lot) -> list[ObjectInstance]:
    rows: list[ObjectInstance] = []
    for item in lot.items:
        params = _merge_item_parameters(item)
        district = str(params.get("district") or "default")
        row = ObjectInstance(
            session_id=lot.session_id,
            object_type_id=item.object_type_id,
            custom_name=str(item.object_type.name if item.object_type else "").strip(),
            current_parameters_json=params,
            source_lot_id=lot.id,
            is_from_start_pack=False,
            district=district,
            is_active=True,
        )
        rows.append(mark_generated_from_lot(row, lot_id=int(lot.id)))
    return rows


def _mark_portfolio_changed(session: GameSession) -> None:
    mark_results_stale(list(session.evaluations), reason="portfolio_changed")


def _ensure_buy_forecast_compatible(session: GameSession) -> None:
    analysis_ctx = resolve_analysis_context(session)
    forecast_summary = dict(analysis_ctx.get("forecast_summary") or {})
    if bool(forecast_summary.get("is_compatible", True)):
        return
    report = dict(forecast_summary.get("compatibility_report") or {})
    raise ForecastCompatibilityError(report)


def buy_lot(session: GameSession, lot: Lot, purchase_price: float) -> Dict[str, Any]:
    if int(lot.session_id) != int(session.id):
        raise ValueError("Лот не принадлежит этой сессии")
    if lot.status != "available":
        raise ValueError("Купить можно только доступный лот")
    _ensure_buy_forecast_compatible(session)

    price = float(purchase_price or 0.0)
    if price <= 0.0:
        raise ValueError("Цена покупки должна быть больше нуля")

    remaining = remaining_budget(session)
    if price > remaining:
        raise ValueError("Цена покупки превышает остаток бюджета")

    lot.status = "bought"
    lot.purchase_price = price
    lot.purchased_at = _utcnow()
    db.session.add(lot)

    created = _portfolio_objects_for_lot(lot)
    for row in created:
        db.session.add(row)
    db.session.flush()

    _mark_portfolio_changed(session)
    available_lots_count = sum(
        1 for row in session.lots if str(row.status or "") == "available"
    )

    return {
        "lot_id": int(lot.id),
        "status": lot.status,
        "purchase_price": float(price),
        "created_objects": len(created),
        "generated_object_ids": [int(row.id or 0) for row in created],
        **budget_snapshot(session),
        "available_lots_count": int(available_lots_count),
        "refresh_required": True,
        "refresh_reason": "portfolio_changed",
    }


def undo_lot_purchase(session: GameSession, lot: Lot) -> Dict[str, Any]:
    if int(lot.session_id) != int(session.id):
        raise ValueError("Лот не принадлежит этой сессии")
    if lot.status != "bought":
        raise ValueError("Отменить можно только купленный лот")

    removed_objects = 0
    for row in list(lot.generated_objects):
        db.session.delete(row)
        removed_objects += 1

    lot.status = "available"
    lot.purchase_price = None
    lot.purchased_at = None
    db.session.add(lot)

    _mark_portfolio_changed(session)
    available_lots_count = sum(
        1 for row in session.lots if str(row.status or "") == "available"
    )

    return {
        "lot_id": int(lot.id),
        "status": lot.status,
        "removed_objects": removed_objects,
        **budget_snapshot(session),
        "available_lots_count": int(available_lots_count),
        "refresh_required": True,
        "refresh_reason": "portfolio_changed",
    }


def reject_lot(lot: Lot) -> Dict[str, Any]:
    if lot.status != "available":
        raise ValueError("Отклонить можно только доступный лот")
    lot.status = "rejected"
    db.session.add(lot)
    return {"lot_id": int(lot.id), "status": lot.status}


def restore_lot(lot: Lot) -> Dict[str, Any]:
    if lot.status != "rejected":
        raise ValueError("Вернуть можно только отклонённый лот")
    lot.status = "available"
    db.session.add(lot)
    return {"lot_id": int(lot.id), "status": lot.status}


def portfolio_rows(
    session: GameSession,
    analytics_by_lot: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[Dict[str, Any]]:
    analytics_by_lot = analytics_by_lot or {}
    rows: list[Dict[str, Any]] = []
    for lot in session.lots:
        if lot.status != "bought":
            continue
        analytics = analytics_by_lot.get(int(lot.id), {})
        financial = dict(analytics.get("financial_breakdown") or {})
        result = dict(financial.get("result") or {})
        losses = dict(financial.get("losses_and_risks") or {})
        rows.append(
            {
                "lot_id": int(lot.id),
                "name": lot.name,
                "purchase_price": float(lot.purchase_price or 0.0),
                "expected_net_profit": float(result.get("net_profit", 0.0) or 0.0),
                "net_profit": float(result.get("net_profit", 0.0) or 0.0),
                "utility": float(analytics.get("summary_score", 0.0) or 0.0),
                "risk": float(losses.get("risk_total", 0.0) or 0.0),
                "status": lot.status,
                "status_label": lot_status_label(lot.status),
                "purchased_at": lot.purchased_at,
            }
        )
    rows.sort(key=lambda row: row["purchase_price"], reverse=True)
    return rows


def portfolio_summary(
    session: GameSession,
    analytics_by_lot: Mapping[int, Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    rows = portfolio_rows(session, analytics_by_lot=analytics_by_lot)
    budget = budget_snapshot(session)
    expected_income = 0.0
    expected_expenses = 0.0
    expected_net_profit = 0.0
    aggregate_risk = 0.0
    for row in rows:
        analytics = dict((analytics_by_lot or {}).get(int(row["lot_id"]), {}) or {})
        financial = dict(analytics.get("financial_breakdown") or {})
        income = dict(financial.get("income") or {})
        expenses = dict(financial.get("expenses") or {})
        losses = dict(financial.get("losses_and_risks") or {})
        result = dict(financial.get("result") or {})
        expected_income += float(income.get("total", 0.0) or 0.0)
        expected_expenses += float(expenses.get("total", 0.0) or 0.0)
        expected_net_profit += float(result.get("net_profit", 0.0) or 0.0)
        aggregate_risk += float(losses.get("risk_total", 0.0) or 0.0)

    if aggregate_risk >= 60.0:
        risk_profile = "Высокий"
    elif aggregate_risk >= 20.0:
        risk_profile = "Умеренный"
    else:
        risk_profile = "Низкий"
    readiness = network_readiness_summary(session)
    warning_message = str(readiness.get("message") or "") if readiness.get("action_required") else ""

    return {
        "analysis_mode": "unified",
        **budget,
        "bought_lots_count": len(rows),
        "owned_objects_count": sum(
            max(1, int(obj.current_parameters_json.get("qty", 1) or 1))
            for obj in session.objects
            if obj.is_active
        ),
        "expected_income": float(expected_income),
        "expected_expenses": float(expected_expenses),
        "expected_net_profit": float(expected_net_profit),
        "aggregate_income_total": float(expected_income),
        "aggregate_expenses_total": float(expected_expenses),
        "aggregate_net_profit": float(expected_net_profit),
        "aggregate_risk": float(aggregate_risk),
        "risk_profile": risk_profile,
        "risk_profile_label": risk_profile,
        "network_action_required": bool(readiness.get("action_required")),
        "unconnected_purchased_objects_count": int(
            readiness.get("unconnected_purchased_objects_count", 0) or 0
        ),
        "network_readiness_message": warning_message,
    }
