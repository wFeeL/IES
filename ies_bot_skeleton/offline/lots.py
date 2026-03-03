from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class FillReport:
    files_total: int
    files_changed: int
    files_skipped: int
    details: List[str]


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _as_int(v: Any, default: int = 1) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _normalize_item(item: Dict[str, Any], idx: int) -> Dict[str, Any]:
    kind = str(item.get("kind", "")).strip() or "unknown"
    item_id = str(item.get("id", "")).strip() or f"{kind}_{idx}"
    qty = max(1, _as_int(item.get("qty", 1), 1))
    contract = _as_float(item.get("contract_rub_per_tick", 0.0), 0.0)
    tariff = _as_float(item.get("tariff_rub_per_mw_tick", 0.0), 0.0)
    meta = item.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}

    return {
        "kind": kind,
        "id": item_id,
        "qty": qty,
        "contract_rub_per_tick": contract,
        "tariff_rub_per_mw_tick": tariff,
        "meta": meta,
    }


def _normalize_lot(data: Dict[str, Any], fallback_lot_id: str) -> Dict[str, Any]:
    lot_id = str(data.get("lot_id", "")).strip() or fallback_lot_id
    title = str(data.get("title", "")).strip() or f"Lot {lot_id}"
    note = str(data.get("note", "")).strip()
    suggested_bid = data.get("suggested_bid", None)
    items_raw = data.get("items", [])
    if not isinstance(items_raw, list):
        items_raw = []

    items: List[Dict[str, Any]] = []
    for i, item in enumerate(items_raw, start=1):
        if not isinstance(item, dict):
            continue
        items.append(_normalize_item(item, i))

    normalized = {
        "lot_id": lot_id,
        "title": title,
        "note": note,
        "items": items,
    }
    if suggested_bid is not None:
        normalized["suggested_bid"] = _as_float(suggested_bid, 0.0)
    return normalized


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
            normalized = _normalize_lot(original, fallback_lot_id=path.stem)
        except Exception as exc:
            skipped += 1
            details.append(f"SKIP {path.name}: {exc}")
            continue

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

