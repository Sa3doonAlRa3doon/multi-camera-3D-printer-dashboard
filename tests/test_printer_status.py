import json

import pytest

from urllib.error import URLError

from app.printer import PrinterStatusError, moonraker_status_url, moonraker_status_urls, query_printer_status


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


def test_dashboard_url_becomes_moonraker_print_stats_query():
    assert moonraker_status_url("http://printer.local:4408/#/") == (
        "http://printer.local:4408/printer/objects/query?print_stats"
    )


def test_moonraker_print_stats_are_normalized_for_layer_timelapse():
    requested = {}

    def opener(request, timeout):
        requested["url"] = request.full_url
        requested["timeout"] = timeout
        return FakeResponse(
            {
                "result": {
                    "status": {
                        "print_stats": {
                            "state": "printing",
                            "filename": "part.gcode",
                            "info": {"current_layer": 42, "total_layer": 120},
                        }
                    }
                }
            }
        )

    status = query_printer_status("http://printer.local:4408/#/", opener=opener)
    assert requested["url"].endswith("/printer/objects/query?print_stats")
    assert requested["timeout"] == 4.0
    assert status == {
        "available": True,
        "state": "printing",
        "filename": "part.gcode",
        "current_layer": 42,
        "total_layer": 120,
        "z_height": None,
        "layer_supported": True,
        "layer_source": "native",
    }


def test_missing_layer_is_reported_without_inventing_one():
    status = query_printer_status(
        "http://printer.local:4408/",
        opener=lambda *_args, **_kwargs: FakeResponse(
            {
                "result": {
                    "status": {
                        "print_stats": {
                            "state": "printing",
                            "filename": "part.gcode",
                            "info": {"current_layer": None, "total_layer": None},
                        }
                    }
                }
            }
        ),
    )
    assert status["current_layer"] is None
    assert status["layer_supported"] is False


def test_z_height_is_used_when_firmware_returns_null_layers():
    status = query_printer_status(
        "http://printer.local:4408/",
        opener=lambda *_args, **_kwargs: FakeResponse(
            {
                "result": {
                    "status": {
                        "print_stats": {
                            "state": "printing",
                            "filename": "part.gcode",
                            "z_pos": 3.437691,
                            "info": {"current_layer": None, "total_layer": None},
                        }
                    }
                }
            }
        ),
    )
    assert status["current_layer"] is None
    assert status["z_height"] == 3.4377
    assert status["layer_supported"] is True
    assert status["layer_source"] == "z_height"


def test_auto_endpoint_falls_back_from_dashboard_to_port_7125():
    requested = []

    def opener(request, **_kwargs):
        requested.append(request.full_url)
        if ":4408/" in request.full_url:
            raise URLError("proxy unavailable")
        return FakeResponse(
            {"result": {"status": {"print_stats": {"state": "standby", "info": {}}}}}
        )

    status = query_printer_status("http://printer.local:4408/#/", opener=opener)
    assert status["available"] is True
    assert requested == moonraker_status_urls("http://printer.local:4408/#/")


def test_non_moonraker_response_has_actionable_error():
    with pytest.raises(PrinterStatusError, match="Moonraker print status"):
        query_printer_status(
            "http://printer.local:4408/",
            opener=lambda *_args, **_kwargs: FakeResponse({"not": "moonraker"}),
        )
