"""Independent camera capture, conversion, and automatic recovery."""

from __future__ import annotations

import os
import platform
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np

CaptureFactory = Callable[[Any, str], Any]


def source_for_capture(camera: dict[str, Any]) -> Any:
    source_type = camera.get("source_type", "usb")
    source = str(camera.get("source", ""))
    if source_type == "usb":
        return int(source) if source.isdigit() else source
    if source_type not in {"rtsp", "http"}:
        raise ValueError(f"Unsupported camera type: {source_type}")
    parts = urlsplit(source)
    if parts.scheme.lower() not in {"rtsp", "rtsps", "http", "https"}:
        raise ValueError("Network cameras require an RTSP, HTTP, or HTTPS stream URL.")
    username = camera.get("username") or ""
    password = camera.get("password") or ""
    if username:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        userinfo = quote(str(username), safe="")
        if password:
            userinfo += ":" + quote(str(password), safe="")
        parts = parts._replace(netloc=f"{userinfo}@{host}")
    return urlunsplit(parts)


def default_capture_factory(source: Any, source_type: str) -> cv2.VideoCapture:
    capture = cv2.VideoCapture()
    if source_type == "usb":
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        capture.open(source, backend)
    else:
        parameters: list[int] = []
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            parameters.extend([cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8_000])
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            parameters.extend([cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8_000])
        capture.open(source, cv2.CAP_FFMPEG, parameters)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
    return capture


def placeholder_frame(name: str, status: str, detail: str = "") -> bytes:
    canvas = np.zeros((540, 960, 3), dtype=np.uint8)
    canvas[:] = (20, 24, 32)
    cv2.putText(canvas, name[:48], (48, 205), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (238, 242, 255), 2)
    cv2.putText(canvas, status.upper(), (48, 275), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (66, 173, 245), 2)
    if detail:
        cv2.putText(canvas, detail[:75], (48, 330), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (170, 178, 195), 1)
    ok, encoded = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return encoded.tobytes() if ok else b""


def configure_capture(capture: Any, camera: dict[str, Any]) -> None:
    """Request USB capture settings when the device/backend supports them."""
    if camera.get("source_type", "usb") != "usb" or not hasattr(capture, "set"):
        return
    settings = (
        (cv2.CAP_PROP_FRAME_WIDTH, int(camera.get("target_width", 0) or 0)),
        (cv2.CAP_PROP_FRAME_HEIGHT, int(camera.get("target_height", 0) or 0)),
        (cv2.CAP_PROP_FPS, int(camera.get("target_fps", 0) or 0)),
    )
    for property_id, value in settings:
        if not value:
            continue
        try:
            capture.set(property_id, value)
        except Exception:
            # Many camera drivers reject unsupported modes by returning False or raising.
            # Output resizing and frame limiting below still provide predictable browser output.
            pass


def transform_frame(frame: np.ndarray, camera: dict[str, Any]) -> np.ndarray:
    """Apply the saved output resolution, rotation, and flip to a captured frame."""
    width = int(camera.get("target_width", 0) or 0)
    height = int(camera.get("target_height", 0) or 0)
    if width and height and (frame.shape[1] != width or frame.shape[0] != height):
        shrinking = width < frame.shape[1] or height < frame.shape[0]
        interpolation = cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR
        frame = cv2.resize(frame, (width, height), interpolation=interpolation)

    rotation = int(camera.get("rotation", 0) or 0)
    rotate_codes = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    if rotation in rotate_codes:
        frame = cv2.rotate(frame, rotate_codes[rotation])

    flip = str(camera.get("flip", "none") or "none")
    flip_codes = {"horizontal": 1, "vertical": 0, "both": -1}
    if flip in flip_codes:
        frame = cv2.flip(frame, flip_codes[flip])
    return frame


class CameraWorker:
    def __init__(self, camera: dict[str, Any], capture_factory: CaptureFactory = default_capture_factory) -> None:
        self.camera = dict(camera)
        self.capture_factory = capture_factory
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._sequence = 0
        self._status = "stopped"
        self._detail = ""
        self._last_frame_at: float | None = None
        self._reconnects = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"camera-{self.camera.get('id', 'unknown')}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._set_status("stopped", "")

    def status(self) -> dict[str, Any]:
        with self._condition:
            age = time.time() - self._last_frame_at if self._last_frame_at else None
            return {
                "state": self._status,
                "detail": self._detail,
                "last_frame_age_seconds": round(age, 1) if age is not None else None,
                "reconnects": self._reconnects,
            }

    def _set_status(self, status: str, detail: str) -> None:
        with self._condition:
            self._status = status
            self._detail = detail
            self._condition.notify_all()

    def _publish(self, frame: bytes) -> None:
        with self._condition:
            self._frame = frame
            self._sequence += 1
            self._last_frame_at = time.time()
            self._condition.notify_all()

    def _run(self) -> None:
        delay = 1.0
        first_attempt = True
        while not self._stop.is_set():
            capture = None
            self._set_status("connecting" if first_attempt else "reconnecting", "Opening camera stream")
            try:
                source = source_for_capture(self.camera)
                capture = self.capture_factory(source, str(self.camera.get("source_type", "usb")))
                if not capture or not capture.isOpened():
                    raise RuntimeError("Camera could not be opened")
                configure_capture(capture, self.camera)
                self._set_status("online", "")
                delay = 1.0
                target_fps = int(self.camera.get("target_fps", 0) or 0)
                minimum_interval = 1.0 / target_fps if target_fps else 0.0
                last_published = 0.0
                while not self._stop.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise RuntimeError("Camera stopped sending frames")
                    now = time.monotonic()
                    if minimum_interval and now - last_published < minimum_interval:
                        continue
                    frame = transform_frame(frame, self.camera)
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
                    )
                    if encoded_ok:
                        self._publish(encoded.tobytes())
                        last_published = now
            except Exception as exc:  # Capture backends report failures through varied exception types.
                self._reconnects += 1
                self._set_status("disconnected", f"{exc}. Retrying in {int(delay)}s")
            finally:
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:
                        pass
            first_attempt = False
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 15.0)

    def multipart_frames(self) -> Iterator[bytes]:
        self.start()
        seen = -1
        while not self._stop.is_set():
            with self._condition:
                self._condition.wait_for(
                    lambda: self._sequence != seen or self._stop.is_set(), timeout=1.0
                )
                frame = self._frame
                sequence = self._sequence
                status = self._status
                detail = self._detail
            if self._stop.is_set():
                return
            if frame is None or sequence == seen:
                frame = placeholder_frame(str(self.camera.get("name", "Camera")), status, detail)
            else:
                seen = sequence
            yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"


class CameraManager:
    def __init__(self, capture_factory: CaptureFactory = default_capture_factory) -> None:
        self.capture_factory = capture_factory
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.RLock()

    def sync(self, cameras: list[dict[str, Any]]) -> None:
        wanted = {str(camera["id"]): camera for camera in cameras if camera.get("enabled", True)}
        with self._lock:
            for camera_id in list(self._workers):
                current = self._workers[camera_id]
                if camera_id not in wanted or current.camera != wanted[camera_id]:
                    current.stop()
                    del self._workers[camera_id]
            for camera_id, camera in wanted.items():
                if camera_id not in self._workers:
                    self._workers[camera_id] = CameraWorker(camera, self.capture_factory)

    def stream(self, camera_id: str, cameras: list[dict[str, Any]]) -> Iterator[bytes]:
        self.sync(cameras)
        with self._lock:
            worker = self._workers.get(camera_id)
            if not worker:
                raise KeyError(camera_id)
            return worker.multipart_frames()

    def statuses(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {camera_id: worker.status() for camera_id, worker in self._workers.items()}

    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()


def detect_usb_cameras(limit: int = 10) -> list[dict[str, str]]:
    if platform.system() == "Linux":
        devices = sorted(Path("/dev").glob("video*"))
        return [{"source": str(path), "name": f"USB camera ({path.name})"} for path in devices]
    detected: list[dict[str, str]] = []
    for index in range(limit):
        capture = default_capture_factory(index, "usb")
        try:
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    detected.append({"source": str(index), "name": f"Camera {index}"})
        finally:
            capture.release()
    return detected
