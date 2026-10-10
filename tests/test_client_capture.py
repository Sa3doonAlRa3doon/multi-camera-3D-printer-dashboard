from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_camera_cards_offer_client_side_screenshot_and_recording():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'class="snapshot"' in html
    assert 'class="record"' in html
    assert "canvas.captureStream(15)" in javascript
    assert "new MediaRecorder" in javascript
    assert "link.download = filename" in javascript
    assert "/record" not in server


def test_camera_cards_offer_browser_side_timelapse_with_saved_controls():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'class="timelapse"' in html
    for control in ("timelapse-mode", "timelapse-interval", "timelapse-fps", "timelapse-max-frames"):
        assert f'id="{control}"' in html
    assert "Every printer layer" in html
    assert 'localStorage.setItem("timelapseOptions"' in javascript
    assert "captureTimelapseFrame" in javascript
    assert "updateLayerTimelapse" in javascript
    assert 'api("/api/printer/status")' in javascript
    assert 'new EventSource("/api/printer/events")' in javascript
    assert "startLayerEventStream" in javascript
    assert "initialPrinterStatus" in javascript
    assert "nativeLayer > active.lastLayer" in javascript
    assert "usableZ > active.highestZ + 0.05" in javascript
    assert "drawTimelapseFrame" in javascript
    assert "videoExtension(mimeType, format.extension)" in javascript
    assert '@app.get("/api/printer/events")' in (ROOT / "app" / "main.py").read_text(encoding="utf-8")


def test_printer_dashboard_has_protected_configurable_embed_and_fallback():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'id="printer-frame"' in html
    assert 'id="printer-open"' in html
    assert "refused to connect" in html
    assert 'api("/api/printer"' in javascript
    assert '@app.get("/api/printer")' in server
    assert '@app.post("/api/printer")' in server


def test_camera_dialog_offers_saved_video_adjustments():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    for control in ("camera-resolution", "camera-fps", "camera-rotation", "camera-flip"):
        assert f'id="{control}"' in html
    assert 'value="90">90° right' in html
    assert 'value="270">90° left' in html
    assert 'value="180">180° upside down' in html
    assert "target_width: width" in javascript
    assert "target_fps: Number" in javascript


def test_settings_drawer_offers_automatic_and_manual_startup_modes():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="autostart-auto"' in html
    assert 'id="autostart-manual"' in html
    assert 'id="autostart-refresh"' in html
    assert 'api("/api/autostart"' in javascript
    assert 'JSON.stringify({ enabled })' in javascript


def test_settings_drawer_offers_saved_port_and_actual_access_urls():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="network-port"' in html
    assert 'id="network-save"' in html
    assert 'id="network-auto"' in html
    assert 'id="network-urls"' in html
    assert 'api("/api/network"' in javascript
    assert 'mode, port: $("#network-port").value' in javascript
