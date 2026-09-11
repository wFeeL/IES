from __future__ import annotations

import io
from typing import Any, Dict

# Russian column headers the 2026 forecast parser maps onto the canonical
# factors and load profiles. Together they cover every object type that
# build_forecast_compatibility_report() can demand.
FORECAST_HEADER = "Ветер,Солнце,Больницы,Заводы,Офисы,Дома А,Дома Б"


def login(client, username: str, password: str) -> None:
    resp = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "remember": "y",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def create_session(
    client, *, title: str = "Test Session", selected_strategy: str = "balanced"
) -> int:
    ruleset_id = ruleset_id_by_code(client, "ies_2026")
    resp = client.post(
        "/api/sessions",
        json={
            "title": title,
            "ruleset_id": ruleset_id,
            "selected_strategy": selected_strategy,
            "budget_total": 5000,
        },
    )
    assert resp.status_code == 200
    payload: Dict[str, Any] = resp.get_json()
    assert payload["ok"] is True
    return int(payload["item"]["id"])


def ruleset_id_by_code(client, code: str) -> int:
    resp = client.get("/api/rulesets")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    return int(next(row["id"] for row in items if row["code"] == code))


def full_coverage_forecast_csv(ticks: int = 48) -> bytes:
    """Forecast CSV that covers every object type of the 2026 ruleset."""
    rows = [FORECAST_HEADER]
    for tick in range(ticks):
        rows.append(
            ",".join(
                (
                    f"{8.0 + (tick % 7) * 0.35}",
                    f"{0.0 if tick < 12 else min(14.5, (tick - 12) * 0.7)}",
                    f"{4.5 + (tick % 5) * 0.1}",
                    f"{5.2 + (tick % 6) * 0.15}",
                    f"{3.6 + (tick % 8) * 0.2}",
                    f"{2.1 + (tick % 4) * 0.25}",
                    f"{2.8 + (tick % 3) * 0.3}",
                )
            )
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


def upload_forecast(
    client, session_id: int, *, name: str = "Test forecast", ticks: int = 48
) -> int:
    """Give the session a real forecast and return its id.

    The bundled forecast is switched off on purpose (see
    forecast_service.load_bundled_forecast_pack), so evaluation, strategy and
    purchase all answer 409 until a session owns an uploaded one.
    """
    resp = client.post(
        "/api/forecast/upload",
        data={
            "session_id": str(session_id),
            "name": name,
            "file": (io.BytesIO(full_coverage_forecast_csv(ticks)), "forecast.csv"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return int(resp.get_json()["item"]["id"])
