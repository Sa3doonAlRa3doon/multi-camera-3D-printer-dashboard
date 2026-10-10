"""FastAPI application and authenticated camera-management API."""

from __future__ import annotations

import asyncio
import json
import uuid
import platform
from collections.abc import AsyncIterator, Callable
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
from .auth import csrf_token, require_csrf, signed_in
from .autostart import (
    autostart_info,
    autostart_terminal_command,
    disable_autostart,
    enable_autostart,
)
from .cameras import CameraManager, detect_usb_cameras, source_for_capture
from .config import MAX_USERS, ConfigStore, find_user, hash_password, public_user, verify_password
from .network import lan_addresses, tailscale_addresses
from .ports import INVALID_PORT_MESSAGE, automatic_port, is_port_available, is_valid_custom_port
from .printer import PrinterStatusError, query_printer_status

MAX_CAMERAS = 6


async def printer_event_stream(
    request: Request,
    dashboard_url: str,
    api_url: str,
    query: Callable[[str, str], dict] = query_printer_status,
    poll_seconds: float = 0.5,
) -> AsyncIterator[str]:
    """Continuously push normalized printer status without relying on browser timers."""
    while not await request.is_disconnected():
        if not dashboard_url:
            status = {
                "available": False,
                "error": "Configure the Fluidd/Moonraker dashboard URL before using layer timelapse.",
            }
        else:
            try:
                status = await run_in_threadpool(lambda: query(dashboard_url, api_url))
            except PrinterStatusError as exc:
                status = {"available": False, "error": str(exc)}
        yield f"data: {json.dumps(status, separators=(',', ':'))}\n\n"
        await asyncio.sleep(poll_seconds)


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
    api_url: str = Field(default="", max_length=2048)


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["admin", "viewer"] = "viewer"
    enabled: bool = True


class UserUpdatePayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default="", max_length=256)
    role: Literal["admin", "viewer"] = "viewer"
    enabled: bool = True


class PasswordPayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


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


def _validated_username(value: str) -> str:
    username = value.strip()
    if not username or any(character.isspace() for character in username):
        raise HTTPException(status_code=422, detail="Username must not be empty or contain spaces.")
    return username


def _ensure_unique_username(users: list[dict], username: str, exclude_id: str = "") -> None:
    wanted = username.casefold()
    if any(
        str(user.get("id")) != exclude_id
        and str(user.get("username", "")).strip().casefold() == wanted
        for user in users
    ):
        raise HTTPException(status_code=409, detail="That username is already in use.")


def _enabled_admin_count(users: list[dict]) -> int:
    return sum(1 for user in users if user.get("role") == "admin" and user.get("enabled", True))


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

    def session_user(request: Request) -> dict:
        user_id = str(request.session.get("user_id", ""))
        user = find_user(store.load_settings(), user_id=user_id)
        if not signed_in(request) or not user or not user.get("enabled", True):
            request.session.clear()
            raise HTTPException(status_code=401, detail="Sign in required")
        return user

    def admin_user(user: dict = Depends(session_user)) -> dict:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Administrator access is required.")
        return user

    def session_csrf(request: Request, user: dict = Depends(session_user)) -> dict:
        require_csrf(request)
        return user

    def admin_csrf(request: Request, user: dict = Depends(admin_user)) -> dict:
        require_csrf(request)
        return user

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
        user = find_user(current, username=username)
        valid_password = bool(user) and verify_password(
            password,
            str(user.get("password_salt", "")),
            str(user.get("password_hash", "")),
        )
        if not (user and user.get("enabled", True) and valid_password):
            return RedirectResponse("/login?error=Incorrect+username+or+password", status_code=303)
        request.session.clear()
        request.session["authenticated"] = True
        request.session["user_id"] = str(user["id"])
        csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, _: dict = Depends(session_csrf)):
        request.session.clear()
        return {"ok": True}

    @app.get("/")
    async def index(request: Request):
        if not signed_in(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(package_dir / "static" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/session")
    async def session(request: Request, user: dict = Depends(session_user)) -> dict:
        return {
            "csrf_token": csrf_token(request),
            "version": __version__,
            "max_cameras": MAX_CAMERAS,
            "max_users": MAX_USERS,
            "user": public_user(user),
        }

    @app.get("/api/users")
    async def list_users(_: dict = Depends(admin_user)) -> dict:
        users = store.load_settings().get("users", [])
        return {"users": [public_user(user) for user in users], "max_users": MAX_USERS}

    @app.post("/api/users", status_code=201)
    async def add_user(payload: UserCreatePayload, _: dict = Depends(admin_csrf)) -> dict:
        settings = store.load_settings()
        users = settings.setdefault("users", [])
        if len(users) >= MAX_USERS:
            raise HTTPException(status_code=409, detail=f"This dashboard supports up to {MAX_USERS} accounts.")
        username = _validated_username(payload.username)
        _ensure_unique_username(users, username)
        salt, digest = hash_password(payload.password)
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_salt": salt,
            "password_hash": digest,
            "role": payload.role,
            "enabled": payload.enabled,
        }
        users.append(user)
        store.save_settings(settings)
        return public_user(user)

    @app.put("/api/users/{user_id}")
    async def update_user(
        user_id: str,
        payload: UserUpdatePayload,
        _: dict = Depends(admin_csrf),
    ) -> dict:
        settings = store.load_settings()
        users = settings.setdefault("users", [])
        user = next((candidate for candidate in users if str(candidate.get("id")) == user_id), None)
        if not user:
            raise HTTPException(status_code=404, detail="Account not found.")
        username = _validated_username(payload.username)
        _ensure_unique_username(users, username, user_id)
        user["username"] = username
        user["role"] = payload.role
        user["enabled"] = payload.enabled
        if payload.password:
            if len(payload.password) < 8:
                raise HTTPException(status_code=422, detail="Passwords must contain at least 8 characters.")
            user["password_salt"], user["password_hash"] = hash_password(payload.password)
        if _enabled_admin_count(users) < 1:
            raise HTTPException(status_code=409, detail="At least one enabled administrator account is required.")
        store.save_settings(settings)
        return public_user(user)

    @app.delete("/api/users/{user_id}", status_code=204)
    async def delete_user(
        user_id: str,
        request: Request,
        _: dict = Depends(admin_csrf),
    ) -> None:
        if str(request.session.get("user_id", "")) == user_id:
            raise HTTPException(status_code=409, detail="Sign in with another administrator before deleting this account.")
        settings = store.load_settings()
        users = settings.setdefault("users", [])
        remaining = [user for user in users if str(user.get("id")) != user_id]
        if len(remaining) == len(users):
            raise HTTPException(status_code=404, detail="Account not found.")
        if _enabled_admin_count(remaining) < 1:
            raise HTTPException(status_code=409, detail="At least one enabled administrator account is required.")
        settings["users"] = remaining
        store.save_settings(settings)

    @app.post("/api/account/password")
    async def change_own_password(
        payload: PasswordPayload,
        user: dict = Depends(session_csrf),
    ) -> dict:
        if not verify_password(
            payload.current_password,
            str(user.get("password_salt", "")),
            str(user.get("password_hash", "")),
        ):
            raise HTTPException(status_code=403, detail="The current password is incorrect.")
        settings = store.load_settings()
        saved = find_user(settings, user_id=str(user["id"]))
        if not saved:
            raise HTTPException(status_code=404, detail="Account not found.")
        saved["password_salt"], saved["password_hash"] = hash_password(payload.new_password)
        store.save_settings(settings)
        return {"ok": True, "message": "Password changed."}

    @app.get("/api/cameras")
    async def list_cameras(_: dict = Depends(session_user)) -> list[dict]:
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
    async def get_autostart(_: dict = Depends(session_user)) -> dict:
        return autostart_info(store.root)

    @app.post("/api/autostart")
    async def set_autostart(payload: AutostartPayload, _: dict = Depends(admin_csrf)) -> dict:
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
    async def get_network(_: dict = Depends(session_user)) -> dict:
        return network_info()

    @app.get("/api/printer")
    async def get_printer(_: dict = Depends(session_user)) -> dict:
        current = store.load_settings()
        url = str(current.get("printer_dashboard_url", ""))
        return {
            "name": str(current.get("printer_dashboard_name", "3D Printer")),
            "url": url,
            "api_url": str(current.get("printer_api_url", "")),
            "configured": bool(url),
        }

    @app.post("/api/printer")
    async def set_printer(payload: PrinterPayload, _: dict = Depends(admin_csrf)) -> dict:
        current = store.load_settings()
        url = _validated_printer_url(payload.url)
        api_url = _validated_printer_url(payload.api_url)
        current["printer_dashboard_name"] = payload.name.strip()
        current["printer_dashboard_url"] = url
        current["printer_api_url"] = api_url
        store.save_settings(current)
        return {
            "name": current["printer_dashboard_name"],
            "url": url,
            "api_url": api_url,
            "configured": bool(url),
        }

    @app.get("/api/printer/status")
    async def get_printer_status(_: dict = Depends(session_user)) -> dict:
        current = store.load_settings()
        dashboard_url = str(current.get("printer_dashboard_url", ""))
        api_url = str(current.get("printer_api_url", ""))
        if not dashboard_url:
            return {
                "available": False,
                "error": "Configure the Fluidd/Moonraker dashboard URL before using layer timelapse.",
            }
        try:
            return await run_in_threadpool(lambda: query_printer_status(dashboard_url, api_url))
        except PrinterStatusError as exc:
            return {"available": False, "error": str(exc)}

    @app.get("/api/printer/events")
    async def printer_events(request: Request, _: dict = Depends(session_user)):
        """Push printer changes from the Pi so background-tab timer throttling loses no polls."""
        current = store.load_settings()
        dashboard_url = str(current.get("printer_dashboard_url", ""))
        api_url = str(current.get("printer_api_url", ""))
        return StreamingResponse(
            printer_event_stream(request, dashboard_url, api_url),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/network")
    async def set_network(payload: NetworkPayload, _: dict = Depends(admin_csrf)) -> dict:
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
    async def add_camera(payload: CameraPayload, _: dict = Depends(admin_csrf)) -> dict:
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
    async def update_camera(camera_id: str, payload: CameraPayload, _: dict = Depends(admin_csrf)) -> dict:
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
    async def delete_camera(camera_id: str, _: dict = Depends(admin_csrf)) -> None:
        cameras = store.load_cameras()
        remaining = [camera for camera in cameras if str(camera["id"]) != camera_id]
        if len(remaining) == len(cameras):
            raise HTTPException(status_code=404, detail="Camera not found")
        store.save_cameras(remaining)
        manager.sync(remaining)

    @app.get("/api/detect-usb")
    async def detect(_: dict = Depends(admin_user)) -> list[dict[str, str]]:
        return await run_in_threadpool(detect_usb_cameras)

    @app.get("/api/cameras/{camera_id}/stream")
    async def stream_camera(camera_id: str, _: dict = Depends(session_user)):
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
