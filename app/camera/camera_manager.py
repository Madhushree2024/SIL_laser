from __future__ import annotations

import platform
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2

from .camera_controls import read_controls, set_control
from .windows_devices import windows_camera_profiles, is_usb_camera


@dataclass
class CameraInfo:
    index: int
    name: str
    status: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    backend: str = "OpenCV"
    model_id: str | None = None
    unique_id: str | None = None
    device_path: str | None = None


class CameraManager:
    def __init__(self) -> None:
        self._capture: cv2.VideoCapture | None = None
        self._index: int | None = None
        self._name: str | None = None
        self._backend = "OpenCV"
        self._lock = threading.Lock()
        self._profiles: dict[int, dict] = {}
        self._usb_only = False

    @staticmethod
    def _backends() -> list[tuple[int, str]]:
        system = platform.system()
        if system == "Windows":
            return [(cv2.CAP_DSHOW, "DirectShow"), (cv2.CAP_MSMF, "Media Foundation")]
        if system == "Darwin":
            return [(cv2.CAP_AVFOUNDATION, "AVFoundation")]
        return [(cv2.CAP_V4L2, "V4L2"), (cv2.CAP_ANY, "OpenCV")]

    def _open_capture(self, index: int, probe: bool = False):
        backends = self._backends()
        # DirectShow identity indexes must not be reused with a differently ordered backend.
        if self._usb_only and platform.system() == "Windows":
            backends = [(cv2.CAP_DSHOW, "DirectShow")]
        for backend, name in backends:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened() and (not probe or cap.read()[0]):
                return cap, name
            cap.release()
        return None, "OpenCV"

    def enumerate_cameras(self, max_devices: int = 8, usb_only: bool = False) -> list[dict[str, Any]]:
        self._usb_only = usb_only
        profiles = windows_camera_profiles() if platform.system() == "Windows" else self._macos_camera_profiles()
        self._profiles = {profile.get("index", i): profile for i, profile in enumerate(profiles)}
        cameras: list[CameraInfo] = []
        for index in range(max_devices):
            profile = self._profiles.get(index, {})
            if usb_only and not is_usb_camera(profile):
                continue
            if index == self._index and self._capture is not None and self._capture.isOpened():
                cameras.append(CameraInfo(
                    index=index, name=self._name or f"Camera {index}", status="CONNECTED",
                    width=int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                    height=int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                    fps=max(0.0, float(self._capture.get(cv2.CAP_PROP_FPS) or 0)), backend=self._backend,
                    device_path=profile.get("device_path"),
                ))
                continue
            cap, backend = self._open_capture(index, probe=True)
            if cap is None:
                continue
            info = CameraInfo(
                index=index,
                name=profile.get("name") or f"Camera {index}",
                status="CONNECTED",
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                fps=max(0.0, float(cap.get(cv2.CAP_PROP_FPS) or 0)),
                backend=backend,
                model_id=profile.get("model_id"),
                unique_id=profile.get("unique_id"),
                device_path=profile.get("device_path"),
            )
            cameras.append(info)
            cap.release()
        return [asdict(camera) for camera in cameras]

    def open(self, index: int, width: int | None = None, height: int | None = None, fps: int | None = None) -> dict[str, Any]:
        with self._lock:
            self.close()
            cap, backend = self._open_capture(index, probe=True)
            if cap is None:
                raise RuntimeError(f"Unable to open camera index {index}")
            if width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            if height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if fps:
                cap.set(cv2.CAP_PROP_FPS, fps)
            profiles = self._profiles
            if index not in profiles and platform.system() == "Darwin":
                profiles = dict(enumerate(self._macos_camera_profiles()))
            self._capture = cap
            self._backend = backend
            self._index = index
            self._name = profiles.get(index, {}).get("name") or f"Camera {index}"
            return self.status()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        self._index = None
        self._name = None

    def status(self) -> dict[str, Any]:
        cap = self._capture
        if cap is None or not cap.isOpened():
            return {"connected": False}
        return {
            "connected": True,
            "index": self._index,
            "name": self._name,
            "backend": self._backend,
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            "fps": max(0.0, float(cap.get(cv2.CAP_PROP_FPS) or 0)),
            "controls": [asdict(control) for control in read_controls(cap)],
        }

    def set_control(self, name: str, value: float) -> bool:
        with self._lock:
            if self._capture is None or not self._capture.isOpened():
                raise RuntimeError("Camera is not open")
            # Linux V4L2 uses 3/1; Windows DirectShow and MSMF use 1/0.
            if name == "auto_exposure":
                enabled = value >= .5
                value = (3.0 if enabled else 1.0) if self._backend == "V4L2" else (1.0 if enabled else 0.0)
            return set_control(self._capture, name, value)

    def capture_frame(self) -> Any:
        with self._lock:
            if self._capture is None or not self._capture.isOpened():
                raise RuntimeError("Camera is not open")
            ok, frame = self._capture.read()
            if not ok or frame is None:
                raise RuntimeError("Camera frame capture failed")
            return frame

    def save_frame(self, path: Path) -> Path:
        frame = self.capture_frame()
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), frame)
        return path

    def mjpeg_stream(self, transform=None):
        while True:
            frame = self.capture_frame()
            if transform is not None:
                frame = transform(frame)
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n"

    @staticmethod
    def _macos_camera_profiles() -> list[dict[str, str]]:
        if platform.system() != "Darwin":
            return []
        try:
            result = subprocess.run(
                ["system_profiler", "SPCameraDataType"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
        except Exception:
            return []
        profiles: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.endswith(":") and not stripped.startswith(("Model", "Unique", "Manufacturer")):
                name = stripped[:-1]
                if name and name != "Camera":
                    if current:
                        profiles.append(current)
                    current = {"name": name}
            elif current and stripped.startswith("Model ID:"):
                current["model_id"] = stripped.split(":", 1)[1].strip()
            elif current and stripped.startswith("Unique ID:"):
                current["unique_id"] = stripped.split(":", 1)[1].strip()
        if current:
            profiles.append(current)

        # OpenCV's AVFoundation numeric indexes do not always match
        # system_profiler order. On Macs with a UVC/IMX camera, the external
        # capture device commonly appears before the built-in MacBook camera.
        if any(CameraManager._profile_is_external(profile) for profile in profiles):
            external = [profile for profile in profiles if CameraManager._profile_is_external(profile)]
            builtin = [profile for profile in profiles if CameraManager._profile_is_builtin(profile)]
            continuity = [profile for profile in profiles if CameraManager._profile_is_continuity(profile)]
            other = [
                profile
                for profile in profiles
                if profile not in external and profile not in builtin and profile not in continuity
            ]
            return external + other + builtin + continuity
        return profiles

    @staticmethod
    def _profile_is_external(profile: dict[str, str]) -> bool:
        text = " ".join((profile.get("name", ""), profile.get("model_id", ""))).lower()
        return any(token in text for token in ("usb", "uvc", "imx", "waveshare"))

    @staticmethod
    def _profile_is_builtin(profile: dict[str, str]) -> bool:
        text = " ".join((profile.get("name", ""), profile.get("model_id", ""))).lower()
        return any(token in text for token in ("macbook", "facetime"))

    @staticmethod
    def _profile_is_continuity(profile: dict[str, str]) -> bool:
        text = " ".join((profile.get("name", ""), profile.get("model_id", ""))).lower()
        return any(token in text for token in ("iphone", "continuity"))
