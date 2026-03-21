from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict, Tuple

from ..extensions import db
from ..models import (
    AuctionEvent,
    EvaluationResult,
    Forecast,
    ForecastPeriod,
    GameSession,
    Lot,
    LotItem,
    ObjectInstance,
    ObjectType,
    Ruleset,
    StartPackTemplate,
)
from .purchased_objects import refresh_integration_state


def export_session_payload(session: GameSession) -> Dict[str, Any]:
    ruleset_payload = session.ruleset.to_dict() if session.ruleset else None
    start_pack_payload = None
    if session.ruleset and session.ruleset.active_start_pack_template is not None:
        start_pack_payload = session.ruleset.active_start_pack_template.to_dict(include_items=True)
    return {
        "schema_version": 6,
        "session": session.to_dict(),
        "ruleset": ruleset_payload,
        "ruleset_start_pack_template": start_pack_payload,
        "objects": [obj.to_dict() for obj in session.objects],
        "lots": [lot.to_dict() for lot in session.lots],
        "forecasts": [
            {
                **fc.to_dict(),
                "periods": [p.to_dict() for p in fc.periods],
            }
            for fc in session.forecasts
        ],
        "evaluations": [ev.to_dict() for ev in session.evaluations],
        "auction_events": [event.to_dict() for event in session.auction_events],
    }


def export_evaluations_csv(session: GameSession) -> str:
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(
        [
            "evaluation_id",
            "session_id",
            "lot_id",
            "mode",
            "scenario",
            "summary_score",
            "recommended_bid_soft",
            "recommended_bid_hard",
            "confidence",
            "explanation",
        ]
    )
    for ev in session.evaluations:
        writer.writerow(
            [
                ev.id,
                ev.session_id,
                ev.lot_id,
                ev.mode,
                ev.scenario,
                ev.summary_score,
                ev.recommended_bid_soft,
                ev.recommended_bid_hard,
                ev.confidence,
                ev.explanation,
            ]
        )
    return sio.getvalue()


def _resolve_ruleset(payload: Dict[str, Any]) -> Ruleset:
    ruleset_payload = payload.get("ruleset") or {}
    session_payload = payload.get("session") or {}

    rid = session_payload.get("ruleset_id")
    if rid:
        row = db.session.get(Ruleset, int(rid))
        if row is not None:
            return row

    code = ruleset_payload.get("code")
    version = ruleset_payload.get("version")
    if code and version:
        row = (
            db.session.query(Ruleset).filter_by(code=str(code), version=str(version)).one_or_none()
        )
        if row is not None:
            return row
        start_pack_template_id = ruleset_payload.get("active_start_pack_template_id")
        if start_pack_template_id is not None:
            try:
                template = db.session.get(StartPackTemplate, int(start_pack_template_id))
                start_pack_template_id = template.id if template is not None else None
            except Exception:
                start_pack_template_id = None
        row = Ruleset(
            code=str(code),
            version=str(version),
            name=str(ruleset_payload.get("name") or f"{code}:{version}"),
            config_json=dict(ruleset_payload.get("config_json") or {}),
            model_settings_json=dict(ruleset_payload.get("model_settings") or {}),
            active_start_pack_template_id=start_pack_template_id,
            is_builtin=bool(ruleset_payload.get("is_builtin", False)),
            is_active=False,
        )
        db.session.add(row)
        db.session.flush()
        return row

    fallback = db.session.query(Ruleset).filter_by(is_active=True).first()
    if fallback is not None:
        return fallback

    raise ValueError("Нет доступного ruleset для импорта")


def _object_type_lookup() -> Tuple[Dict[int, ObjectType], Dict[str, ObjectType]]:
    rows = db.session.query(ObjectType).all()
    by_id = {row.id: row for row in rows}
    by_code = {row.code: row for row in rows}
    return by_id, by_code


def _parse_iso_datetime(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def import_session_payload(payload: Dict[str, Any]) -> GameSession:
    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект")
    schema_version = int(payload.get("schema_version", 4) or 4)
    if schema_version not in {4, 5, 6}:
        raise ValueError(f"Неподдерживаемая версия schema_version={schema_version}")

    session_payload = payload.get("session") or {}
    title = str(session_payload.get("title", "Imported Session")).strip() or "Imported Session"

    ruleset = _resolve_ruleset(payload)
    out_session = GameSession(
        title=title,
        ruleset_id=ruleset.id,
        selected_strategy="unified",
        selected_forecast_id=None,
        budget_total=float(
            session_payload.get("start_budget", session_payload.get("budget_total", 9999.0)) or 9999.0
        ),
        allpay_spent=float(session_payload.get("allpay_spent", 0.0) or 0.0),
    )
    db.session.add(out_session)
    db.session.flush()

    type_by_id, type_by_code = _object_type_lookup()
    old_to_new_object_id: Dict[int, int] = {}
    pending_object_source_lot_links: list[tuple[int, int]] = []

    for row in payload.get("objects", []) or []:
        src_type_id = row.get("object_type_id")
        src_code = row.get("object_type_code")
        type_row = None
        if src_type_id:
            type_row = type_by_id.get(int(src_type_id))
        if type_row is None and src_code:
            type_row = type_by_code.get(str(src_code))
        if type_row is None:
            continue

        item = ObjectInstance(
            session_id=out_session.id,
            object_type_id=type_row.id,
            custom_name=str(row.get("custom_name", "")),
            current_parameters_json=dict(row.get("current_parameters") or {}),
            source_lot_id=None,
            is_from_start_pack=bool(row.get("is_from_start_pack", False)),
            district=str(row.get("district", "default")),
            is_active=bool(row.get("is_active", True)),
        )
        refresh_integration_state(item)
        db.session.add(item)
        db.session.flush()
        if row.get("id"):
            old_to_new_object_id[int(row["id"])] = item.id
        old_source_lot_id = row.get("source_lot_id")
        if old_source_lot_id not in (None, "", 0, "0"):
            pending_object_source_lot_links.append((int(item.id), int(old_source_lot_id)))

    for row in payload.get("objects", []) or []:
        old_id = row.get("id")
        old_parent = row.get("parent_instance_id")
        if not old_id or not old_parent:
            continue
        new_obj = db.session.get(ObjectInstance, old_to_new_object_id.get(int(old_id)))
        new_parent_id = old_to_new_object_id.get(int(old_parent))
        if new_obj is None or new_parent_id is None:
            continue
        new_obj.parent_instance_id = new_parent_id
        db.session.add(new_obj)

    old_to_new_lot_id: Dict[int, int] = {}
    for row in payload.get("lots", []) or []:
        purchased_at = _parse_iso_datetime(row.get("purchased_at"))
        lot = Lot(
            session_id=out_session.id,
            name=str(row.get("name", "Imported lot")),
            scope=str(row.get("scope", "normal")),
            status=str(row.get("status", "available")),
            base_bid=float(row.get("base_bid", 0.0) or 0.0),
            current_bid=float(row.get("current_bid", 0.0) or 0.0),
            purchase_price=(
                float(row.get("purchase_price", 0.0) or 0.0)
                if row.get("purchase_price") not in (None, "")
                else None
            ),
            purchased_at=purchased_at,
            note=str(row.get("note", "")),
            available_round=int(row.get("available_round", 1) or 1),
        )
        db.session.add(lot)
        db.session.flush()
        if row.get("id"):
            old_to_new_lot_id[int(row["id"])] = lot.id

        for it in row.get("items", []) or []:
            src_code = it.get("object_type_code")
            type_row = type_by_code.get(str(src_code)) if src_code else None
            if type_row is None:
                src_id = it.get("object_type_id")
                if src_id:
                    type_row = type_by_id.get(int(src_id))
            if type_row is None:
                continue

            item = LotItem(
                lot_id=lot.id,
                object_type_id=type_row.id,
                quantity=max(1, int(it.get("quantity", 1) or 1)),
                overrides_json=dict(it.get("overrides") or {}),
            )
            db.session.add(item)

    for new_object_id, old_source_lot_id in pending_object_source_lot_links:
        new_object = db.session.get(ObjectInstance, int(new_object_id))
        if new_object is None:
            continue
        new_source_lot_id = old_to_new_lot_id.get(int(old_source_lot_id))
        if new_source_lot_id is None:
            continue
        new_object.source_lot_id = int(new_source_lot_id)
        refresh_integration_state(new_object)
        db.session.add(new_object)

    imported_forecasts_by_old_id: Dict[int, int] = {}
    for fc in payload.get("forecasts", []) or []:
        normalization_map = dict(fc.get("normalization_map") or {})
        compatibility_report = dict(fc.get("compatibility_report") or {})
        is_compatible = bool(fc.get("is_compatible", True))
        incompatibility_reason = str(fc.get("incompatibility_reason", ""))
        forecast = Forecast(
            session_id=out_session.id,
            name=str(fc.get("name", "Imported forecast")),
            source_file=str(fc.get("source_file", "")),
            column_map_json=dict(fc.get("column_map") or {}),
            normalization_map_json=normalization_map,
            metadata_json=dict(fc.get("metadata") or {}),
            compatibility_report_json=compatibility_report,
            is_compatible=is_compatible,
            incompatibility_reason=incompatibility_reason,
        )
        db.session.add(forecast)
        db.session.flush()
        if fc.get("id"):
            imported_forecasts_by_old_id[int(fc["id"])] = forecast.id

        for period in fc.get("periods", []) or []:
            db.session.add(
                ForecastPeriod(
                    forecast_id=forecast.id,
                    tick=int(period.get("tick", 0) or 0),
                    illumination=period.get("illumination"),
                    wind=period.get("wind"),
                    market_price=period.get("market_price"),
                    consumption_json=dict(period.get("consumption") or {}),
                    factors_json=dict(period.get("factors") or {}),
                    profiles_json=dict(period.get("profiles") or {}),
                    extra_json=dict(period.get("extra") or {}),
                )
            )

    old_selected_forecast_id = session_payload.get("selected_forecast_id")
    if old_selected_forecast_id:
        out_session.selected_forecast_id = imported_forecasts_by_old_id.get(
            int(old_selected_forecast_id)
        )

    for ev in payload.get("evaluations", []) or []:
        src_lot_id = ev.get("lot_id")
        new_lot_id = old_to_new_lot_id.get(int(src_lot_id)) if src_lot_id else None
        if new_lot_id is None:
            continue
        db.session.add(
            EvaluationResult(
                session_id=out_session.id,
                lot_id=new_lot_id,
                mode=str(ev.get("mode", "forecast")),
                scenario=str(ev.get("scenario", "base")),
                summary_score=float(ev.get("summary_score", 0.0) or 0.0),
                metrics_json=dict(ev.get("metrics") or {}),
                explanation=str(ev.get("explanation", "")),
                recommended_bid_soft=float(ev.get("recommended_bid_soft", 0.0) or 0.0),
                recommended_bid_hard=float(ev.get("recommended_bid_hard", 0.0) or 0.0),
                confidence=float(ev.get("confidence", 0.0) or 0.0),
                is_stale=bool(ev.get("is_stale", False)),
                stale_reason=str(ev.get("stale_reason", "")),
            )
        )

    for row in payload.get("auction_events", []) or []:
        src_lot_id = row.get("lot_id")
        new_lot_id = old_to_new_lot_id.get(int(src_lot_id)) if src_lot_id else None
        if new_lot_id is None:
            continue
        db.session.add(
            AuctionEvent(
                session_id=out_session.id,
                lot_id=int(new_lot_id),
                action=str(row.get("action", "watch")),
                bid_level=str(row.get("bid_level", "")),
                amount=float(row.get("amount", 0.0) or 0.0),
                outcome=str(row.get("outcome", "none")),
                budget_effect=float(row.get("budget_effect", 0.0) or 0.0),
                details_json=dict(row.get("details") or {}),
                resolved_at=_parse_iso_datetime(row.get("resolved_at")),
                created_at=_parse_iso_datetime(row.get("created_at")) or datetime.utcnow(),
            )
        )

    db.session.commit()
    return out_session
