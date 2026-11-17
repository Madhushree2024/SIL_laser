import json
from types import SimpleNamespace
import cv2
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.camera.camera_manager import CameraManager
from app.camera.windows_devices import is_usb_camera, windows_camera_profiles


@pytest.mark.parametrize("name,allowed", [("USB Camera", True), ("Waveshare IMX335", True), ("UVC Camera", True),
    ("HP HD Camera", False), ("Integrated USB Webcam", False), ("OBS Virtual Camera", False), ("Camera 0", False)])
def test_usb_identity_filter(name, allowed):
    assert is_usb_camera({"name": name}) is allowed


def test_usb_path_allows_external_camera_without_usb_in_name():
    assert is_usb_camera({"name": "Logitech BRIO", "device_path": r"\\?\usb#vid_1234"})
    assert not is_usb_camera({"name": "HP HD Camera", "device_path": r"\\?\usb#vid_1234"})


def test_usb_only_discovery_never_probes_builtin(monkeypatch):
    monkeypatch.setattr("app.camera.camera_manager.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.camera.camera_manager.windows_camera_profiles", lambda: [
        {"index": 0, "name": "HP HD Camera"}, {"index": 1, "name": "USB Camera"}])
    calls = []
    class Capture:
        def isOpened(self): return True
        def read(self): return True, object()
        def get(self, prop): return 30
        def release(self): pass
        def set(self, prop, value): pass
    def capture(index, backend):
        calls.append((index, backend))
        return Capture()
    monkeypatch.setattr(cv2, "VideoCapture", capture)
    manager = CameraManager()
    assert [(cam["index"], cam["name"]) for cam in manager.enumerate_cameras(usb_only=True)] == [(1, "USB Camera")]
    assert calls == [(1, cv2.CAP_DSHOW)]
    assert manager.open(1)["name"] == "USB Camera"
    manager.close()


def test_windows_profile_query_parses_order_and_encoded_command(monkeypatch):
    profiles = [{"index": 0, "name": "USB Camera"}, {"index": 1, "name": "HP HD Camera"}]
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(profiles))
    monkeypatch.setattr("app.camera.windows_devices.subprocess.run", run)
    assert windows_camera_profiles() == profiles
    assert "-EncodedCommand" in calls[0]


def test_windows_discovery_failure_does_not_guess_index(monkeypatch):
    def fail(*args, **kwargs): raise OSError("Discovery failed")
    monkeypatch.setattr("app.camera.windows_devices.subprocess.run", fail)
    assert windows_camera_profiles() == []


@pytest.fixture
def camera_api(monkeypatch):
    all_cameras = [{"index": 0, "name": "HP HD Camera", "status": "CONNECTED"},
                   {"index": 1, "name": "USB Camera", "status": "CONNECTED"}]
    calls, current = [], {"connected": False}
    def open_camera(index, width, height, fps):
        calls.append((index, width, height, fps))
        current.update(connected=True, index=index, name="USB Camera", width=width, height=height, fps=fps)
        return dict(current)
    manager = SimpleNamespace(enumerate_cameras=lambda **kwargs: all_cameras, status=lambda: dict(current), open=open_camera)
    monkeypatch.setattr(main, "camera_manager", manager)
    monkeypatch.setattr(main, "load_config", lambda: {"camera": {"resolution": {"width": 1280, "height": 720}, "fps": 30}})
    return TestClient(main.app), calls, all_cameras


def test_api_lists_usb_only_and_blocks_builtin_even_with_override(camera_api):
    client, calls, cameras = camera_api
    assert [c["name"] for c in client.get("/api/cameras").json()["cameras"]] == ["USB Camera"]
    assert client.post("/api/camera/open", json={"index": 0, "allow_non_external": True}).status_code == 400
    assert not calls


def test_default_camera_uses_saved_resolution_and_reuses_open_connection(camera_api):
    client, calls, cameras = camera_api
    assert client.post("/api/camera/open-machine").status_code == 200
    assert calls == [(1, 1280, 720, 30)]
    assert client.post("/api/camera/open-machine").status_code == 200
    assert len(calls) == 1


def test_no_usb_camera_never_falls_back_to_builtin(camera_api):
    client, calls, cameras = camera_api
    cameras.pop()
    response = client.post("/api/camera/open-machine")
    assert response.status_code == 400
    assert "USB camera not found" in response.json()["detail"]
    assert not calls
