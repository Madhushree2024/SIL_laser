from __future__ import annotations

import cv2
import numpy as np

from .camera_calibration import CameraCalibrationProfile


def undistort(image: np.ndarray, profile: CameraCalibrationProfile | None) -> np.ndarray:
    if profile is None:
        return image
    camera_matrix = np.array(profile.camera_matrix, dtype=np.float32)
    height, width = image.shape[:2]
    if profile.image_width <= 0 or profile.image_height <= 0:
        return image
    original_aspect = profile.image_width / profile.image_height
    if abs(width / height - original_aspect) / original_aspect >= 0.15:
        return image
    camera_matrix[0, :] *= width / profile.image_width
    camera_matrix[1, :] *= height / profile.image_height
    coefficients = np.array(profile.distortion_coefficients, dtype=np.float32)
    return cv2.undistort(image, camera_matrix, coefficients)
