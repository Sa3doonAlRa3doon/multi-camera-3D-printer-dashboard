import socket
from pathlib import Path

from app.__main__ import bind_listener
from app.autostart import (
    autostart_info,
    autostart_terminal_command,
    disable_autostart,
    enable_autostart,
    linux_service_text,
    windows_task_command,
)
from app.config import ConfigStore, new_settings


def test_windows_autostart_is_hidden_and_runs_at_user_sign_in(tmp_path):
    command = windows_task_command(tmp_path)
    joined = " ".join(command)
    assert "ONLOGON" in command
    assert "wscript.exe" in joined
    assert "start-hidden.vbs" in joined


def test_linux_autostart_is_boot_system_service_with_restart(tmp_path):
    text = linux_service_text(tmp_path, username="camera-user")
    assert "User=camera-user" in text
    assert "WantedBy=multi-user.target" in text
    assert "Restart=on-failure" in text
    assert "StartLimitIntervalSec=0" in text
    assert "After=network-online.target" in text
    assert f"WorkingDirectory={tmp_path.resolve()}" in text
    assert 'WorkingDirectory="' not in text
    assert 'ExecStart="' in text


def test_linux_working_directory_is_not_quoted_for_systemd(tmp_path):
    root = tmp_path / "Multi Camera Printer Dashboard"
    root.mkdir()
    text = linux_service_text(root, username="camera-user")
    assert f"WorkingDirectory={root.resolve()}" in text
    assert f'WorkingDirectory="{root.resolve()}"' not in text


def test_web_linux_autostart_change_is_noninteractive_and_does_not_start_duplicate(monkeypatch, tmp_path):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("app.autostart.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.autostart.subprocess.run", lambda command, **kwargs: calls.append(command) or Result())
    ok, _ = enable_autostart(tmp_path, start_now=False, non_interactive=True)
    assert ok is True
    assert len(calls) == 3
    assert all(command[:2] == ["sudo", "-n"] for command in calls)
    assert "enable" in calls[-1]
    assert "--now" not in calls[-1]
    assert (tmp_path / "multi-camera-printer-dashboard.service").is_file()


def test_linux_start_now_is_enabled_started_and_verified(monkeypatch, tmp_path):
    calls = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def run(command, **_kwargs):
        calls.append(command)
        if command[:2] == ["systemctl", "is-enabled"]:
            return Result(stdout="enabled\n")
        if command[:2] == ["systemctl", "is-active"]:
            return Result(stdout="active\n")
        return Result()

    monkeypatch.setattr("app.autostart.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.autostart.subprocess.run", run)
    monkeypatch.setattr("app.autostart.time.sleep", lambda _seconds: None)
    ok, detail = enable_autostart(tmp_path, start_now=True)
    assert ok is True
    assert "running now" in detail
    assert not any("--now" in command for command in calls)
    service = "multi-camera-printer-dashboard.service"
    assert any(command[-2:] == ["restart", service] for command in calls)
    enable_index = next(index for index, command in enumerate(calls) if command[-2:] == ["enable", service])
    restart_index = next(index for index, command in enumerate(calls) if command[-2:] == ["restart", service])
    assert enable_index < restart_index
    assert "ExecStart=" in (tmp_path / "multi-camera-printer-dashboard.service").read_text(encoding="utf-8")


def test_linux_status_exposes_enabled_but_failed_service_and_repair_command(monkeypatch, tmp_path):
    class Result:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def run(command, **_kwargs):
        if command[:2] == ["systemctl", "is-enabled"]:
            return Result(0, "enabled\n")
        if command[:2] == ["systemctl", "is-active"]:
            return Result(3, "failed\n")
        raise AssertionError(command)

    monkeypatch.setattr("app.autostart.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.autostart.subprocess.run", run)
    info = autostart_info(tmp_path)
    assert info["enabled"] is True
    assert info["active"] is False
    assert info["healthy"] is False
    assert info["service_state"] == "failed"
    assert "manage.py enable-autostart" in str(info["command"])


def test_web_manual_mode_keeps_current_linux_viewer_running(monkeypatch, tmp_path):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("app.autostart.platform.system", lambda: "Linux")
    monkeypatch.setattr("app.autostart.subprocess.run", lambda command, **kwargs: calls.append(command) or Result())
    ok, _ = disable_autostart(tmp_path, stop_now=False, non_interactive=True)
    assert ok is True
    assert calls == [["sudo", "-n", "systemctl", "disable", "multi-camera-printer-dashboard.service"]]


def test_linux_admin_fallback_uses_the_installed_virtual_environment(monkeypatch):
    monkeypatch.setattr("app.autostart.platform.system", lambda: "Linux")
    command = autostart_terminal_command(Path("/home/pi5/Documents/Multi Camera Printer Dashboard"), True)
    assert command == "cd '/home/pi5/Documents/Multi Camera Printer Dashboard' && ./.venv/bin/python manage.py enable-autostart"


def test_saved_occupied_port_is_replaced_and_persisted(tmp_path):
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    store = ConfigStore(tmp_path)
    settings = new_settings(port, "127.0.0.1", "admin", "password123")
    store.save_settings(settings)
    listener = None
    try:
        listener, selected, fallback = bind_listener(store, settings)
        assert fallback is True
        assert selected != port
        assert store.load_settings()["port"] == selected
    finally:
        occupied.close()
        if listener:
            listener.close()
