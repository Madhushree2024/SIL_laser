import cv2
import numpy as np
import pytest

from camera_aruco_coverage import analyze_frame, save_report, annotate_frame
from camera_aruco_coverage import main


def photo():
    image = np.full((600, 800), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    image[200:400, 300:500] = cv2.aruco.generateImageMarker(dictionary, 10, 200)
    return image


def test_overhead_plane_coverage_and_report(tmp_path):
    report = analyze_frame(photo(), marker_size_mm=100)
    scale = report["local_mm_per_pixel"]
    assert scale == pytest.approx(0.5, abs=0.01)
    assert report["coverage_bounds_mm"]["width"] == pytest.approx(799 * scale, abs=0.1)
    assert report["coverage_bounds_mm"]["height"] == pytest.approx(599 * scale, abs=0.1)
    assert report["visible_plane_area_mm2"] == pytest.approx(799 * 599 * scale ** 2, rel=0.001)
    save_report(tmp_path, annotate_frame(photo(), report), report)
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_marker_missing_clears_measurement():
    with pytest.raises(ValueError, match="detected IDs"):
        analyze_frame(np.full((600, 800), 255, np.uint8))


def test_saved_photo_command_line(tmp_path, monkeypatch, capsys):
    path = tmp_path / "marker.png"
    cv2.imwrite(str(path), photo())
    output = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["camera_aruco_coverage.py", "--image", str(path), "--marker-size-mm", "100", "--output-dir", str(output)])
    assert main() == 0
    assert "Estimated plane bounds" in capsys.readouterr().out
    assert len(list(output.glob("*.json"))) == 1


def test_tilted_plane_coverage_is_quadrilateral():
    image = photo()
    src = np.array([[0, 0], [799, 0], [799, 599], [0, 599]], np.float32)
    dst = np.array([[45, 15], [740, 60], [790, 575], [15, 550]], np.float32)
    warp = cv2.getPerspectiveTransform(src, dst)
    tilted = cv2.warpPerspective(image, warp, (800, 600), borderValue=255)
    report = analyze_frame(tilted, 100)
    edges = report["frame_edges_mm"]
    assert abs(edges["top"] - edges["bottom"]) > 5
    assert report["visible_plane_area_mm2"] > 0
    # A physical point's marker-plane coordinates survive perspective tilt.
    center_pixel = cv2.perspectiveTransform(np.array([[[399.5, 299.5]]], np.float32), warp)
    center_mm = cv2.perspectiveTransform(center_pixel, np.array(report["homography"])).reshape(2)
    assert center_mm == pytest.approx([50, 50], abs=0.5)


@pytest.mark.parametrize("size", [0, -1, float("nan"), float("inf")])
def test_invalid_marker_sizes(size):
    with pytest.raises(ValueError, match="positive"):
        analyze_frame(photo(), size)
