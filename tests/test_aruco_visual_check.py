import cv2
import numpy as np
import pytest

from aruco_visual_check import analyze_frame, annotate_frame, demo_frame


def test_known_dimensions_and_two_distances():
    report = analyze_frame(demo_frame(), 100)
    assert report["warning"] is None
    assert sorted(m["id"] for m in report["markers"]) == [0, 10]
    for marker in report["markers"]:
        assert marker["edges_mm"] == pytest.approx([100] * 4, abs=.5)
    pair = report["pairs"][0]
    assert pair["center_distance_mm"] == pytest.approx(250, abs=1)
    assert pair["gap_mm"] == pytest.approx(150, abs=1)
    assert len(pair["gap_endpoints_px"]) == 2
    assert set(pair["gap_edges"]) == {"left", "right"}
    assert annotate_frame(demo_frame(), report).shape[0] > 700


def test_tilt_preserves_plane_dimensions_and_distance():
    frame = demo_frame()
    transform = cv2.getPerspectiveTransform(
        np.array([[0, 0], [1099, 0], [1099, 699], [0, 699]], np.float32),
        np.array([[160, 60], [940, 100], [1070, 650], [20, 670]], np.float32))
    tilted = cv2.warpPerspective(frame, transform, (1100, 700), borderValue=(255, 255, 255))
    report = analyze_frame(tilted, 100)
    assert len(report["markers"]) == 2
    for marker in report["markers"]:
        assert marker["edges_mm"] == pytest.approx([100] * 4, abs=1.5)
    assert report["pairs"][0]["center_distance_mm"] == pytest.approx(250, abs=2)
    assert report["pairs"][0]["gap_mm"] == pytest.approx(150, abs=2)


def test_reference_missing_retains_pixels_but_no_mm():
    report = analyze_frame(demo_frame(), reference_id=9)
    assert "reference ID 9" in report["warning"]
    assert len(report["markers"]) == 2
    assert all(m["edges_mm"] is None for m in report["markers"])
    assert "gap_mm" not in report["pairs"][0]


def test_duplicate_ids_disable_mapping():
    frame = demo_frame()
    frame[250:450, 650:850] = frame[250:450, 150:350]
    report = analyze_frame(frame)
    assert "Duplicate IDs" in report["warning"]
    assert all(m["center_mm"] is None for m in report["markers"])


def test_blank_frame_does_not_keep_stale_measurements():
    report = analyze_frame(np.full((700, 1100, 3), 255, np.uint8))
    assert report["markers"] == []
    assert report["pairs"] == []


@pytest.mark.parametrize("size", [0, -1, float("inf"), float("nan")])
def test_invalid_size(size):
    with pytest.raises(ValueError, match="positive"):
        analyze_frame(demo_frame(), size)
