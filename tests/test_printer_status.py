import json

import pytest

from app.printer import PrinterStatusError, moonraker_status_url, query_printer_status


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
        "layer_supported": True,
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


def test_non_moonraker_response_has_actionable_error():
    with pytest.raises(PrinterStatusError, match="Moonraker print status"):
        query_printer_status(
            "http://printer.local:4408/",
            opener=lambda *_args, **_kwargs: FakeResponse({"not": "moonraker"}),
        )
