"""Data-preserving updater for installed Multi Camera Printer Dashboard copies."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__

REPOSITORY = "Sa3doonAlRa3doon/multi-camera-3D-printer-dashboard"
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/main/VERSION"
ARCHIVE_URL = f"https://github.com/{REPOSITORY}/archive/refs/heads/main.zip"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
UPDATE_PATHS = (
    "app",
    "VERSION",
    "requirements.txt",
    "manage.py",
    "setup.py",
    "setup-windows.ps1",
    "setup-linux.sh",
    "update-multi-camera-printer-dashboard.bat",
    "update-multi-camera-printer-dashboard.sh",
    "README.md",
    "SECURITY.md",
    "LICENSE",
)
PRIVATE_PATHS = ("config", "data", "logs")


class UpdateError(RuntimeError):
    pass


class NoUpdate(UpdateError):
    pass


def parse_version(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise UpdateError(f"Invalid version value: {value!r}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def fetch_remote_version(timeout: int = 15) -> str:
    request = urllib.request.Request(REMOTE_VERSION_URL, headers={"User-Agent": "MultiCameraPrinterDashboard-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            version = response.read(100).decode("utf-8").strip()
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"Could not check GitHub for updates: {exc}") from exc
    parse_version(version)
    return version


def update_available(current: str, remote: str) -> bool:
    return parse_version(remote) > parse_version(current)


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination = destination.resolve()
    total_size = 0
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            total_size += item.file_size
            if total_size > 150_000_000:
                raise UpdateError("The update archive is unexpectedly large.")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UpdateError("The update archive contains an unsupported symbolic link.")
            target = (destination / item.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise UpdateError("The update archive contains an unsafe path.") from exc
        bundle.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise UpdateError("The GitHub update archive has an unexpected layout.")
    source = roots[0]
    required = (source / "VERSION", source / "app" / "__init__.py", source / "requirements.txt")
    if not all(path.exists() for path in required):
        raise UpdateError("The update archive is missing required application files.")
    return source


def _zip_paths(root: Path, paths: tuple[str, ...], destination: Path, skip_pid: bool = False) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in paths:
            path = root / relative
            if not path.exists():
                continue
            files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
            for file in files:
                if skip_pid and file.name == "server.pid":
                    continue
                bundle.write(file, file.relative_to(root))


def _copy_payload(source: Path, root: Path) -> None:
    for relative in UPDATE_PATHS:
        incoming = source / relative
        if not incoming.exists():
            continue
        target = root / relative
        if incoming.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(incoming, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(incoming, target)
    if os.name != "nt":
        for relative in ("setup-linux.sh", "update-multi-camera-printer-dashboard.sh"):
            path = root / relative
            if path.exists():
                path.chmod(0o755)


def _restore_code(root: Path, backup: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mcv-rollback-") as temporary:
        extracted = Path(temporary)
        with zipfile.ZipFile(backup) as bundle:
            bundle.extractall(extracted)
        _copy_payload(extracted, root)


def install_from_payload(root: Path, source: Path, expected_version: str) -> tuple[Path, Path]:
    root = root.resolve()
    payload_version = (source / "VERSION").read_text(encoding="utf-8").strip()
    if payload_version != expected_version:
        raise UpdateError(
            f"The downloaded archive reports version {payload_version}, expected {expected_version}."
        )
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        backup_dir.chmod(0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    code_backup = backup_dir / f"code-before-{expected_version}-{stamp}.zip"
    data_backup = backup_dir / f"private-data-before-{expected_version}-{stamp}.zip"
    _zip_paths(root, UPDATE_PATHS, code_backup)
    _zip_paths(root, PRIVATE_PATHS, data_backup, skip_pid=True)
    try:
        _copy_payload(source, root)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
            check=True,
        )
    except Exception as exc:
        _restore_code(root, code_backup)
        raise UpdateError(f"The update failed and application code was restored: {exc}") from exc
    return code_backup, data_backup


def download_and_install(root: Path, force: bool = False) -> tuple[str, Path, Path]:
    remote = fetch_remote_version()
    if not force and not update_available(__version__, remote):
        raise NoUpdate(f"Multi Camera Printer Dashboard {__version__} is already up to date.")
    with tempfile.TemporaryDirectory(prefix="mcv-update-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "update.zip"
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "MultiCameraPrinterDashboard-Updater"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except (OSError, urllib.error.URLError) as exc:
            raise UpdateError(f"Could not download the update from GitHub: {exc}") from exc
        source = _safe_extract(archive, temporary_path / "extracted")
        code_backup, data_backup = install_from_payload(root, source, remote)
    return remote, code_backup, data_backup
