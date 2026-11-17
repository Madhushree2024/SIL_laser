import pytest
from shapely import affinity
from shapely.geometry import Polygon, box

from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate


def verify(result, sheath, spacing, clearance):
    usable = sheath.buffer(-clearance).buffer(3e-7) if clearance else sheath.buffer(3e-7)
    polygons = [Polygon(placement.polygon) for placement in result.placements]
    for index, polygon in enumerate(polygons):
        assert usable.covers(polygon)
        for other in polygons[:index]:
            assert polygon.intersection(other).area < 1e-6
            assert polygon.distance(other) >= spacing - 3e-7


def test_dense_search_fills_two_rows_instead_of_one_piece():
    sheath = box(0, 0, 106, 106)
    template = CuttingTemplate("50", "50 mm", 50, 50, quantity=4, priority=1)
    engine = NestingEngine()
    old = engine.nest(list(sheath.exterior.coords), [template], spacing_mm=2, edge_clearance_mm=2, strategy="maximum_pieces")
    dense = engine.nest(list(sheath.exterior.coords), [template], spacing_mm=2, edge_clearance_mm=2, strategy="accurate")
    assert len(dense.placements) == 4
    assert len(dense.placements) > len(old.placements)
    verify(dense, sheath, 2, 2)


def test_fractional_exact_fit_preserves_dimensions():
    sheath = box(0, 0, 103.4, 53.2)
    template = CuttingTemplate("fractional", "50.2 mm", 50.2, 50.2, quantity=2)
    result = NestingEngine().nest(list(sheath.exterior.coords), [template], spacing_mm=0, edge_clearance_mm=1.5, strategy="accurate")
    assert len(result.placements) == 2
    for placement in result.placements:
        assert Polygon(placement.polygon).area == pytest.approx(50.2 ** 2)
    verify(result, sheath, 0, 1.5)


def test_circle_stays_a_circle_in_shared_mode():
    sheath = box(0, 0, 90, 90)
    template = CuttingTemplate("circle", "30 mm circle", 30, 30, shape="circle", quantity=3)
    result = NestingEngine().nest(list(sheath.exterior.coords), [template], spacing_mm=2, edge_clearance_mm=2, strategy="shared_side")
    assert len(result.placements) == 3
    for placement in result.placements:
        assert Polygon(placement.polygon).area == pytest.approx(template.geometry().area)
        assert len(placement.polygon) > 100
    verify(result, sheath, 2, 2)


def test_rotated_sheath_uses_allowed_angle():
    sheath = affinity.rotate(box(0, 0, 104, 34), 17, origin=(0, 0))
    template = CuttingTemplate("angled", "100 by 30", 100, 30, allowed_rotations=[17])
    result = NestingEngine().nest(list(sheath.exterior.coords), [template], spacing_mm=0, edge_clearance_mm=2, strategy="accurate")
    assert len(result.placements) == 1
    assert result.placements[0].rotation_deg == 17
    verify(result, sheath, 0, 2)


def test_concave_sheath_respects_edges_and_spacing():
    sheath = Polygon([[0, 0], [120, 0], [120, 40], [50, 40], [50, 100], [0, 100]])
    template = CuttingTemplate("square", "30 mm", 30, 30, quantity=8, allowed_rotations=[0,90])
    result = NestingEngine().nest(list(sheath.exterior.coords), [template], spacing_mm=3, edge_clearance_mm=2, strategy="accurate")
    assert len(result.placements) >= 3
    verify(result, sheath, 3, 2)


def test_dense_mode_keeps_user_priority():
    sheath = box(0, 0, 90, 90)
    preferred = CuttingTemplate("preferred", "80", 80, 80, priority=1)
    later = CuttingTemplate("later", "20", 20, 20, quantity=4, priority=2)
    result = NestingEngine().nest(list(sheath.exterior.coords), [later, preferred], edge_clearance_mm=1, strategy="accurate")
    assert [placement.template_id for placement in result.placements] == ["preferred"]


def test_shared_side_respects_positive_spacing():
    sheath = box(0, 0, 110, 60)
    template = CuttingTemplate("square", "50", 50, 50, quantity=2)
    result = NestingEngine().nest(list(sheath.exterior.coords), [template], spacing_mm=5, edge_clearance_mm=2, strategy="shared_side")
    assert len(result.placements) == 2
    verify(result, sheath, 5, 2)
