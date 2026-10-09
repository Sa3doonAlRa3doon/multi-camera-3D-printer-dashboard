"""Read normalized print and layer state from a Fluidd/Moonraker dashboard."""

from __future__ import annotations

import json
import math
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class PrinterStatusError(RuntimeError):
    """The configured printer status API could not be read."""


def moonraker_status_url(base_url: str) -> str:
    """Build the Moonraker query URL from a dashboard or Moonraker base URL."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PrinterStatusError("The printer dashboard or Moonraker API URL is not configured correctly.")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{origin}/printer/objects/query?print_stats"


def moonraker_status_urls(dashboard_url: str, api_url: str = "") -> list[str]:
    """Return explicit or automatic Moonraker endpoints in deterministic order."""
    if api_url.strip():
        return [moonraker_status_url(api_url.strip())]
    primary = moonraker_status_url(dashboard_url)
    parsed = urlsplit(dashboard_url)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    fallback_origin = urlunsplit((parsed.scheme, f"{host}:7125", "", "", ""))
    candidates = [primary, moonraker_status_url(fallback_origin)]
    return list(dict.fromkeys(candidates))


def _layer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _height(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, 4) if number >= 0 and math.isfinite(number) else None


def _read_status(url: str, timeout: float, opener: Callable[..., Any]) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "MultiCameraPrinterDashboard/2"},
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
    z_height = _height(stats.get("z_pos"))
    return {
        "available": True,
        "state": str(stats.get("state", "unknown")),
        "filename": str(stats.get("filename", "")),
        "current_layer": current_layer,
        "total_layer": total_layer,
        "z_height": z_height,
        "layer_supported": current_layer is not None or z_height is not None,
        "layer_source": "native" if current_layer is not None else ("z_height" if z_height is not None else "unavailable"),
    }


def query_printer_status(
    dashboard_url: str,
    api_url: str = "",
    *,
    timeout: float = 4.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Query Moonraker, trying its dashboard proxy then port 7125 when set to Auto."""
    errors: list[PrinterStatusError] = []
    for status_url in moonraker_status_urls(dashboard_url, api_url):
        try:
            return _read_status(status_url, timeout, opener)
        except PrinterStatusError as exc:
            errors.append(exc)
    if api_url.strip() and errors:
        raise errors[-1]
    if errors and any("could not be reached" not in str(error) for error in errors):
        raise errors[-1]
    raise PrinterStatusError(
        "Moonraker could not be reached through the dashboard or port 7125. Set the Moonraker API URL in Settings."
    )
