import cv2
import pytest

from app.camera.camera_manager import CameraManager


class FakeCapture:
    def __init__(self, opened, readable=True):
        self.opened = opened
        self.readable = readable
        self.released = False

    def isOpened(self):
        return self.opened and not self.released

    def read(self):
        return self.readable, object() if self.readable else None

    def release(self):
        self.released = True

    def get(self, prop):
        return 30

    def set(self, prop, value):
        return True


@pytest.mark.parametrize("system,expected", [
    ("Windows", cv2.CAP_DSHOW),
    ("Darwin", cv2.CAP_AVFOUNDATION),
    ("Linux", cv2.CAP_V4L2),
])
def test_open_uses_platform_backend(monkeypatch, system, expected):
    monkeypatch.setattr("app.camera.camera_manager.platform.system", lambda: system)
    monkeypatch.setattr(CameraManager, "_macos_camera_profiles", staticmethod(lambda: []))
    calls = []
    def capture(index, backend):
        calls.append((index, backend))
        return FakeCapture(True)
    monkeypatch.setattr(cv2, "VideoCapture", capture)
    manager = CameraManager()
    assert manager.open(2)["connected"]
    assert calls == [(2, expected)]
    manager.close()


def test_windows_fallback_releases_failed_capture(monkeypatch):
    monkeypatch.setattr("app.camera.camera_manager.platform.system", lambda: "Windows")
    failed, working = FakeCapture(False), FakeCapture(True)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index, backend: failed if backend == cv2.CAP_DSHOW else working)
    manager = CameraManager()
    assert manager.open(0)["backend"] == "Media Foundation"
    assert failed.released
    manager.close()
    assert working.released


def test_discovery_falls_back_when_backend_has_no_frames(monkeypatch):
    monkeypatch.setattr("app.camera.camera_manager.platform.system", lambda: "Windows")
    failed, working = FakeCapture(True, False), FakeCapture(True)
    monkeypatch.setattr(cv2, "VideoCapture", lambda index, backend: failed if backend == cv2.CAP_DSHOW else working)
    cameras = CameraManager().enumerate_cameras(max_devices=1)
    assert cameras[0]["backend"] == "Media Foundation"
    assert failed.released and working.released


def test_discovery_does_not_reopen_active_camera(monkeypatch):
    monkeypatch.setattr("app.camera.camera_manager.platform.system", lambda: "Windows")
    monkeypatch.setattr(cv2, "VideoCapture", lambda index, backend: FakeCapture(True))
    manager = CameraManager()
    manager.open(0)
    def unexpected(*args):
        pytest.fail("Active camera must not be opened a second time")
    monkeypatch.setattr(cv2, "VideoCapture", unexpected)
    assert manager.enumerate_cameras(max_devices=1)[0]["status"] == "CONNECTED"
    assert manager.status()["connected"]
    manager.close()
