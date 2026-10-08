import zipfile

import pytest

from app import updater


def test_semantic_update_comparison():
    assert updater.update_available("1.0.0", "1.1.0")
    assert updater.update_available("1.9.9", "2.0.0")
    assert not updater.update_available("1.1.0", "1.1.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("latest")


def test_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "unsafe")
    with pytest.raises(updater.UpdateError, match="unsafe path"):
        updater._safe_extract(archive, tmp_path / "extract")


def test_update_replaces_code_but_preserves_private_data(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    source = tmp_path / "payload"
    (root / "app").mkdir(parents=True)
    (root / "app" / "old.py").write_text("old", encoding="utf-8")
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "settings.json").write_text("private-settings", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "cameras.json").write_text("private-cameras", encoding="utf-8")
    (root / "data" / "server.pid").write_text("123", encoding="ascii")
    (root / "logs").mkdir()
    (root / "logs" / "viewer.log").write_text("private-log", encoding="utf-8")

    (source / "app").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text("NEW = True\n", encoding="utf-8")
    (source / "VERSION").write_text("1.1.0\n", encoding="utf-8")
    (source / "requirements.txt").write_text("", encoding="utf-8")
    (source / "manage.py").write_text("# updated\n", encoding="utf-8")
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: None)

    code_backup, data_backup = updater.install_from_payload(root, source, "1.1.0")

    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.1.0"
    assert (root / "app" / "__init__.py").exists()
    assert not (root / "app" / "old.py").exists()
    assert (root / "config" / "settings.json").read_text(encoding="utf-8") == "private-settings"
    assert (root / "data" / "cameras.json").read_text(encoding="utf-8") == "private-cameras"
    assert (root / "logs" / "viewer.log").read_text(encoding="utf-8") == "private-log"
    assert code_backup.exists() and data_backup.exists()
    with zipfile.ZipFile(data_backup) as bundle:
        names = set(bundle.namelist())
    assert "config/settings.json" in names
    assert "data/cameras.json" in names
    assert "logs/viewer.log" in names
    assert "data/server.pid" not in names
