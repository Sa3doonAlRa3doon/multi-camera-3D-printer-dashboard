import inspect

import setup as installer


def test_autostart_question_is_asked_before_application_installation():
    source = inspect.getsource(installer.main)
    question = "Start Multi Camera Printer Dashboard automatically when this computer starts? [Y/N]"
    assert question in source
    assert source.index("ask_yes_no") < source.index("copy_application")


def test_access_summary_reports_detected_lan_and_tailscale(monkeypatch):
    monkeypatch.setattr(installer, "lan_addresses", lambda: ["192.0.2.10", "100.64.0.2"])
    monkeypatch.setattr(installer, "tailscale_addresses", lambda: ["100.64.0.2"])
    lines = installer.access_summary(8080)
    assert "Local access URL: http://127.0.0.1:8080" in lines
    assert "LAN access URL: http://192.0.2.10:8080" in lines
    assert "Tailscale access URL: http://100.64.0.2:8080" in lines
