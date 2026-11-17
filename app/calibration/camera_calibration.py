from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class CameraCalibrationProfile:
    camera_matrix: list[list[float]]
    distortion_coefficients: list[float]
    image_width: int
    image_height: int
    rms_error: float


def calibrate_from_checkerboards(
    image_paths: list[Path],
    checkerboard: tuple[int, int] = (9, 6),
    square_size_mm: float = 25.0,
) -> CameraCalibrationProfile:
    if len(checkerboard) != 2 or min(checkerboard) < 2 or square_size_mm <= 0:
        raise ValueError("Use at least 2 inner corners per axis and a positive square size")
    objp = np.zeros((checkerboard[0] * checkerboard[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0 : checkerboard[0], 0 : checkerboard[1]].T.reshape(-1, 2)
    objp *= square_size_mm
    object_points = []
    image_points = []
    image_size: tuple[int, int] | None = None

    for path in image_paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        current_size = (gray.shape[1], gray.shape[0])
        found, corners = cv2.findChessboardCorners(gray, checkerboard)
        if not found:
            continue
        if image_size is not None and current_size != image_size:
            raise ValueError("All checkerboard images must use the same resolution")
        image_size = current_size
        refined = cv2.cornerSubPix(
            gray,
            corners,
            (11, 11),
            (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
        )
        object_points.append(objp)
        image_points.append(refined)

    if image_size is None or len(object_points) < 3:
        raise RuntimeError("At least three checkerboard images with detected corners are required")

    rms, camera_matrix, distortion, _, _ = cv2.calibrateCamera(object_points, image_points, image_size, None, None)
    return CameraCalibrationProfile(
        camera_matrix=camera_matrix.tolist(),
        distortion_coefficients=distortion.ravel().tolist(),
        image_width=image_size[0],
        image_height=image_size[1],
        rms_error=float(rms),
    )


def save_profile(profile: CameraCalibrationProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), indent=2))


def load_profile(path: Path) -> CameraCalibrationProfile | None:
    if not path.exists():
        return None
    return CameraCalibrationProfile(**json.loads(path.read_text()))
