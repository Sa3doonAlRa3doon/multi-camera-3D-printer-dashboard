"""Manage a completed Multi Camera Printer Dashboard installation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from app.autostart import (
    LINUX_SERVICE_NAME,
    autostart_info,
    autostart_status,
    disable_autostart,
    enable_autostart,
)
from app.config import ConfigStore, application_home
from app.updater import NoUpdate, UpdateError, download_and_install, fetch_remote_version, update_available
from app import __version__


def update_autostart(root: Path, enabled: bool, kind: str) -> None:
    store = ConfigStore(root)
    settings = store.load_settings()
    settings["autostart"] = enabled
    settings["autostart_kind"] = kind
    store.save_settings(settings)


def _result_text(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()


def _linux_service_is_running() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", LINUX_SERVICE_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() in {"active", "activating", "deactivating", "reloading"}


def _stop_linux_service() -> tuple[bool, str]:
    """Stop systemd cleanly, then force the service down if streams block shutdown."""
    command = ["sudo", "systemctl", "stop", LINUX_SERVICE_NAME]
    timed_out = False
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )
        if result.returncode != 0:
            return False, _result_text(result) or "systemctl stop failed"
    except subprocess.TimeoutExpired:
        timed_out = True

    if timed_out or _linux_service_is_running():
        # A connected MJPEG/browser stream can keep an older Uvicorn process in
        # graceful shutdown. The unit already has a stop job, so killing its
        # remaining processes here does not trigger Restart=on-failure.
        subprocess.run(
            [
                "sudo",
                "systemctl",
                "kill",
                "--kill-who=all",
                "--signal=SIGKILL",
                LINUX_SERVICE_NAME,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["sudo", "systemctl", "stop", "--no-block", LINUX_SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
        )

    for _ in range(20):
        if not _linux_service_is_running():
            return True, (
                "Multi Camera Printer Dashboard systemd service stopped"
                + (" after its graceful-shutdown timeout." if timed_out else ".")
            )
        time.sleep(0.25)
    return False, (
        "The systemd service is still active. Run: "
        f"sudo systemctl status {LINUX_SERVICE_NAME} --no-pager --full"
    )


def stop_server(root: Path) -> int:
    pid_path = root / "data" / "server.pid"
    try:
        process = None
        if pid_path.exists():
            pid = int(pid_path.read_text(encoding="ascii").strip())
            process = psutil.Process(pid)
            command = " ".join(process.cmdline()).lower()
            if "app" not in command and "multi-camera-printer-dashboard" not in command:
                raise RuntimeError("PID file does not refer to Multi Camera Printer Dashboard; refusing to stop it.")

        startup = autostart_info(root)
        service_state = str(startup.get("service_state", ""))
        if startup.get("platform") == "Linux" and service_state in {
            "active", "activating", "deactivating", "reloading",
        }:
            ok, detail = _stop_linux_service()
            if not ok:
                raise RuntimeError(detail)
            pid_path.unlink(missing_ok=True)
            print(detail)
            return 0

        if process is None:
            print("Multi Camera Printer Dashboard is not running (no PID file found).")
            return 0

        process.terminate()
        try:
            process.wait(timeout=8)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            print("Multi Camera Printer Dashboard required a forced stop after its graceful-shutdown timeout.")
            pid_path.unlink(missing_ok=True)
            return 0
        pid_path.unlink(missing_ok=True)
        print("Multi Camera Printer Dashboard stopped.")
        return 0
    except psutil.NoSuchProcess:
        pid_path.unlink(missing_ok=True)
        print("Removed a stale PID file; the server was not running.")
        return 0
    except Exception as exc:
        print(f"Could not stop Multi Camera Printer Dashboard: {exc}", file=sys.stderr)
        return 1


def server_is_running(root: Path) -> bool:
    pid_path = root / "data" / "server.pid"
    if not pid_path.exists():
        return False
    try:
        return psutil.pid_exists(int(pid_path.read_text(encoding="ascii").strip()))
    except (OSError, ValueError):
        return False


def start_background(root: Path) -> None:
    environment = os.environ.copy()
    environment["MCPD_HOME"] = str(root)
    log_path = root / "logs" / "update-restart.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, "-m", "app"],
        cwd=root,
        env=environment,
        stdout=output,
        stderr=output,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Multi Camera Printer Dashboard")
    parser.add_argument("command", choices=["start", "stop", "status", "check-update", "update", "enable-autostart", "disable-autostart", "remove-autostart"])
    parser.add_argument("--force", action="store_true", help="Reinstall the current remote version")
    parser.add_argument("--keep-running", action="store_true", help="Change future autostart without stopping the current viewer")
    args = parser.parse_args()
    root = application_home()
    if args.command == "start":
        from app.__main__ import main as run
        return run()
    if args.command == "stop":
        return stop_server(root)
    if args.command == "status":
        print(f"Version: {__version__}")
        print(f"Autostart: {autostart_status()}")
        pid_path = root / "data" / "server.pid"
        print(f"Server PID: {pid_path.read_text(encoding='ascii').strip() if pid_path.exists() else 'not running'}")
        return 0
    if args.command == "check-update":
        try:
            remote = fetch_remote_version()
            if update_available(__version__, remote):
                print(f"Update available: {__version__} -> {remote}")
                return 0
            print(f"Multi Camera Printer Dashboard {__version__} is up to date.")
            return 0
        except NoUpdate as exc:
            print(exc)
            return 0
        except UpdateError as exc:
            print(exc, file=sys.stderr)
            return 1
    if args.command == "update":
        startup_before_update = autostart_info(root)
        linux_autostart = startup_before_update["platform"] == "Linux" and bool(startup_before_update["enabled"])
        was_running = server_is_running(root) or bool(startup_before_update.get("active"))
        if was_running and stop_server(root) != 0:
            return 1
        try:
            version, code_backup, data_backup = download_and_install(root, force=args.force)
            print(f"Updated successfully to Multi Camera Printer Dashboard {version}.")
            print(f"Application backup: {code_backup}")
            print(f"Private data backup: {data_backup}")
            print("Camera settings, credentials, port, logs, and recordings were not replaced.")
            if linux_autostart:
                ok, detail = enable_autostart(root, start_now=True)
                print(detail)
                if ok:
                    update_autostart(root, True, detail)
                    print("The upgraded viewer is running under systemd and remains enabled for boot.")
                elif was_running:
                    start_background(root)
                    print("Systemd repair failed, so the viewer was restored manually. Run manage.py enable-autostart again.")
            elif was_running:
                start_background(root)
                print("The viewer was restarted in the background.")
            return 0
        except NoUpdate as exc:
            print(exc)
            if linux_autostart:
                ok, detail = enable_autostart(root, start_now=True)
                print(detail)
                if ok:
                    update_autostart(root, True, detail)
                elif was_running:
                    start_background(root)
                    print("Systemd repair failed, so the viewer was restored manually.")
            elif was_running:
                start_background(root)
                print("The viewer was restarted in the background.")
            return 0
        except UpdateError as exc:
            print(f"Update failed: {exc}", file=sys.stderr)
            if linux_autostart:
                ok, detail = enable_autostart(root, start_now=True)
                print(detail)
                if ok:
                    update_autostart(root, True, detail)
                elif was_running:
                    start_background(root)
                    print("The existing viewer was restored manually because systemd repair also failed.")
            elif was_running:
                start_background(root)
                print("The existing viewer was restarted.")
            return 1
    if args.command == "enable-autostart":
        # On Linux this command is also the repair path shown in Settings. Stop
        # a manually launched copy first so systemd can bind the saved port,
        # then start and verify the service instead of merely enabling a unit.
        was_running = server_is_running(root)
        stopped_for_repair = os.name != "nt" and was_running
        if stopped_for_repair and stop_server(root) != 0:
            return 1
        ok, detail = enable_autostart(root, start_now=os.name != "nt")
        if ok:
            update_autostart(root, True, detail)
        elif stopped_for_repair:
            start_background(root)
            detail += "\nAutomatic startup was not repaired; the viewer was restored in manual mode."
        print(detail)
        return 0 if ok else 1
    remove = args.command == "remove-autostart"
    ok, detail = disable_autostart(root, remove=remove, stop_now=not args.keep_running)
    if ok:
        update_autostart(root, False, "none")
    print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
