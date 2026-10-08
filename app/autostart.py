"""Platform autostart registration and status helpers."""

from __future__ import annotations

import getpass
import os
import platform
import shlex
import subprocess
import time
from pathlib import Path

WINDOWS_TASK_NAME = "MultiCameraPrinterDashboard"
LINUX_SERVICE_NAME = "multi-camera-printer-dashboard.service"


def _systemd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def windows_task_command(root: Path) -> list[str]:
    wrapper = root / "start-hidden.vbs"
    return [
        "schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/SC", "ONLOGON",
        "/RL", "LIMITED", "/TR", f'wscript.exe "{wrapper}"', "/F",
    ]


def linux_service_text(root: Path, username: str | None = None) -> str:
    root = root.expanduser().resolve()
    user = username or os.environ.get("SUDO_USER") or getpass.getuser()
    python = root / ".venv" / "bin" / "python"
    return f"""[Unit]
Description=Multi Camera Printer Dashboard
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User={user}
WorkingDirectory={root}
Environment={_systemd_quote(f"MCPD_HOME={root}")}
ExecStart={_systemd_quote(python)} -m app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _sudo(command: list[str], non_interactive: bool) -> list[str]:
    return ["sudo", "-n", *command] if non_interactive else ["sudo", *command]


def _result_text(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()


def _linux_state() -> tuple[bool, bool, str]:
    enabled_result = subprocess.run(
        ["systemctl", "is-enabled", LINUX_SERVICE_NAME],
        capture_output=True, text=True, check=False,
    )
    active_result = subprocess.run(
        ["systemctl", "is-active", LINUX_SERVICE_NAME],
        capture_output=True, text=True, check=False,
    )
    enabled = enabled_result.returncode == 0
    active = active_result.returncode == 0
    service_state = active_result.stdout.strip() or ("active" if active else "inactive")
    return enabled, active, service_state


def _verify_linux_service() -> tuple[bool, str]:
    """Require the enabled service to remain active across several checks."""
    consecutive_active_checks = 0
    last_state = "unknown"
    for _ in range(16):
        enabled, active, last_state = _linux_state()
        if enabled and active:
            consecutive_active_checks += 1
            if consecutive_active_checks >= 3:
                return True, "systemd system service (enabled at boot and running now)"
        else:
            consecutive_active_checks = 0
        time.sleep(0.4)

    status_result = subprocess.run(
        ["systemctl", "status", LINUX_SERVICE_NAME, "--no-pager", "--full"],
        capture_output=True, text=True, check=False,
    )
    diagnostic = _result_text(status_result)
    if len(diagnostic) > 2000:
        diagnostic = diagnostic[-2000:]
    message = f"systemd service did not stay running (state: {last_state})."
    if diagnostic:
        message += f"\n{diagnostic}"
    message += (
        f"\nInspect full logs with: sudo journalctl -u {LINUX_SERVICE_NAME} -n 100 --no-pager"
    )
    return False, message


def enable_autostart(
    root: Path,
    *,
    start_now: bool = False,
    non_interactive: bool = False,
) -> tuple[bool, str]:
    system = platform.system()
    if system == "Windows":
        result = subprocess.run(windows_task_command(root), capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return True, "Windows Task Scheduler (starts at user sign-in)"
        return False, (result.stderr or result.stdout).strip()
    if system == "Linux":
        service_source = root / "multi-camera-printer-dashboard.service"
        service_target = Path("/etc/systemd/system") / LINUX_SERVICE_NAME
        try:
            # Rebuild this generated file on every enable/repair. This fixes old or
            # missing unit files after moving or upgrading an installation.
            service_source.write_text(linux_service_text(root), encoding="utf-8")
        except OSError as exc:
            return False, f"Could not write {service_source}: {exc}"
        commands = [
            _sudo(["install", "-m", "644", str(service_source), str(service_target)], non_interactive),
            _sudo(["systemctl", "daemon-reload"], non_interactive),
            _sudo(["systemctl", "enable", LINUX_SERVICE_NAME], non_interactive),
            *(
                [
                    _sudo(["systemctl", "reset-failed", LINUX_SERVICE_NAME], non_interactive),
                    _sudo(["systemctl", "restart", LINUX_SERVICE_NAME], non_interactive),
                ]
                if start_now else []
            ),
        ]
        for command in commands:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                return False, _result_text(result)
        if start_now:
            return _verify_linux_service()
        return True, "systemd system service (enabled at boot; run the repair command to start and verify it now)"
    return False, f"Automatic startup is not supported on {system}."


def disable_autostart(
    root: Path,
    remove: bool = False,
    *,
    stop_now: bool = True,
    non_interactive: bool = False,
) -> tuple[bool, str]:
    del root
    system = platform.system()
    if system == "Windows":
        command = ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.returncode == 0, (result.stderr or result.stdout).strip()
    if system == "Linux":
        command = _sudo(
            ["systemctl", "disable", *(["--now"] if stop_now else []), LINUX_SERVICE_NAME],
            non_interactive,
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and remove:
            target = Path("/etc/systemd/system") / LINUX_SERVICE_NAME
            subprocess.run(_sudo(["rm", "-f", str(target)], non_interactive), check=False)
            subprocess.run(_sudo(["systemctl", "daemon-reload"], non_interactive), check=False)
        return result.returncode == 0, (result.stderr or result.stdout).strip()
    return False, f"Automatic startup is not supported on {system}."


def autostart_info(root: Path | None = None) -> dict[str, object]:
    system = platform.system()
    if system == "Windows":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME],
            capture_output=True, text=True, check=False,
        )
        enabled = result.returncode == 0
        return {
            "enabled": enabled,
            "active": None,
            "healthy": enabled,
            "service_state": "scheduled" if enabled else "not scheduled",
            "status": "enabled (user sign-in)" if enabled else "disabled",
            "platform": system,
            "description": "Automatic starts at user sign-in using Windows Task Scheduler.",
            "supported": True,
        }
    if system == "Linux":
        enabled, active, service_state = _linux_state()
        if enabled and active:
            status = "enabled and running (system boot)"
        elif enabled:
            status = f"enabled for system boot, but service is {service_state}"
        elif active:
            status = "manual mode, but the systemd service is still running"
        else:
            status = "disabled"
        info: dict[str, object] = {
            "enabled": enabled,
            "active": active,
            "healthy": enabled and active,
            "service_state": service_state,
            "status": status,
            "platform": system,
            "description": (
                "Automatic uses a systemd service. A working setup is both enabled for boot and running now."
            ),
            "supported": True,
        }
        if root is not None and enabled and not active:
            info["command"] = autostart_terminal_command(root, True)
        return info
    return {
        "enabled": False,
        "active": None,
        "healthy": False,
        "service_state": "unsupported",
        "status": "unsupported",
        "platform": system,
        "description": f"Automatic startup is not supported on {system}.",
        "supported": False,
    }


def autostart_status() -> str:
    return str(autostart_info()["status"])


def autostart_terminal_command(root: Path, enabled: bool) -> str:
    if platform.system() == "Windows":
        command = "enable-autostart" if enabled else "disable-autostart --keep-running"
        return rf'.\.venv\Scripts\python.exe manage.py {command}'
    command = "enable-autostart" if enabled else "disable-autostart --keep-running"
    linux_root = str(root).replace("\\", "/")
    return f"cd {shlex.quote(linux_root)} && ./.venv/bin/python manage.py {command}"
