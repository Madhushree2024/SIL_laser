import cv2
import pytest
from fastapi.testclient import TestClient
from app.camera.camera_manager import CameraManager
from app.camera.camera_controls import set_control
import app.main as main


class Capture:
    def __init__(self, accepted=True):
        self.calls = []
        self.accepted = accepted
    def isOpened(self): return True
    def set(self, prop, value):
        self.calls.append((prop, value))
        return self.accepted


@pytest.mark.parametrize('backend,enabled,expected', [('DirectShow',1,1),('DirectShow',0,0),('Media Foundation',1,1),('V4L2',1,3),('V4L2',0,1)])
def test_auto_exposure_uses_backend_flags(backend, enabled, expected):
    manager = CameraManager()
    manager._capture = Capture()
    manager._backend = backend
    assert manager.set_control('auto_exposure', enabled)
    assert manager._capture.calls == [(cv2.CAP_PROP_AUTO_EXPOSURE, expected)]


def test_rejected_or_nonfinite_control_is_not_reported_as_applied():
    cap = Capture(False)
    assert not set_control(cap, 'exposure', -6)
    cap.calls.clear()
    assert not set_control(cap, 'brightness', float('nan'))
    assert not cap.calls


@pytest.mark.parametrize('accepted', [True, False])
def test_camera_control_resets_preview_only_when_driver_accepts(monkeypatch, accepted):
    resets = []
    monkeypatch.setattr(main.camera_manager, 'set_control', lambda name, value: accepted)
    monkeypatch.setattr(main.live_green_preview, 'reset', lambda: resets.append(True))
    response = TestClient(main.app).post('/api/camera/control', json={'name':'auto_exposure','value':1})
    assert response.status_code == 200
    assert response.json() == {'ok':accepted}
    assert len(resets) == int(accepted)
