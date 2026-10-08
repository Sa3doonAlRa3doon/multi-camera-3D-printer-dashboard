"""Bind the selected port, print access information, and run the server."""

from __future__ import annotations

import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from . import __version__
from .autostart import autostart_status
from .config import ConfigStore, application_home
from .main import create_app
from .network import lan_addresses, tailscale_addresses
from .ports import automatic_port


def configure_logging(root: Path) -> Path:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "multi-camera-printer-dashboard.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler], force=True)
    return log_path


def bind_listener(store: ConfigStore, settings: dict) -> tuple[socket.socket, int, bool]:
    host = str(settings.get("bind_host", "0.0.0.0"))
    requested = int(settings.get("port", 8080))
    port = requested
    for attempt in range(50):
        if attempt:
            port = automatic_port(host)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind((host, port))
            listener.listen(2048)
            listener.setblocking(False)
            if port != requested:
                settings["port"] = port
                store.save_settings(settings)
            return listener, port, port != requested
        except OSError:
            listener.close()
            if attempt == 0:
                logging.error("Saved port %s is unavailable on %s; selecting a fallback.", requested, host)
            else:
                logging.warning("Port %s became occupied during startup; retrying selection.", port)
    raise RuntimeError("Unable to claim an available port after repeated attempts.")


def startup_lines(root: Path, port: int, log_path: Path, fallback: bool) -> list[str]:
    tailscale = tailscale_addresses()
    lan = [address for address in lan_addresses() if address not in tailscale]
    lines = [
        f"Multi Camera Printer Dashboard v{__version__}",
        f"Selected port: {port}" + (" (automatic fallback because the saved port was occupied)" if fallback else ""),
        f"Installation folder: {root}",
        f"Local access: http://127.0.0.1:{port}",
    ]
    if lan:
        lines.extend(f"LAN access: http://{address}:{port}" for address in lan)
    else:
        lines.append("LAN access: no active LAN address detected")
    if tailscale:
        lines.extend(f"Tailscale access: http://{address}:{port}" for address in tailscale)
    else:
        lines.append("Tailscale access: no connected Tailscale IPv4 address detected")
    lines.extend([f"Autostart: {autostart_status()}", f"Log location: {log_path}"])
    return lines


def main() -> int:
    root = application_home()
    log_path = configure_logging(root)
    store = ConfigStore(root)
    try:
        settings = store.load_settings()
        listener, port, fallback = bind_listener(store, settings)
        for line in startup_lines(root, port, log_path, fallback):
            print(line, flush=True)
            logging.info(line)
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "data" / "server.pid").write_text(str(os.getpid()), encoding="ascii")
        app = create_app(store, active_port=port)
        config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="on")
        server = uvicorn.Server(config)
        server.run(sockets=[listener])
        return 0
    except KeyboardInterrupt:
        logging.info("Multi Camera Printer Dashboard stopped by the user.")
        return 0
    except FileNotFoundError as exc:
        logging.error("Startup failed: %s", exc)
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logging.exception("Startup failed")
        print(f"Startup failed: {exc}. See {log_path}", file=sys.stderr)
        return 1
    finally:
        pid_path = root / "data" / "server.pid"
        try:
            if pid_path.exists() and pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
