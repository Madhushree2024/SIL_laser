from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import HTTPException

import app.main as main
from app.calibration.aruco_scale import aruco_dictionary
from app.calibration.scale_calibration import load_profile


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "STATE", {"project_dir": str(tmp_path), "captured_image": None})
    monkeypatch.setattr(main, "_correct_camera_frame", lambda image: image)
    monkeypatch.setattr(main, "load_config", lambda: {"calibration": {"aruco": {"enabled": True, "marker_size_mm": 97, "marker_id": 10, "dictionary": "DICT_4X4_50"}}, "vision": {"min_area_px": 1000}})
    image = np.full((400, 500), 255, np.uint8)
    image[100:300, 150:350] = cv2.aruco.generateImageMarker(aruco_dictionary(), 10, 200)
    path = tmp_path / "marker.jpg"
    cv2.imwrite(str(path), image)
    main.STATE["captured_image"] = str(path)
    return tmp_path


def sheath(workspace, shape=(400, 500)):
    image = np.full((*shape, 3), 25, np.uint8)
    cv2.rectangle(image, (70, 60), (250, 220), (80, 170, 190), -1)
    path = workspace / "sheath.jpg"
    cv2.imwrite(str(path), image)
    main.STATE["captured_image"] = str(path)


def test_marker_scale_persists_and_measures_separate_sheath(workspace):
    result = main.save_scale_from_aruco_photo()
    assert result["scale"]["marker_size_mm"] == 97
    profile = load_profile(workspace / "data" / "calibration" / "scale_profile.json")
    assert profile.source == "saved_aruco"
    sheath(workspace)
    result = main.detect()
    assert result["scale_source"] == "saved_aruco"
    assert result["measurements"]["width_mm"] > 0
    pixel_width = max(p[0] for p in result["pixel_polygon"]) - min(p[0] for p in result["pixel_polygon"])
    assert abs(result["measurements"]["width_mm"] - pixel_width * profile.mm_per_pixel) < 1


def test_saved_scale_rejects_different_resolution(workspace):
    main.save_scale_from_aruco_photo()
    sheath(workspace, shape=(240, 320))
    with pytest.raises(HTTPException, match="resolution"):
        main.detect()


def test_bad_marker_photo_preserves_saved_scale(workspace):
    main.save_scale_from_aruco_photo()
    profile_path = workspace / "data" / "calibration" / "scale_profile.json"
    original = profile_path.read_bytes()
    sheath(workspace)
    with pytest.raises(HTTPException, match="not detected"):
        main.save_scale_from_aruco_photo()
    assert profile_path.read_bytes() == original
