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


def test_object_parent_must_be_substation_or_infrastructure(client):
    login(client, "admin", "admin123")
    session_id = create_session(client, title="Parent type validation")
    type_map = _type_map(client)

    start_pack = client.post(f"/api/sessions/{session_id}/add-start-pack", json={})
    assert start_pack.status_code == 200

    objects = client.get(f"/api/objects?session_id={session_id}").get_json()["items"]
    main_parent = next(
        row
        for row in objects
        if str(row.get("object_type_code") or "")
        in {"main_substation", "main", "main_substation_hq", "mini_substation_a", "mini_substation_b", "mini"}
    )

    consumer = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": type_map["house"],
            "custom_name": "Consumer parent",
            "parent_instance_id": int(main_parent["id"]),
            "current_parameters": {"expected_consumption_mw": 1.0},
        },
    )
    assert consumer.status_code == 200
    consumer_id = int(consumer.get_json()["item"]["id"])

    invalid_child = client.post(
        "/api/objects",
        json={
            "session_id": session_id,
            "object_type_id": type_map["wind"],
            "custom_name": "Wind with invalid parent",
            "parent_instance_id": consumer_id,
            "current_parameters": {"generation_mw": 2.0},
        },
    )
    assert invalid_child.status_code == 400
    assert "Родителем может быть только подстанция" in invalid_child.get_json()["error"]["message"]
