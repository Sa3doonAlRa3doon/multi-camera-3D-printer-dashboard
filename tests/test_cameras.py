import time

import numpy as np

from app.cameras import CameraManager, CameraWorker, configure_capture, source_for_capture, transform_frame


class FakeCapture:
    def __init__(self, frames, opened=True):
        self.frames = list(frames)
        self.opened = opened
        self.released = False
        self.settings = []

    def isOpened(self):
        return self.opened

    def read(self):
        if not self.frames:
            return False, None
        return self.frames.pop(0)

    def release(self):
        self.released = True

    def set(self, property_id, value):
        self.settings.append((property_id, value))
        return True


def wait_for(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.03)
    return False


def test_network_credentials_are_injected_only_for_capture():
    source = source_for_capture({"source_type": "rtsp", "source": "rtsp://cam.local/live", "username": "a@b", "password": "p:q"})
    assert source == "rtsp://a%40b:p%3Aq@cam.local/live"


def test_frame_rotation_and_flip_are_applied_in_display_order():
    frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    rotated = transform_frame(frame, {"rotation": 90, "flip": "none"})
    assert np.array_equal(rotated, np.rot90(frame, k=3))

    transformed = transform_frame(frame, {"rotation": 270, "flip": "horizontal"})
    expected = np.flip(np.rot90(frame, k=1), axis=1)
    assert np.array_equal(transformed, expected)


def test_resolution_and_usb_capture_settings_are_applied():
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    camera = {
        "source_type": "usb",
        "target_width": 640,
        "target_height": 480,
        "target_fps": 15,
        "rotation": 0,
        "flip": "vertical",
    }
    capture = FakeCapture([])
    configure_capture(capture, camera)
    assert [value for _, value in capture.settings] == [640, 480, 15]
    assert transform_frame(frame, camera).shape == (480, 640, 3)


def test_disconnected_camera_reconnects_and_publishes_frame():
    frame = np.full((24, 32, 3), 125, dtype=np.uint8)
    captures = [FakeCapture([], opened=False), FakeCapture([(True, frame)] * 20)]

    def factory(_source, _type):
        return captures.pop(0) if captures else FakeCapture([(True, frame)] * 20)

    worker = CameraWorker({"id": "one", "name": "Test", "source_type": "usb", "source": "0", "enabled": True}, factory)
    worker.start()
    try:
        assert wait_for(lambda: worker.status()["reconnects"] >= 1)
        assert wait_for(lambda: worker._frame is not None)
    finally:
        worker.stop()


def test_two_camera_workers_run_independently():
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    def factory(_source, _type):
        return FakeCapture([(True, frame)] * 100)

    cameras = [
        {"id": "one", "name": "One", "source_type": "usb", "source": "0", "enabled": True},
        {"id": "two", "name": "Two", "source_type": "usb", "source": "1", "enabled": True},
    ]
    manager = CameraManager(factory)
    manager.sync(cameras)
    streams = [manager.stream(camera["id"], cameras) for camera in cameras]
    try:
        first = [next(stream) for stream in streams]
        assert all(chunk.startswith(b"--frame") for chunk in first)
        assert set(manager.statuses()) == {"one", "two"}
    finally:
        manager.shutdown()


def test_six_camera_workers_can_stream_simultaneously():
    frame = np.zeros((12, 16, 3), dtype=np.uint8)

    def factory(_source, _type):
        return FakeCapture([(True, frame)] * 100)

    cameras = [
        {"id": f"camera-{index}", "name": f"Camera {index}", "source_type": "usb", "source": str(index), "enabled": True}
        for index in range(6)
    ]
    manager = CameraManager(factory)
    manager.sync(cameras)
    streams = [manager.stream(camera["id"], cameras) for camera in cameras]
    try:
        assert all(next(stream).startswith(b"--frame") for stream in streams)
        assert set(manager.statuses()) == {camera["id"] for camera in cameras}
    finally:
        manager.shutdown()
