import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.calibration.aruco_bed import BedCalibrationRequest, profile_from_bed_markers
from app.calibration.aruco_scale import aruco_dictionary
from app.calibration.scale_calibration import pixel_points_to_mm, load_profile


def marker_photo():
    image = np.full((600, 800), 255, np.uint8)
    positions = [(80, 70), (620, 70), (620, 430), (80, 430)]
    for marker_id, (x, y) in zip([10, 11, 12, 13], positions):
        image[y:y + 100, x:x + 100] = cv2.aruco.generateImageMarker(aruco_dictionary(), marker_id, 100)
    return image


def request():
    # Actual laser coordinates, with Y increasing upward, independent of ID order.
    return BedCalibrationRequest(markers=[
        dict(marker_id=12, x_mm=560, y_mm=30),
        dict(marker_id=10, x_mm=20, y_mm=390),
        dict(marker_id=13, x_mm=20, y_mm=30),
        dict(marker_id=11, x_mm=560, y_mm=390),
    ])


def test_detect_four_markers_maps_centers_and_interior_point():
    profile = profile_from_bed_markers(marker_photo(), request())
    assert profile.source == "aruco_bed"
    assert profile.bed_markers[0]["marker_id"] == 12
    points = pixel_points_to_mm([[129.5, 119.5], [669.5, 479.5], [399.5, 299.5]], profile)
    for actual, expected in zip(points, [[20, 390], [560, 30], [290, 210]]):
        assert actual == pytest.approx(expected, abs=0.2)


def test_perspective_photo_maps_interior_point():
    source = np.array([[0, 0], [799, 0], [799, 599], [0, 599]], np.float32)
    target = np.array([[35, 25], [770, 60], [730, 570], [65, 535]], np.float32)
    warp = cv2.getPerspectiveTransform(source, target)
    image = cv2.warpPerspective(marker_photo(), warp, (800, 600), borderValue=255)
    profile = profile_from_bed_markers(image, request())
    pixel = cv2.perspectiveTransform(np.array([[[399.5, 299.5]]], np.float32), warp).reshape(-1, 2).tolist()
    assert pixel_points_to_mm(pixel, profile)[0] == pytest.approx([290, 210], abs=1.0)


def test_missing_marker_does_not_calibrate():
    image = marker_photo()
    image[430:530, 620:720] = 255
    with pytest.raises(ValueError, match="ID 12"):
        profile_from_bed_markers(image, request())


def test_duplicate_ids_and_degenerate_bed_coordinates_rejected():
    markers = request().model_dump()["markers"]
    markers[0]["marker_id"] = markers[1]["marker_id"]
    with pytest.raises(ValidationError, match="different"):
        BedCalibrationRequest(markers=markers)
    markers = request().model_dump()["markers"]
    for marker in markers:
        marker["y_mm"] = 0
    with pytest.raises(ValidationError, match="corners"):
        BedCalibrationRequest(markers=markers)


def test_bad_corner_correspondences_rejected():
    markers = request().model_dump()["markers"]
    markers[0]["marker_id"], markers[1]["marker_id"] = markers[1]["marker_id"], markers[0]["marker_id"]
    # Swap adjacent positions, making the ordered bed quadrilateral self-crossing.
    markers[0]["marker_id"], markers[3]["marker_id"] = markers[3]["marker_id"], markers[0]["marker_id"]
    with pytest.raises(ValueError, match="corners"):
        profile_from_bed_markers(marker_photo(), BedCalibrationRequest(markers=markers))


def test_bed_profile_persists_and_single_marker_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "_correct_camera_frame", lambda image: image)
    image_path = tmp_path / "bed.png"
    cv2.imwrite(str(image_path), marker_photo())
    monkeypatch.setattr(main, "STATE", {"project_dir": str(tmp_path), "captured_image": str(image_path), "nesting_result": {"placements": []}})
    client = TestClient(main.app)
    client.post("/api/laser/alignment", json={"enabled": True, "laser_x_mm": 123})
    response = client.post("/api/calibration/aruco-bed", json=request().model_dump())
    assert response.status_code == 200, response.text
    assert main.STATE["nesting_result"] is None
    assert not main.get_laser_alignment()["enabled"]
    saved = load_profile(tmp_path / "data" / "calibration" / "scale_profile.json")
    assert saved.source == "aruco_bed"
    assert saved.bed_markers == request().model_dump()["markers"]
    # Detection must use the saved bed homography even with configured marker 10 visible.
    monkeypatch.setattr(main, "load_config", lambda: {"calibration": {"aruco": {"enabled": True, "marker_id": 10, "marker_size_mm": 97}}, "vision": {"min_area_px": 100}})
    monkeypatch.setattr(main, "detect_aruco_scale_profile", lambda *args, **kwargs: pytest.fail("Single-marker scale must not replace laser bed coordinates"))
    from types import SimpleNamespace
    def fake_detect(image, project_dir, **kwargs):
        assert kwargs["scale_profile"].source == "aruco_bed"
        from app.vision.sheath_detection import DetectionResult
        return DetectionResult(pixel_polygon=[[200, 200], [250, 200], [250, 250], [200, 250]], mm_polygon=[[90, 310], [140, 310], [140, 260], [90, 260]], mask_path=None, overlay_path=None)
    monkeypatch.setattr(main, "detector", SimpleNamespace(detect=fake_detect))
    assert client.post("/api/detect").json()["scale_source"] == "aruco_bed"
