import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from app.nesting.precision_nesting import _valid_candidates, TOLERANCE


@pytest.mark.parametrize("spacing", [0, 1e-8, 2, 5])
@pytest.mark.parametrize("shape", ["rectangle", "circle", "rotated"])
def test_candidate_filter_matches_exhaustive_geometry_checks(spacing, shape):
    base = Point(0, 0).buffer(7, quad_segs=64) if shape == "circle" else box(-9, -4, 9, 4)
    if shape == "rotated": base = affinity.rotate(base, 17)
    usable = Polygon([[0, 0], [100, 0], [100, 35], [65, 35], [65, 95], [0, 95]])
    placed = [affinity.translate(base, 20, 20), affinity.translate(base, 47, 40), affinity.translate(base, 40, 70)]
    centers = np.random.default_rng(17).uniform(-10, 110, (600, 2))
    geometries, actual = _valid_candidates(base, centers, usable, placed, spacing)
    expected = np.array([usable.covers(candidate) and all(
        candidate.distance(other) >= spacing - TOLERANCE if spacing > 0
        else candidate.intersection(other).area <= TOLERANCE
        for other in placed) for candidate in geometries])
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("spacing", [0, 2])
def test_shared_edges_and_spacing_thresholds_remain_unchanged(spacing):
    base = box(0, 0, 10, 10)
    centers = np.array([[10 + spacing, 0], [10 + spacing - 1e-8, 0], [10 + spacing - 1e-5, 0], [0, 0], [50, 0]])
    geometries, actual = _valid_candidates(base, centers, box(-1, -1, 100, 100), [base], spacing)
    expected = [candidate.distance(base) >= spacing - TOLERANCE if spacing > 0 else candidate.intersection(base).area <= TOLERANCE for candidate in geometries]
    np.testing.assert_array_equal(actual, expected)


def test_distant_and_intersecting_candidates_skip_expensive_distance(monkeypatch):
    base = Point(5, 5).buffer(5, quad_segs=64)
    calls = []
    distance = shapely.distance
    def record(geometries, other):
        calls.append(len(geometries))
        return distance(geometries, other)
    monkeypatch.setattr(shapely, "distance", record)
    _, valid = _valid_candidates(base, np.array([[0, 0], [50, 0], [11, 0]]), box(-1, -1, 100, 100), [base], 2)
    assert valid.tolist() == [False, True, False]
    assert calls == [1]


def test_full_containment_does_not_allow_crossing_a_material_hole():
    usable = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)], holes=[[(40, 40), (60, 40), (60, 60), (40, 60)]])
    _, valid = _valid_candidates(box(-20, -20, 20, 20), np.array([[50, 50], [20, 20]]), usable, [], 0)
    assert valid.tolist() == [False, True]
