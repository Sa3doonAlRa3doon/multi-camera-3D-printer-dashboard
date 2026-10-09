import subprocess

import manage


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_systemd_stop_completes_cleanly(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:2] == ["systemctl", "is-active"]:
            return Result(3, "inactive\n")
        return Result()

    monkeypatch.setattr(manage.subprocess, "run", run)
    ok, detail = manage._stop_linux_service()

    assert ok is True
    assert "service stopped" in detail
    assert calls[0] == ["sudo", "systemctl", "stop", manage.LINUX_SERVICE_NAME]
    assert not any("kill" in command for command in calls)


def test_systemd_stop_force_kills_after_graceful_timeout(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:2] == ["sudo", "systemctl"] and "stop" in command and "--no-block" not in command:
            raise subprocess.TimeoutExpired(command, 12)
        if command[:2] == ["systemctl", "is-active"]:
            return Result(3, "inactive\n")
        return Result()

    monkeypatch.setattr(manage.subprocess, "run", run)
    ok, detail = manage._stop_linux_service()

    assert ok is True
    assert "graceful-shutdown timeout" in detail
    assert any("kill" in command and "--signal=SIGKILL" in command for command in calls)
    assert any("stop" in command and "--no-block" in command for command in calls)


def test_stop_server_uses_systemd_and_removes_pid(monkeypatch, tmp_path):
    pid_path = tmp_path / "data" / "server.pid"
    pid_path.parent.mkdir()
    pid_path.write_text("1113", encoding="ascii")

    class Process:
        def cmdline(self):
            return ["python", "-m", "app"]

    monkeypatch.setattr(manage.psutil, "Process", lambda _pid: Process())
    monkeypatch.setattr(
        manage,
        "autostart_info",
        lambda _root: {"platform": "Linux", "service_state": "active"},
    )
    monkeypatch.setattr(manage, "_stop_linux_service", lambda: (True, "service stopped"))

    assert manage.stop_server(tmp_path) == 0
    assert not pid_path.exists()


def test_manual_stop_force_kills_a_hung_process(monkeypatch, tmp_path):
    pid_path = tmp_path / "data" / "server.pid"
    pid_path.parent.mkdir()
    pid_path.write_text("1113", encoding="ascii")
    events = []

    class Process:
        def cmdline(self):
            return ["python", "-m", "app"]

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(("wait", timeout))
            if timeout == 8:
                raise manage.psutil.TimeoutExpired(timeout)

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(manage.psutil, "Process", lambda _pid: Process())
    monkeypatch.setattr(
        manage,
        "autostart_info",
        lambda _root: {"platform": "Linux", "service_state": "inactive"},
    )

    assert manage.stop_server(tmp_path) == 0
    assert events == ["terminate", ("wait", 8), "kill", ("wait", 5)]
    assert not pid_path.exists()
