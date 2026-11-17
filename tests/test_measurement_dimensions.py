from math import hypot
import pytest
from shapely.geometry import Polygon
from shapely.affinity import rotate
from app.vision.measurements import polygon_measurements


@pytest.mark.parametrize("angle", [0, 10, 25, -20])
def test_aligned_dimensions_resist_rotation_without_changing_legacy_spans(angle):
    polygon = rotate(Polygon([(0, 0), (100, 0), (100, 60), (0, 60)]), angle, origin=(0, 0))
    result = polygon_measurements(list(polygon.exterior.coords)[:-1])
    assert result["aligned_width_mm"] == pytest.approx(100)
    assert result["aligned_height_mm"] == pytest.approx(60)
    x0, y0, x1, y1 = polygon.bounds
    assert result["width_mm"] == pytest.approx(x1 - x0)
    assert result["height_mm"] == pytest.approx(y1 - y0)
    assert result["area_mm2"] == pytest.approx(6000)
    assert result["area_m2"] == pytest.approx(.006)
    for key, expected in [("width_edge_mm", 100), ("height_edge_mm", 60)]:
        a, b = result[key]
        assert hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(expected)


def test_vertical_piece_uses_edge_closest_to_horizontal_for_width():
    result = polygon_measurements([(0, 0), (60, 0), (60, 100), (0, 100)])
    assert result["aligned_width_mm"] == pytest.approx(60)
    assert result["aligned_height_mm"] == pytest.approx(100)


def test_degenerate_boundary_retains_legacy_measurements_without_arrows():
    result = polygon_measurements([(0, 0), (10, 0), (20, 0)])
    assert result["width_mm"] == 20
    assert result["height_mm"] == 0
    assert "width_edge_mm" not in result
