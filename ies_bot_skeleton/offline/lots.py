from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

KNOWN_LOT_FIELDS = {"lot_id", "title", "note", "items", "suggested_bid"}
KNOWN_ITEM_FIELDS = {
    "kind",
    "type",  # accepted alias, normalized into "kind"
    "id",
    "qty",
    "contract_rub_per_tick",
    "tariff_rub_per_mw_tick",
    "meta",
}


@dataclass
class FillReport:
    files_total: int
    files_changed: int
    files_skipped: int
    details: List[str]


def _as_finite_float(v: Any, *, field_name: str, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        out = float(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: expected number, got {v!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"{field_name}: number must be finite, got {v!r}")
    return out


def _as_positive_int(v: Any, *, field_name: str) -> int:
    try:
        out = int(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: expected integer, got {v!r}") from exc
    if out < 1:
        raise ValueError(f"{field_name}: must be >= 1, got {out!r}")
    return out


def _normalize_item(item: Dict[str, Any], idx: int) -> Tuple[Dict[str, Any], List[str]]:
    kind_raw = item.get("kind", item.get("type"))
    if kind_raw is None or not str(kind_raw).strip():
        raise ValueError(f"item[{idx}]: missing required field 'type' (or 'kind')")
    kind = str(kind_raw).strip()

    item_id = str(item.get("id", "")).strip()
    if not item_id:
        raise ValueError(f"item[{idx}]: missing required field 'id'")

    if "qty" not in item:
        raise ValueError(f"item[{idx}]: missing required field 'qty'")
    qty = _as_positive_int(item.get("qty"), field_name=f"item[{idx}].qty")

    contract = _as_finite_float(
        item.get("contract_rub_per_tick", 0.0),
        field_name=f"item[{idx}].contract_rub_per_tick",
        default=0.0,
    )
    tariff = _as_finite_float(
        item.get("tariff_rub_per_mw_tick", 0.0),
        field_name=f"item[{idx}].tariff_rub_per_mw_tick",
        default=0.0,
    )
    meta = item.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError(f"item[{idx}].meta: expected object, got {type(meta).__name__}")

    warnings: List[str] = []
    for field in sorted(item.keys()):
        if field not in KNOWN_ITEM_FIELDS:
            warnings.append(f"item[{idx}]: unknown field '{field}' kept as-is")

    normalized = {
        "kind": kind,
        "id": item_id,
        "qty": qty,
        "contract_rub_per_tick": contract,
        "tariff_rub_per_mw_tick": tariff,
        "meta": meta,
    }
    for field in sorted(item.keys()):
        if field not in KNOWN_ITEM_FIELDS:
            normalized[field] = item[field]
    return normalized, warnings


def _normalize_lot(data: Dict[str, Any], fallback_lot_id: str) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    lot_id = str(data.get("lot_id", "")).strip() or fallback_lot_id
    title = str(data.get("title", "")).strip() or f"Lot {lot_id}"
    note = str(data.get("note", "")).strip()
    suggested_bid = data.get("suggested_bid", None)
    items_raw = data.get("items")
    if items_raw is None:
        items_raw = []
    if not isinstance(items_raw, list):
        raise ValueError("items: expected array")

    items: List[Dict[str, Any]] = []
    for i, item in enumerate(items_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item[{i}]: expected object, got {type(item).__name__}")
        normalized, item_warnings = _normalize_item(item, i)
        items.append(normalized)
        warnings.extend(item_warnings)

    normalized = {
        "lot_id": lot_id,
        "title": title,
        "note": note,
        "items": items,
    }
    if suggested_bid is not None:
        normalized["suggested_bid"] = _as_finite_float(
            suggested_bid,
            field_name="suggested_bid",
            default=0.0,
        )

    for field in sorted(data.keys()):
        if field not in KNOWN_LOT_FIELDS:
            warnings.append(f"lot: unknown field '{field}' kept as-is")
            normalized[field] = data[field]

    return normalized, warnings


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("lot JSON must be an object")
    return payload


def _dump_json(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fill_lots(lots_dir: str, dry_run: bool = False) -> FillReport:
    base = Path(lots_dir)
    paths = sorted([p for p in base.glob("*.json") if p.is_file()])
    details: List[str] = []
    changed = 0
    skipped = 0

    for path in paths:
        try:
            original = _load_json(path)
            normalized, warnings = _normalize_lot(original, fallback_lot_id=path.stem)
        except Exception as exc:
            skipped += 1
            details.append(f"SKIP {path.name}: {exc}")
            continue

        for warning in warnings:
            details.append(f"WARN {path.name}: {warning}")

        if original == normalized:
            details.append(f"OK   {path.name}: unchanged")
            continue

        changed += 1
        details.append(f"FIX  {path.name}: normalized fields")
        if not dry_run:
            _dump_json(path, normalized)

    return FillReport(
        files_total=len(paths),
        files_changed=changed,
        files_skipped=skipped,
        details=details,
    )


def summarize_report(report: FillReport) -> Tuple[str, List[str]]:
    summary = (
        f"lots: total={report.files_total}, changed={report.files_changed}, "
        f"skipped={report.files_skipped}"
    )
    return summary, report.details
