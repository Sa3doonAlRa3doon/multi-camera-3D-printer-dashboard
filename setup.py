"""Interactive, cross-platform installer for Multi Camera Printer Dashboard."""

from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path
from urllib.parse import urlsplit

from app import __version__
from app.autostart import disable_autostart, enable_autostart, linux_service_text
from app.config import ConfigStore, new_settings
from app.network import lan_addresses, tailscale_addresses
from app.ports import select_port_interactive

SOURCE_ROOT = Path(__file__).resolve().parent
APP_NAME = "MultiCameraPrinterDashboard"


def _expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _windows_documents() -> Path | None:
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "Personal")
        candidate = _expanded_path(str(raw))
        if candidate.is_dir():
            return candidate
    except (ImportError, OSError):
        pass
    return None


def _linux_documents(home: Path) -> Path | None:
    config = home / ".config" / "user-dirs.dirs"
    if config.is_file():
        match = re.search(r'^XDG_DOCUMENTS_DIR="(.+)"$', config.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            value = match.group(1).replace("$HOME", str(home))
            candidate = _expanded_path(value)
            if candidate.is_dir():
                return candidate
    executable = shutil.which("xdg-user-dir")
    if executable:
        result = subprocess.run([executable, "DOCUMENTS"], capture_output=True, text=True, check=False)
        candidate = _expanded_path(result.stdout.strip()) if result.stdout.strip() else None
        if candidate and candidate.is_dir():
            return candidate
    return None


def _bounded_documents_search(roots: list[Path], max_depth: int = 3) -> Path | None:
    seen: set[Path] = set()
    queue: list[tuple[Path, int]] = [(root, 0) for root in roots if root.exists()]
    while queue:
        current, depth = queue.pop(0)
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if current.is_dir() and current.name.casefold() == "documents":
            return current
        if depth >= max_depth or current.name.startswith("."):
            continue
        try:
            queue.extend((child, depth + 1) for child in current.iterdir() if child.is_dir())
        except (OSError, PermissionError):
            continue
    return None


def detect_documents() -> Path | None:
    home = Path.home()
    direct = _windows_documents() if os.name == "nt" else _linux_documents(home)
    if direct:
        return direct
    candidates = [home / "Documents"]
    for key in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        if os.environ.get(key):
            candidates.append(_expanded_path(os.environ[key]) / "Documents")
    candidates.extend(path / "Documents" for path in home.glob("OneDrive*"))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    roots = [home, *[path for path in home.glob("OneDrive*") if path.is_dir()]]
    return _bounded_documents_search(roots)


def choose_install_folder() -> Path:
    documents = detect_documents()
    if documents:
        default = documents / APP_NAME
        answer = input(f"Where should Multi Camera Printer Dashboard save the application and its data?\nPress Enter for [{default}] or enter a custom folder:\n> ").strip()
        return _expanded_path(answer) if answer else default.resolve()
    print("A Documents folder could not be detected in the usual or redirected locations.")
    while True:
        answer = input("Enter the full folder where the application and its data should be saved:\n> ").strip()
        if answer:
            return _expanded_path(answer)
        print("A location is required; nothing has been saved elsewhere.")


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = input(prompt).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter Y or N.")


def ask_credentials() -> tuple[str, str]:
    username = input("Administrator username [admin]: ").strip() or "admin"
    while True:
        password = getpass.getpass("Administrator password (at least 8 characters): ")
        if len(password) < 8:
            print("Use at least 8 characters.")
            continue
        if password != getpass.getpass("Confirm administrator password: "):
            print("Passwords did not match.")
            continue
        return username, password


def ask_printer_dashboard() -> tuple[str, str]:
    print("You can embed one local 3D-printer dashboard beside the camera view.")
    print("Leave this blank to configure it later in Settings. Do not put credentials in the URL.")
    while True:
        value = input("3D-printer dashboard URL (example http://printer-ip:4408/#/):\n> ").strip()
        if not value:
            return "3D Printer", ""
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
            parsed = None
        if valid and parsed and not (parsed.username or parsed.password):
            name = input("Printer name [3D Printer]: ").strip() or "3D Printer"
            return name[:80], value
        print("Enter a complete HTTP or HTTPS URL without a username or password, or leave it blank.")


def copy_application(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if target.resolve() != SOURCE_ROOT.resolve():
        shutil.copytree(SOURCE_ROOT / "app", target / "app", dirs_exist_ok=True)
        for filename in (
            "VERSION", "requirements.txt", "manage.py", "setup.py", "setup-windows.ps1", "setup-linux.sh",
            "update-multi-camera-printer-dashboard.bat", "update-multi-camera-printer-dashboard.sh",
            "README.md", "SECURITY.md", "LICENSE",
        ):
            shutil.copy2(SOURCE_ROOT / filename, target / filename)
    (target / "config").mkdir(exist_ok=True)
    (target / "data").mkdir(exist_ok=True)
    (target / "logs").mkdir(exist_ok=True)


def create_environment(target: Path) -> Path:
    venv_dir = target / ".venv"
    if not (venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")).exists():
        print("Creating the private Python environment...")
        venv.EnvBuilder(with_pip=True, system_site_packages=platform.system() == "Linux").create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(target / "requirements.txt")], check=True)
    cv_check = subprocess.run([str(python), "-c", "import cv2"], capture_output=True, check=False)
    if cv_check.returncode != 0:
        print("Installing the OpenCV camera backend...")
        subprocess.run([str(python), "-m", "pip", "install", "opencv-python-headless>=4.8,<5"], check=True)
    return python


def write_launchers(target: Path, python: Path) -> None:
    if os.name == "nt":
        foreground = f'@echo off\r\nset "MCPD_HOME={target}"\r\n"{python}" -m app\r\n'
        hidden_cmd = f'@echo off\r\nset "MCPD_HOME={target}"\r\n"{python}" -m app >> "{target / "logs" / "autostart.log"}" 2>&1\r\n'
        vbs = f'Set shell = CreateObject("WScript.Shell")\r\nshell.Run Chr(34) & "{target / "start-hidden.cmd"}" & Chr(34), 0, False\r\n'
        (target / "start-multi-camera-printer-dashboard.bat").write_text(foreground, encoding="utf-8")
        (target / "start-hidden.cmd").write_text(hidden_cmd, encoding="utf-8")
        (target / "start-hidden.vbs").write_text(vbs, encoding="utf-8")
    else:
        launcher = f'#!/usr/bin/env bash\nset -euo pipefail\nexport MCPD_HOME="{target}"\nexec "{python}" -m app\n'
        path = target / "start-multi-camera-printer-dashboard.sh"
        path.write_text(launcher, encoding="utf-8")
        path.chmod(0o755)
        updater = target / "update-multi-camera-printer-dashboard.sh"
        if updater.exists():
            updater.chmod(0o755)
        service = target / "multi-camera-printer-dashboard.service"
        service.write_text(linux_service_text(target), encoding="utf-8")


def protect_private_storage(target: Path) -> None:
    if os.name == "nt":
        account = subprocess.run(["whoami"], capture_output=True, text=True, check=False).stdout.strip()
        if account:
            for folder in (target / "config", target / "data", target / "logs"):
                subprocess.run(
                    ["icacls", str(folder), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
                    capture_output=True, check=False,
                )
    else:
        for folder in (target / "config", target / "data", target / "logs"):
            folder.chmod(0o700)


def access_summary(port: int) -> list[str]:
    tailscale = tailscale_addresses()
    lan = [address for address in lan_addresses() if address not in tailscale]
    lines = [f"Local access URL: http://127.0.0.1:{port}"]
    lines.extend(f"LAN access URL: http://{address}:{port}" for address in lan)
    if not lan:
        lines.append("LAN access URL: no active LAN address detected")
    lines.extend(f"Tailscale access URL: http://{address}:{port}" for address in tailscale)
    if not tailscale:
        lines.append(
            "Tailscale access URL: none detected. Install and connect Tailscale on this computer and the viewing device, then restart the viewer."
        )
    return lines


def main() -> int:
    print(f"Multi Camera Printer Dashboard v{__version__} setup")
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 2
    target = choose_install_folder()
    print(f"Application files, private configuration, and logs will be kept in: {target}")
    print("Choose Y for background startup, or N to use the manual launcher whenever you want the viewer.")
    auto = ask_yes_no("Start Multi Camera Printer Dashboard automatically when this computer starts? [Y/N] ")
    print("Automatic startup selected." if auto else "Manual startup selected; no background autostart will be registered.")
    copy_application(target)
    store = ConfigStore(target)
    if store.settings_path.exists():
        settings = store.load_settings()
        settings.setdefault("printer_dashboard_name", "3D Printer")
        settings.setdefault("printer_dashboard_url", "")
        store.save_settings(settings)
        print("Existing private settings were found and will be preserved.")
    else:
        port = select_port_interactive("0.0.0.0")
        username, password = ask_credentials()
        printer_name, printer_url = ask_printer_dashboard()
        settings = new_settings(port, "0.0.0.0", username, password)
        settings["printer_dashboard_name"] = printer_name
        settings["printer_dashboard_url"] = printer_url
        store.save_settings(settings)
        store.save_cameras([])
    python = create_environment(target)
    write_launchers(target, python)
    protect_private_storage(target)
    if auto:
        ok, detail = enable_autostart(target, start_now=True)
        settings["autostart"] = ok
        settings["autostart_kind"] = detail if ok else "registration failed"
        store.save_settings(settings)
        if not ok:
            print(f"Automatic startup could not be registered: {detail}", file=sys.stderr)
            print("Use the manual launcher shown below, then see README.md for repair steps.")
        else:
            print(f"Automatic startup configured: {detail}")
            if os.name == "nt":
                subprocess.Popen(
                    ["wscript.exe", str(target / "start-hidden.vbs")],
                    cwd=target,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(1)
                print("Multi Camera Printer Dashboard was also started now in the background.")
    else:
        if settings.get("autostart"):
            ok, detail = disable_autostart(target, remove=True)
            print("Previous autostart registration removed." if ok else f"Could not remove previous autostart registration: {detail}")
        settings["autostart"] = False
        settings["autostart_kind"] = "none"
        store.save_settings(settings)
    manual = target / ("start-multi-camera-printer-dashboard.bat" if os.name == "nt" else "start-multi-camera-printer-dashboard.sh")
    print("\nSetup complete.")
    print(f"Manual launcher: {manual}")
    print(f"Stop a foreground launch with Ctrl+C, or run: {python} {target / 'manage.py'} stop")
    print(f"Private configuration: {target / 'config'} and {target / 'data'}")
    print(f"Logs: {target / 'logs'}")
    print("\nAccess information:")
    for line in access_summary(int(store.load_settings()["port"])):
        print(line)
    if not auto:
        print("These URLs become active after you run the manual launcher.")
    print("Tailscale must already be installed and connected on both devices. No IP address is hard-coded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
