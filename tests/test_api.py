from fastapi.testclient import TestClient

from app.cameras import CameraManager
from app.config import ConfigStore, new_settings
from app.main import create_app
import app.main as main_module


def authenticated_client(tmp_path, active_port=None):
    store = ConfigStore(tmp_path)
    store.save_settings(new_settings(8080, "0.0.0.0", "admin", "password123"))
    store.save_cameras([])
    client = TestClient(create_app(store, CameraManager(), active_port=active_port))
    client.__enter__()
    response = client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    assert response.status_code == 303
    csrf = client.get("/api/session").json()["csrf_token"]
    return store, client, {"X-CSRF-Token": csrf}


def test_camera_crud_persists_and_redacts_credentials(tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        payload = {
            "name": "Gate",
            "source_type": "rtsp",
            "source": "rtsp://camera/live?token=abc",
            "username": "viewer",
            "password": "secret-password",
            "enabled": True,
            "target_width": 1280,
            "target_height": 720,
            "target_fps": 20,
            "rotation": 270,
            "flip": "horizontal",
        }
        created = client.post("/api/cameras", json=payload, headers=headers)
        assert created.status_code == 201
        camera_id = created.json()["id"]
        listing = client.get("/api/cameras").json()[0]
        assert listing["name"] == "Gate"
        assert "abc" not in listing["source"]
        assert "secret-password" not in str(listing)
        assert listing["target_width"] == 1280
        assert listing["target_height"] == 720
        assert listing["target_fps"] == 20
        assert listing["rotation"] == 270
        assert listing["flip"] == "horizontal"

        update = {**payload, "name": "Updated Gate", "source": "", "username": "", "password": ""}
        assert client.put(f"/api/cameras/{camera_id}", json=update, headers=headers).status_code == 200
        saved = store.load_cameras()[0]
        assert saved["source"] == payload["source"]
        assert saved["password"] == payload["password"]
        assert saved["target_fps"] == 20

        assert client.delete(f"/api/cameras/{camera_id}", headers=headers).status_code == 204
        assert store.load_cameras() == []
    finally:
        client.__exit__(None, None, None)


def test_api_requires_authentication_and_csrf(tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(new_settings(8080, "0.0.0.0", "admin", "password123"))
    store.save_cameras([])
    with TestClient(create_app(store, CameraManager())) as client:
        assert client.get("/api/cameras").status_code == 401
        client.post("/login", data={"username": "admin", "password": "password123"})
        payload = {"name": "Camera", "source_type": "usb", "source": "0", "enabled": True}
        assert client.post("/api/cameras", json=payload).status_code == 403


def test_camera_video_settings_are_validated(tmp_path):
    _, client, headers = authenticated_client(tmp_path)
    try:
        base = {"name": "Camera", "source_type": "usb", "source": "0", "enabled": True}
        mismatched = client.post(
            "/api/cameras",
            json={**base, "target_width": 1280, "target_height": 0},
            headers=headers,
        )
        assert mismatched.status_code == 422
        invalid_rotation = client.post(
            "/api/cameras",
            json={**base, "rotation": 45},
            headers=headers,
        )
        assert invalid_rotation.status_code == 422
        invalid_fps = client.post(
            "/api/cameras",
            json={**base, "target_fps": 61},
            headers=headers,
        )
        assert invalid_fps.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_dashboard_enforces_six_camera_limit(tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        for index in range(6):
            response = client.post(
                "/api/cameras",
                json={"name": f"Camera {index + 1}", "source_type": "usb", "source": str(index)},
                headers=headers,
            )
            assert response.status_code == 201
        rejected = client.post(
            "/api/cameras",
            json={"name": "Camera 7", "source_type": "usb", "source": "6"},
            headers=headers,
        )
        assert rejected.status_code == 409
        assert "up to 6 cameras" in rejected.json()["detail"]
        assert len(store.load_cameras()) == 6
    finally:
        client.__exit__(None, None, None)


def test_printer_dashboard_settings_are_private_authenticated_and_validated(tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        saved = client.post(
            "/api/printer",
            json={
                "name": "K2 Plus",
                "url": "http://printer.local:4408/#/",
                "api_url": "http://printer.local:7125/",
            },
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["configured"] is True
        assert store.load_settings()["printer_dashboard_url"] == "http://printer.local:4408/#/"
        assert store.load_settings()["printer_api_url"] == "http://printer.local:7125/"
        assert client.get("/api/printer").json()["name"] == "K2 Plus"

        invalid = client.post(
            "/api/printer",
            json={"name": "Printer", "url": "ftp://printer.local/control"},
            headers=headers,
        )
        assert invalid.status_code == 422
        credentials = client.post(
            "/api/printer",
            json={"name": "Printer", "url": "http://admin:secret@printer.local:4408/"},
            headers=headers,
        )
        assert credentials.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_printer_layer_status_uses_private_dashboard_configuration(monkeypatch, tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        settings = store.load_settings()
        settings["printer_dashboard_url"] = "http://printer.local:4408/#/"
        store.save_settings(settings)
        seen = []
        monkeypatch.setattr(
            main_module,
            "query_printer_status",
            lambda url, api_url: seen.append((url, api_url)) or {
                "available": True,
                "state": "printing",
                "filename": "part.gcode",
                "current_layer": 7,
                "total_layer": 50,
                "layer_supported": True,
            },
        )
        response = client.get("/api/printer/status")
        assert response.status_code == 200
        assert response.json()["current_layer"] == 7
        assert seen == [("http://printer.local:4408/#/", "")]
        assert headers
    finally:
        client.__exit__(None, None, None)


def test_autostart_can_be_changed_from_authenticated_settings(monkeypatch, tmp_path):
    current = {"enabled": False}

    def info(*_args):
        enabled = current["enabled"]
        return {
            "enabled": enabled,
            "status": "enabled (user sign-in)" if enabled else "disabled",
            "platform": "Windows",
            "description": "Automatic starts at user sign-in using Windows Task Scheduler.",
            "supported": True,
        }

    def enable(_root, *, start_now, non_interactive):
        assert start_now is False
        assert non_interactive is True
        current["enabled"] = True
        return True, "Windows Task Scheduler (starts at user sign-in)"

    monkeypatch.setattr(main_module, "autostart_info", info)
    monkeypatch.setattr(main_module, "enable_autostart", enable)
    store, client, headers = authenticated_client(tmp_path)
    try:
        initial = client.get("/api/autostart").json()
        assert initial["enabled"] is False
        changed = client.post("/api/autostart", json={"enabled": True}, headers=headers)
        assert changed.status_code == 200
        assert changed.json()["enabled"] is True
        assert changed.json()["changed"] is True
        assert store.load_settings()["autostart"] is True
    finally:
        client.__exit__(None, None, None)


def test_linux_autostart_permission_fallback_is_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main_module,
        "autostart_info",
        lambda *_args: {
            "enabled": False,
            "status": "disabled",
            "platform": "Linux",
            "description": "Automatic starts at system boot using a systemd service.",
            "supported": True,
        },
    )
    monkeypatch.setattr(
        main_module,
        "enable_autostart",
        lambda _root, *, start_now, non_interactive: (False, "sudo: a password is required"),
    )
    monkeypatch.setattr(
        main_module,
        "autostart_terminal_command",
        lambda _root, _enabled: "./.venv/bin/python manage.py enable-autostart",
    )
    _, client, headers = authenticated_client(tmp_path)
    try:
        response = client.post("/api/autostart", json={"enabled": True}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is False
        assert body["requires_admin"] is True
        assert "manage.py enable-autostart" in body["command"]
    finally:
        client.__exit__(None, None, None)


def test_network_settings_save_a_preferred_port_and_show_actual_urls(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "lan_addresses", lambda: ["192.0.2.20", "100.64.0.5"])
    monkeypatch.setattr(main_module, "tailscale_addresses", lambda: ["100.64.0.5"])
    monkeypatch.setattr(main_module, "is_port_available", lambda _host, port: port == 1010)
    store, client, headers = authenticated_client(tmp_path, active_port=8080)
    try:
        response = client.post(
            "/api/network",
            json={"mode": "custom", "port": "1010"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["active_port"] == 8080
        assert body["saved_port"] == 1010
        assert body["restart_required"] is True
        assert body["local_urls"] == ["http://127.0.0.1:8080"]
        assert body["lan_urls"] == ["http://192.0.2.20:8080"]
        assert body["tailscale_urls"] == ["http://100.64.0.5:8080"]
        assert store.load_settings()["port"] == 1010
    finally:
        client.__exit__(None, None, None)


def test_network_settings_validate_and_reject_an_occupied_custom_port(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "is_port_available", lambda _host, _port: False)
    _, client, headers = authenticated_client(tmp_path, active_port=8080)
    try:
        invalid = client.post(
            "/api/network",
            json={"mode": "custom", "port": "101"},
            headers=headers,
        )
        assert invalid.status_code == 422
        assert "4-digit port number" in invalid.json()["detail"]
        occupied = client.post(
            "/api/network",
            json={"mode": "custom", "port": "9090"},
            headers=headers,
        )
        assert occupied.status_code == 409
        assert "already occupied" in occupied.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_network_settings_can_choose_an_available_port_automatically(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "automatic_port", lambda host: 2020 if host == "0.0.0.0" else 3030)
    store, client, headers = authenticated_client(tmp_path, active_port=8080)
    try:
        response = client.post(
            "/api/network",
            json={"mode": "automatic", "port": ""},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["saved_port"] == 2020
        assert store.load_settings()["port"] == 2020
    finally:
        client.__exit__(None, None, None)


def test_admin_can_manage_accounts_and_viewer_cannot_manage_settings(tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        created = client.post(
            "/api/users",
            json={"username": "CameraViewer", "password": "viewer-pass", "role": "viewer", "enabled": True},
            headers=headers,
        )
        assert created.status_code == 201
        viewer = created.json()
        assert viewer["role"] == "viewer"
        assert "password" not in str(viewer).lower()
        assert client.get("/api/users").json()["max_users"] == 10

        client.post("/logout", headers=headers)
        assert client.post(
            "/login",
            data={"username": "cameraviewer", "password": "viewer-pass"},
            follow_redirects=False,
        ).status_code == 303
        session = client.get("/api/session").json()
        assert session["user"]["id"] == viewer["id"]
        viewer_headers = {"X-CSRF-Token": session["csrf_token"]}
        denied = client.post(
            "/api/cameras",
            json={"name": "Nope", "source_type": "usb", "source": "0"},
            headers=viewer_headers,
        )
        assert denied.status_code == 403
        assert client.get("/api/users").status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_user_can_change_own_password(tmp_path):
    _, client, headers = authenticated_client(tmp_path)
    try:
        wrong = client.post(
            "/api/account/password",
            json={"current_password": "wrong", "new_password": "replacement-pass"},
            headers=headers,
        )
        assert wrong.status_code == 403
        changed = client.post(
            "/api/account/password",
            json={"current_password": "password123", "new_password": "replacement-pass"},
            headers=headers,
        )
        assert changed.status_code == 200
        client.post("/logout", headers=headers)
        assert client.post(
            "/login",
            data={"username": "admin", "password": "password123"},
            follow_redirects=False,
        ).headers["location"].startswith("/login")
        assert client.post(
            "/login",
            data={"username": "admin", "password": "replacement-pass"},
            follow_redirects=False,
        ).headers["location"] == "/"
    finally:
        client.__exit__(None, None, None)


def test_last_enabled_admin_is_protected_and_account_limit_is_ten(tmp_path):
    store, client, headers = authenticated_client(tmp_path)
    try:
        admin = client.get("/api/session").json()["user"]
        demote = client.put(
            f"/api/users/{admin['id']}",
            json={"username": "admin", "password": "", "role": "viewer", "enabled": True},
            headers=headers,
        )
        assert demote.status_code == 409

        settings = store.load_settings()
        template = settings["users"][0]
        for index in range(2, 11):
            settings["users"].append(
                {
                    **template,
                    "id": f"user-{index}",
                    "username": f"viewer{index}",
                    "role": "viewer",
                }
            )
        store.save_settings(settings)
        rejected = client.post(
            "/api/users",
            json={"username": "eleventh", "password": "password11", "role": "viewer", "enabled": True},
            headers=headers,
        )
        assert rejected.status_code == 409
        assert "up to 10 accounts" in rejected.json()["detail"]
    finally:
        client.__exit__(None, None, None)
