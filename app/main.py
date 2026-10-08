"""FastAPI application and authenticated camera-management API."""

from __future__ import annotations

import uuid
import platform
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import csrf_token, require_auth, require_csrf, signed_in
from .autostart import (
    autostart_info,
    autostart_terminal_command,
    disable_autostart,
    enable_autostart,
)
from .cameras import CameraManager, detect_usb_cameras, source_for_capture
from .config import ConfigStore, verify_password
from .network import lan_addresses, tailscale_addresses
from .ports import INVALID_PORT_MESSAGE, automatic_port, is_port_available, is_valid_custom_port
from .printer import PrinterStatusError, query_printer_status

MAX_CAMERAS = 6


class CameraPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source_type: Literal["usb", "rtsp", "http"]
    source: str = Field(default="", max_length=2048)
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=512)
    enabled: bool = True
    target_width: int = Field(default=0, ge=0, le=3840)
    target_height: int = Field(default=0, ge=0, le=2160)
    target_fps: int = Field(default=0, ge=0, le=60)
    rotation: Literal[0, 90, 180, 270] = 0
    flip: Literal["none", "horizontal", "vertical", "both"] = "none"
    clear_username: bool = False
    clear_password: bool = False

    @model_validator(mode="after")
    def validate_resolution(self) -> "CameraPayload":
        if bool(self.target_width) != bool(self.target_height):
            raise ValueError("Resolution width and height must both be set, or both be 0 for source resolution.")
        if self.target_width and (self.target_width < 160 or self.target_height < 120):
            raise ValueError("Custom resolution must be at least 160 x 120.")
        return self


class AutostartPayload(BaseModel):
    enabled: bool


class NetworkPayload(BaseModel):
    mode: Literal["custom", "automatic"]
    port: str = ""


class PrinterPayload(BaseModel):
    name: str = Field(default="3D Printer", min_length=1, max_length=80)
    url: str = Field(default="", max_length=2048)


def _validated_printer_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        valid = False
        parsed = None
    if not valid:
        raise HTTPException(
            status_code=422,
            detail="Enter a complete HTTP or HTTPS printer dashboard URL, for example http://printer-ip:4408/#/.",
        )
    if parsed and (parsed.username or parsed.password):
        raise HTTPException(
            status_code=422,
            detail="Do not put a username or password in the dashboard URL.",
        )
    return value


def _validated_camera(payload: CameraPayload, old: dict | None = None) -> dict:
    values = payload.model_dump()
    source = values["source"].strip()
    if old and (not source or "REDACTED" in source):
        source = str(old.get("source", ""))
    username = values["username"]
    password = values["password"]
    if old:
        if not username and not values["clear_username"]:
            username = str(old.get("username", ""))
        if not password and not values["clear_password"]:
            password = str(old.get("password", ""))
    camera = {
        "id": str(old["id"]) if old else str(uuid.uuid4()),
        "name": values["name"].strip(),
        "source_type": values["source_type"],
        "source": source,
        "username": "" if values["clear_username"] else username,
        "password": "" if values["clear_password"] else password,
        "enabled": values["enabled"],
        "target_width": values["target_width"],
        "target_height": values["target_height"],
        "target_fps": values["target_fps"],
        "rotation": values["rotation"],
        "flip": values["flip"],
    }
    if not source:
        raise HTTPException(status_code=422, detail="A camera device or stream URL is required.")
    try:
        source_for_capture(camera)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return camera


def create_app(
    store: ConfigStore | None = None,
    manager: CameraManager | None = None,
    active_port: int | None = None,
) -> FastAPI:
    store = store or ConfigStore()
    settings = store.load_settings()
    running_port = int(active_port if active_port is not None else settings.get("port", 8080))
    manager = manager or CameraManager()
    package_dir = Path(__file__).resolve().parent

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.sync(store.load_cameras())
        yield
        manager.shutdown()

    app = FastAPI(title="Multi Camera Printer Dashboard", version=__version__, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings["session_secret"],
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=package_dir / "static"), name="static")
    app.state.store = store
    app.state.manager = manager

    def network_info(message: str = "") -> dict:
        current = store.load_settings()
        saved_port = int(current.get("port", 8080))
        tailscale = tailscale_addresses()
        lan = [address for address in lan_addresses() if address not in tailscale]
        return {
            "active_port": running_port,
            "saved_port": saved_port,
            "restart_required": saved_port != running_port,
            "bind_host": str(current.get("bind_host", "0.0.0.0")),
            "local_urls": [f"http://127.0.0.1:{running_port}"],
            "lan_urls": [f"http://{address}:{running_port}" for address in lan],
            "tailscale_urls": [f"http://{address}:{running_port}" for address in tailscale],
            "message": message,
            "platform": platform.system(),
        }

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "application": "Multi Camera Printer Dashboard", "version": __version__}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        if signed_in(request):
            return RedirectResponse("/", status_code=303)
        html = (package_dir / "static" / "login.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("{{ERROR}}", error))

    @app.post("/login")
    async def login(request: Request, username: str = Form(), password: str = Form()):
        current = store.load_settings()
        valid_user = username == current.get("username")
        valid_password = verify_password(
            password,
            str(current.get("password_salt", "")),
            str(current.get("password_hash", "")),
        )
        if not (valid_user and valid_password):
            return RedirectResponse("/login?error=Incorrect+username+or+password", status_code=303)
        request.session.clear()
        request.session["authenticated"] = True
        csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, _: None = Depends(require_csrf)):
        request.session.clear()
        return {"ok": True}

    @app.get("/")
    async def index(request: Request):
        if not signed_in(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(package_dir / "static" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/session")
    async def session(request: Request, _: None = Depends(require_auth)) -> dict:
        return {"csrf_token": csrf_token(request), "version": __version__, "max_cameras": MAX_CAMERAS}

    @app.get("/api/cameras")
    async def list_cameras(_: None = Depends(require_auth)) -> list[dict]:
        cameras = store.load_cameras()
        manager.sync(cameras)
        statuses = manager.statuses()
        result = []
        for camera in cameras:
            public = store.public_camera(camera)
            public["status"] = statuses.get(str(camera["id"]), {"state": "disabled", "detail": ""})
            result.append(public)
        return result

    @app.get("/api/autostart")
    async def get_autostart(_: None = Depends(require_auth)) -> dict:
        return autostart_info(store.root)

    @app.post("/api/autostart")
    async def set_autostart(payload: AutostartPayload, _: None = Depends(require_csrf)) -> dict:
        root = store.root
        if payload.enabled:
            ok, detail = await run_in_threadpool(
                lambda: enable_autostart(root, start_now=False, non_interactive=True)
            )
        else:
            ok, detail = await run_in_threadpool(
                lambda: disable_autostart(root, stop_now=False, non_interactive=True)
            )
        current = autostart_info(root)
        if ok:
            settings = store.load_settings()
            settings["autostart"] = bool(current["enabled"])
            settings["autostart_kind"] = str(current["status"]) if current["enabled"] else "none"
            store.save_settings(settings)
        needs_linux_repair = (
            payload.enabled
            and current["platform"] == "Linux"
            and not bool(current.get("healthy"))
        )
        command = autostart_terminal_command(root, payload.enabled) if (not ok or needs_linux_repair) else ""
        if needs_linux_repair and ok:
            detail = (
                "Automatic startup is registered, but the service is not running. "
                "Run the displayed terminal command once to stop the manual copy, start systemd, and verify it."
            )
        return {
            **current,
            "changed": ok,
            "message": detail or ("Automatic startup enabled." if payload.enabled else "Manual startup selected."),
            "requires_admin": (not ok or needs_linux_repair) and current["platform"] == "Linux",
            "command": command,
        }

    @app.get("/api/network")
    async def get_network(_: None = Depends(require_auth)) -> dict:
        return network_info()

    @app.get("/api/printer")
    async def get_printer(_: None = Depends(require_auth)) -> dict:
        current = store.load_settings()
        url = str(current.get("printer_dashboard_url", ""))
        return {
            "name": str(current.get("printer_dashboard_name", "3D Printer")),
            "url": url,
            "configured": bool(url),
        }

    @app.post("/api/printer")
    async def set_printer(payload: PrinterPayload, _: None = Depends(require_csrf)) -> dict:
        current = store.load_settings()
        url = _validated_printer_url(payload.url)
        current["printer_dashboard_name"] = payload.name.strip()
        current["printer_dashboard_url"] = url
        store.save_settings(current)
        return {
            "name": current["printer_dashboard_name"],
            "url": url,
            "configured": bool(url),
        }

    @app.get("/api/printer/status")
    async def get_printer_status(_: None = Depends(require_auth)) -> dict:
        dashboard_url = str(store.load_settings().get("printer_dashboard_url", ""))
        if not dashboard_url:
            return {
                "available": False,
                "error": "Configure the Fluidd/Moonraker dashboard URL before using layer timelapse.",
            }
        try:
            return await run_in_threadpool(lambda: query_printer_status(dashboard_url))
        except PrinterStatusError as exc:
            return {"available": False, "error": str(exc)}

    @app.post("/api/network")
    async def set_network(payload: NetworkPayload, _: None = Depends(require_csrf)) -> dict:
        current = store.load_settings()
        bind_host = str(current.get("bind_host", "0.0.0.0"))
        if payload.mode == "automatic":
            selected = await run_in_threadpool(lambda: automatic_port(bind_host))
            message = f"Port {selected} was selected automatically and saved for the next launch."
        else:
            value = payload.port.strip()
            if not is_valid_custom_port(value):
                raise HTTPException(status_code=422, detail=INVALID_PORT_MESSAGE)
            selected = int(value)
            available = selected == running_port or await run_in_threadpool(
                lambda: is_port_available(bind_host, selected)
            )
            if not available:
                raise HTTPException(
                    status_code=409,
                    detail=f"Port {selected} is already occupied. Enter another port or choose automatic selection.",
                )
            message = f"Port {selected} was saved as the preferred port for the next launch."
        current["port"] = selected
        store.save_settings(current)
        return network_info(message)

    @app.post("/api/cameras", status_code=201)
    async def add_camera(payload: CameraPayload, _: None = Depends(require_csrf)) -> dict:
        cameras = store.load_cameras()
        if len(cameras) >= MAX_CAMERAS:
            raise HTTPException(
                status_code=409,
                detail=f"This dashboard supports up to {MAX_CAMERAS} cameras. Remove one before adding another.",
            )
        camera = _validated_camera(payload)
        cameras.append(camera)
        store.save_cameras(cameras)
        manager.sync(cameras)
        return store.public_camera(camera)

    @app.put("/api/cameras/{camera_id}")
    async def update_camera(camera_id: str, payload: CameraPayload, _: None = Depends(require_csrf)) -> dict:
        cameras = store.load_cameras()
        index = next((i for i, camera in enumerate(cameras) if str(camera["id"]) == camera_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        camera = _validated_camera(payload, cameras[index])
        cameras[index] = camera
        store.save_cameras(cameras)
        manager.sync(cameras)
        return store.public_camera(camera)

    @app.delete("/api/cameras/{camera_id}", status_code=204)
    async def delete_camera(camera_id: str, _: None = Depends(require_csrf)) -> None:
        cameras = store.load_cameras()
        remaining = [camera for camera in cameras if str(camera["id"]) != camera_id]
        if len(remaining) == len(cameras):
            raise HTTPException(status_code=404, detail="Camera not found")
        store.save_cameras(remaining)
        manager.sync(remaining)

    @app.get("/api/detect-usb")
    async def detect(_: None = Depends(require_auth)) -> list[dict[str, str]]:
        return await run_in_threadpool(detect_usb_cameras)

    @app.get("/api/cameras/{camera_id}/stream")
    async def stream_camera(camera_id: str, _: None = Depends(require_auth)):
        cameras = store.load_cameras()
        if not any(str(camera["id"]) == camera_id and camera.get("enabled", True) for camera in cameras):
            raise HTTPException(status_code=404, detail="Camera not found or disabled")
        try:
            frames = manager.stream(camera_id, cameras)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Camera not found") from exc
        return StreamingResponse(
            frames,
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "X-Accel-Buffering": "no"},
        )

    return app
