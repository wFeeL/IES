from __future__ import annotations

from tests.web_helpers import create_session, login


def _type_map(client):
    rows = client.get("/api/object-types").get_json()["items"]
    return {row["code"]: int(row["id"]) for row in rows}


def test_system_page_supports_object_crud_via_ssr(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="System objects")
    wind_id = _type_map(client)["wind"]

    system_resp = client.get(f"/system/{session_id}")
    assert system_resp.status_code == 200
    system_html = system_resp.get_data(as_text=True)
    assert f"/system/{session_id}/objects/new" in system_html

    create_page = client.get(f"/system/{session_id}/objects/new")
    assert create_page.status_code == 200
    create_html = create_page.get_data(as_text=True)
    assert "Новый объект" in create_html
    assert "Генерация, МВт" in create_html

    create_resp = client.post(
        f"/system/{session_id}/objects/new",
        data={
            "session_id": str(session_id),
            "object_type_id": str(wind_id),
            "custom_name": "Ветер-1",
            "district": "north",
            "parent_instance_id": "0",
            "generation_mw": "7.5",
            "forecast_sensitivity": "0.6",
            "is_active": "y",
        },
        follow_redirects=False,
    )
    assert create_resp.status_code in (302, 303)
    assert create_resp.headers["Location"].endswith(f"/system/{session_id}")

    objects_resp = client.get(f"/api/objects?session_id={session_id}")
    assert objects_resp.status_code == 200
    items = objects_resp.get_json()["items"]
    assert len(items) == 1
    object_id = int(items[0]["id"])

    edit_page = client.get(f"/system/objects/{object_id}/edit")
    assert edit_page.status_code == 200
    edit_html = edit_page.get_data(as_text=True)
    assert "Редактирование объекта" in edit_html
    assert "Ветер-1" in edit_html

    update_resp = client.post(
        f"/system/objects/{object_id}/edit",
        data={
            "session_id": str(session_id),
            "object_type_id": str(wind_id),
            "custom_name": "Ветер-2",
            "district": "south",
            "parent_instance_id": "0",
            "generation_mw": "8.0",
            "forecast_sensitivity": "0.4",
            "is_active": "y",
        },
        follow_redirects=False,
    )
    assert update_resp.status_code in (302, 303)

    api_object = client.get(f"/api/objects?session_id={session_id}").get_json()["items"][0]
    assert api_object["custom_name"] == "Ветер-2"
    assert api_object["current_parameters"]["generation_mw"] == 8.0

    confirm = client.get(f"/system/objects/{object_id}/delete")
    assert confirm.status_code == 200
    confirm_html = confirm.get_data(as_text=True)
    assert "Удаление объекта" in confirm_html
    assert "Ветер-2" in confirm_html

    delete_resp = client.post(
        f"/system/objects/{object_id}/delete", data={}, follow_redirects=False
    )
    assert delete_resp.status_code in (302, 303)
    assert delete_resp.headers["Location"].endswith(f"/system/{session_id}")

    final_objects = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    assert final_objects == []


def test_api_object_create_validates_session_and_type(client):
    login(client, "admin", "admin123")

    missing_session = client.post(
        "/api/objects",
        json={"session_id": 999999, "object_type_id": 1, "custom_name": "bad"},
    )
    assert missing_session.status_code == 400
    assert missing_session.get_json()["error"]["message"] == "Сессия 999999 не найдена"

    session_id = create_session(client, title="Object API")
    bad_type = client.post(
        "/api/objects",
        json={"session_id": session_id, "object_type_id": 999999, "custom_name": "bad"},
    )
    assert bad_type.status_code == 400
    assert "Тип объекта 999999 не найден" in bad_type.get_json()["error"]["message"]
