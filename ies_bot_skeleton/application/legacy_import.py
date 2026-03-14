from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..web.services.legacy_import import import_legacy_data


def run_legacy_import(
    *,
    session_id: int,
    state_path: Path | None = None,
    lots_dir: Path | None = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"session_id": session_id}
    if state_path is not None:
        kwargs["state_path"] = state_path
    if lots_dir is not None:
        kwargs["lots_dir"] = lots_dir
    return import_legacy_data(**kwargs)
