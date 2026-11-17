import cv2
import numpy as np

from app.calibration.aruco_scale import aruco_dictionary, detect_aruco_scale_profile


def test_detect_aruco_scale_profile_from_synthetic_marker() -> None:
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 0, 120)
    image = np.full((360, 480), 255, dtype=np.uint8)
    image[100:220, 180:300] = marker
    bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    profile = detect_aruco_scale_profile(bgr, marker_size_mm=100, dictionary_name="DICT_4X4_50", marker_id=0)

    assert profile is not None
    assert profile.source == "aruco"
    assert profile.marker_id == 0
    assert 0.7 < profile.mm_per_pixel < 0.95


def test_large_97mm_marker_scales_to_physical_size():
    image = np.full((400, 500), 255, np.uint8)
    image[100:300, 150:350] = cv2.aruco.generateImageMarker(aruco_dictionary(), 10, 200)
    profile = detect_aruco_scale_profile(image, marker_size_mm=97, marker_id=10)
    assert profile is not None
    assert profile.marker_size_mm == 97
    corners = np.array([[[150, 100], [349, 100], [349, 299], [150, 299]]], np.float32)
    mm = cv2.perspectiveTransform(corners, np.array(profile.homography))
    assert abs(np.linalg.norm(mm[0, 1] - mm[0, 0]) - 97) < 1
