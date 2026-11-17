from __future__ import annotations

from dataclasses import dataclass
import math

import cv2


CONTROL_MAP = {
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "hue": cv2.CAP_PROP_HUE,
    "gain": cv2.CAP_PROP_GAIN,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "white_balance": cv2.CAP_PROP_WB_TEMPERATURE,
    "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
    "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
}


@dataclass
class CameraControl:
    name: str
    value: float
    supported: bool


def read_controls(capture: cv2.VideoCapture) -> list[CameraControl]:
    controls: list[CameraControl] = []
    for name, prop in CONTROL_MAP.items():
        value = float(capture.get(prop))
        controls.append(CameraControl(name=name, value=value, supported=value != -1))
    return controls


def set_control(capture: cv2.VideoCapture, name: str, value: float) -> bool:
    prop = CONTROL_MAP.get(name)
    if prop is None or not math.isfinite(value):
        return False
    return bool(capture.set(prop, value))
