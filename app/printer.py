"""Read normalized print and layer state from a Fluidd/Moonraker dashboard."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class PrinterStatusError(RuntimeError):
    """The configured printer status API could not be read."""


def moonraker_status_url(dashboard_url: str) -> str:
    """Build the Moonraker query URL from a Fluidd/Mainsail dashboard URL."""
    parsed = urlsplit(dashboard_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PrinterStatusError("The printer dashboard URL is not configured correctly.")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{origin}/printer/objects/query?print_stats"


def _layer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def query_printer_status(
    dashboard_url: str,
    *,
    timeout: float = 4.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Query Moonraker and return only the fields needed by layer timelapse."""
    request = Request(
        moonraker_status_url(dashboard_url),
        headers={"Accept": "application/json", "User-Agent": "MultiCameraPrinterDashboard/1"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
    except HTTPError as exc:
        raise PrinterStatusError(f"The printer returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PrinterStatusError("The printer layer API could not be reached.") from exc
    if len(raw) > 1_000_000:
        raise PrinterStatusError("The printer returned an unexpectedly large status response.")
    try:
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("error"):
            raise PrinterStatusError("Moonraker returned an error while reading print status.")
        stats = payload["result"]["status"]["print_stats"]
        info = stats.get("info") or {}
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise PrinterStatusError("The dashboard did not return Moonraker print status data.") from exc
    current_layer = _layer(info.get("current_layer"))
    total_layer = _layer(info.get("total_layer"))
    return {
        "available": True,
        "state": str(stats.get("state", "unknown")),
        "filename": str(stats.get("filename", "")),
        "current_layer": current_layer,
        "total_layer": total_layer,
        "layer_supported": current_layer is not None,
    }
