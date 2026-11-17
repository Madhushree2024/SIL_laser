from pathlib import Path

from shapely.geometry import box

from app.export.svg_export import export_svg
from app.nesting.collision import collides, is_inside
from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate


def test_collision_spacing() -> None:
    first = box(0, 0, 10, 10)
    second = box(11, 0, 21, 10)
    assert not collides(second, [first], spacing=1)
    assert collides(second, [first], spacing=3)


def test_containment_with_edge_clearance() -> None:
    container = box(0, 0, 100, 100)
    assert is_inside(container, box(10, 10, 20, 20), edge_clearance=5)
    assert not is_inside(container, box(1, 1, 20, 20), edge_clearance=5)


def test_nesting_places_real_templates_inside_polygon() -> None:
    sheath = [[0, 0], [160, 0], [160, 120], [0, 120]]
    template = CuttingTemplate(
        id="50-square",
        name="50 square",
        width_mm=50,
        height_mm=50,
        shape="square",
        quantity=4,
        allowed_rotations=[0],
    )
    result = NestingEngine().nest(sheath, [template], spacing_mm=2, edge_clearance_mm=2)
    assert len(result.placements) >= 2
    assert result.utilization > 0


def test_shared_side_nesting_aligns_one_edge() -> None:
    sheath = [[0, 0], [500, 0], [500, 180], [0, 180]]
    templates = [
        CuttingTemplate(
            id=f"{size}-square",
            name=f"{size} square",
            width_mm=size,
            height_mm=size,
            shape="square",
            quantity=1,
            allowed_rotations=[0],
        )
        for size in [150, 120, 90, 60]
    ]
    result = NestingEngine().nest(sheath, templates, edge_clearance_mm=5, strategy="shared_side")
    assert len(result.placements) == 4
    top_edges = [min(point[1] for point in placement.polygon) for placement in result.placements]
    assert max(top_edges) - min(top_edges) < 1e-6


def test_svg_export_keeps_vector_paths(tmp_path: Path) -> None:
    path = export_svg(
        tmp_path / "layout.svg",
        [[0, 0], [100, 0], [100, 100], [0, 100]],
        [{"template_id": "a", "polygon": [[10, 10], [30, 10], [30, 30], [10, 30]]}],
    )
    text = path.read_text()
    assert "<path" in text
    assert "<image" not in text
