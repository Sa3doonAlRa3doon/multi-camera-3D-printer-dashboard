import json

from app.config import ConfigStore, find_user, new_settings, verify_password


def test_settings_and_cameras_survive_a_new_store_instance(tmp_path):
    store = ConfigStore(tmp_path)
    settings = new_settings(8080, "0.0.0.0", "admin", "strong-password")
    cameras = [{"id": "one", "name": "Door", "source_type": "rtsp", "source": "rtsp://host/live?token=secret", "username": "cam", "password": "private", "enabled": True}]
    store.save_settings(settings)
    store.save_cameras(cameras)

    restored = ConfigStore(tmp_path)
    assert restored.load_settings()["port"] == 8080
    assert restored.load_cameras() == cameras
    admin = find_user(settings, username="ADMIN")
    assert admin is not None
    assert verify_password("strong-password", admin["password_salt"], admin["password_hash"])
    assert not verify_password("wrong", admin["password_salt"], admin["password_hash"])
    assert settings["printer_dashboard_url"] == ""
    assert settings["printer_dashboard_name"] == "3D Printer"
    assert settings["printer_api_url"] == ""


def test_legacy_single_login_is_migrated_without_changing_password(tmp_path):
    store = ConfigStore(tmp_path)
    current = new_settings(8080, "0.0.0.0", "admin", "legacy-pass")
    admin = current["users"][0]
    legacy = {
        **{key: value for key, value in current.items() if key != "users"},
        "version": 1,
        "username": admin["username"],
        "password_salt": admin["password_salt"],
        "password_hash": admin["password_hash"],
    }
    store.save_settings(legacy)

    migrated = store.load_settings()
    user = migrated["users"][0]
    assert user["username"] == "admin"
    assert user["role"] == "admin"
    assert verify_password("legacy-pass", user["password_salt"], user["password_hash"])
    assert "password_hash" not in {key: value for key, value in migrated.items() if key != "users"}


def test_public_camera_redacts_credentials_and_query_values(tmp_path):
    camera = {"id": "one", "name": "Door", "source_type": "rtsp", "source": "rtsp://user:pass@host:554/live?token=secret", "username": "u", "password": "p", "enabled": True}
    public = ConfigStore(tmp_path).public_camera(camera)
    encoded = json.dumps(public)
    assert "user:pass" not in encoded
    assert '"password": "p"' not in encoded
    assert "secret" not in encoded
    assert public["source"] == "rtsp://host:554/live?token=REDACTED"
    assert public["has_password"] is True
    assert public["target_width"] == 0
    assert public["target_height"] == 0
    assert public["target_fps"] == 0
    assert public["rotation"] == 0
    assert public["flip"] == "none"
