from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .scale_calibration import ScaleCalibrationProfile


ARUCO_DICTIONARIES = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
}


def aruco_dictionary(name: str = "DICT_4X4_50"):
    dictionary_id = ARUCO_DICTIONARIES.get(name, cv2.aruco.DICT_4X4_50)
    return cv2.aruco.getPredefinedDictionary(dictionary_id)


def detect_aruco_scale_profile(
    image: np.ndarray,
    marker_size_mm: float = 100.0,
    dictionary_name: str = "DICT_4X4_50",
    marker_id: int | None = None,
) -> ScaleCalibrationProfile | None:
    dictionary = aruco_dictionary(dictionary_name)
    detector = cv2.aruco.ArucoDetector(dictionary, robust_detector_parameters())
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None or len(corners) == 0:
        return None

    flat_ids = ids.flatten().tolist()
    selected_index = 0
    if marker_id is not None:
        if marker_id not in flat_ids:
            return None
        selected_index = flat_ids.index(marker_id)

    src = corners[selected_index].reshape(4, 2).astype(np.float32)
    dst = np.array(
        [[0, 0], [marker_size_mm, 0], [marker_size_mm, marker_size_mm], [0, marker_size_mm]],
        dtype=np.float32,
    )
    homography, _ = cv2.findHomography(src, dst)
    if homography is None:
        return None

    side_lengths = [
        np.linalg.norm(src[0] - src[1]),
        np.linalg.norm(src[1] - src[2]),
        np.linalg.norm(src[2] - src[3]),
        np.linalg.norm(src[3] - src[0]),
    ]
    mean_side_px = float(np.mean(side_lengths))
    if mean_side_px <= 0:
        return None

    image_h, image_w = image.shape[:2]
    image_corners = np.array([[[0, 0]], [[image_w, 0]], [[image_w, image_h]], [[0, image_h]]], dtype=np.float32)
    transformed = cv2.perspectiveTransform(image_corners, homography).reshape(4, 2)
    min_xy = transformed.min(axis=0)
    max_xy = transformed.max(axis=0)

    return ScaleCalibrationProfile(
        mm_per_pixel=marker_size_mm / mean_side_px,
        working_width_mm=float(max_xy[0] - min_xy[0]),
        working_height_mm=float(max_xy[1] - min_xy[1]),
        homography=homography.tolist(),
        image_width=image_w,
        image_height=image_h,
        source="aruco",
        marker_id=int(flat_ids[selected_index]),
        marker_size_mm=float(marker_size_mm),
    )


def draw_detected_markers(image: np.ndarray, dictionary_name: str = "DICT_4X4_50") -> np.ndarray:
    output = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary_name), robust_detector_parameters())
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is not None and len(corners):
        cv2.aruco.drawDetectedMarkers(output, corners, ids)
    return output


def robust_detector_parameters():
    params = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 101
    params.adaptiveThreshWinSizeStep = 10
    params.minMarkerPerimeterRate = 0.01
    params.maxMarkerPerimeterRate = 4.0
    params.polygonalApproxAccuracyRate = 0.08
    return params


def write_marker_png(
    path: Path,
    marker_id: int = 0,
    side_pixels: int = 1000,
    dictionary_name: str = "DICT_4X4_50",
) -> Path:
    marker = cv2.aruco.generateImageMarker(aruco_dictionary(dictionary_name), marker_id, side_pixels)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), marker)
    return path
