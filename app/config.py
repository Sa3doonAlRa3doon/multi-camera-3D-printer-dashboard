"""Private settings and camera configuration persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PBKDF2_ITERATIONS = 600_000


def application_home() -> Path:
    configured = os.environ.get("MCPD_HOME")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent.parent


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    _, actual = hash_password(password, salt_hex)
    return hmac.compare_digest(actual, expected_hex)


def _protect_file(path: Path) -> None:
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _protect_file(temporary)
    os.replace(temporary, path)
    _protect_file(path)


def redact_network_source(source: str) -> str:
    try:
        parts = urlsplit(source)
        hostname = parts.hostname or ""
        if parts.port:
            hostname = f"{hostname}:{parts.port}"
        query = urlencode([(key, "REDACTED") for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, hostname, parts.path, query, ""))
    except (TypeError, ValueError):
        return "Configured (hidden)"


class ConfigStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or application_home()).resolve()
        self.settings_path = self.root / "config" / "settings.json"
        self.cameras_path = self.root / "data" / "cameras.json"
        self._lock = threading.RLock()

    def load_settings(self) -> dict[str, Any]:
        with self._lock:
            if not self.settings_path.exists():
                raise FileNotFoundError(
                    f"Missing {self.settings_path}. Run the setup script before launching Multi Camera Printer Dashboard."
                )
            return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def save_settings(self, settings: dict[str, Any]) -> None:
        with self._lock:
            _atomic_json(self.settings_path, settings)

    def load_cameras(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.cameras_path.exists():
                return []
            value = json.loads(self.cameras_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []

    def save_cameras(self, cameras: list[dict[str, Any]]) -> None:
        with self._lock:
            _atomic_json(self.cameras_path, cameras)

    @staticmethod
    def public_camera(camera: dict[str, Any]) -> dict[str, Any]:
        network = camera.get("source_type") in {"rtsp", "http"}
        source = str(camera.get("source", ""))
        return {
            "id": camera["id"],
            "name": camera.get("name", "Camera"),
            "source_type": camera.get("source_type", "usb"),
            "source": redact_network_source(source) if network else source,
            "source_configured": bool(source),
            "has_username": bool(camera.get("username")),
            "has_password": bool(camera.get("password")),
            "enabled": bool(camera.get("enabled", True)),
            "target_width": int(camera.get("target_width", 0) or 0),
            "target_height": int(camera.get("target_height", 0) or 0),
            "target_fps": int(camera.get("target_fps", 0) or 0),
            "rotation": int(camera.get("rotation", 0) or 0),
            "flip": str(camera.get("flip", "none") or "none"),
        }


def new_settings(port: int, bind_host: str, username: str, password: str) -> dict[str, Any]:
    salt, digest = hash_password(password)
    return {
        "version": 1,
        "port": port,
        "bind_host": bind_host,
        "username": username,
        "password_salt": salt,
        "password_hash": digest,
        "session_secret": secrets.token_urlsafe(48),
        "autostart": False,
        "autostart_kind": "none",
        "printer_dashboard_name": "3D Printer",
        "printer_dashboard_url": "",
    }
