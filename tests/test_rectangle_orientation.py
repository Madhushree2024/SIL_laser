import pytest
from shapely.geometry import Polygon, box
from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager


@pytest.mark.parametrize("strategy", ["accurate", "shared_side", "maximum_pieces", "best_fit", "first_fit", "maximum_utilization"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_locked_rectangle_orientation_reaches_nesting(strategy, rotation):
    template = CuttingTemplate("rect", "70 by 30", 70, 30, quantity=2, allowed_rotations=[rotation])
    result = NestingEngine().nest(list(box(0, 0, 160, 160).exterior.coords), [template], spacing_mm=2, edge_clearance_mm=2, strategy=strategy)
    assert len(result.placements) == 2
    for placement in result.placements:
        assert placement.rotation_deg == rotation
        x0, y0, x1, y1 = Polygon(placement.polygon).bounds
        assert x1 - x0 == pytest.approx(70 if rotation == 0 else 30)
        assert y1 - y0 == pytest.approx(30 if rotation == 0 else 70)


@pytest.mark.parametrize("rotation", [0, 90])
def test_locked_orientation_round_trips_existing_template_storage(tmp_path, rotation):
    manager = TemplateManager(tmp_path / "templates.json")
    template = CuttingTemplate("rect", "Locked plate", 70, 30, allowed_rotations=[rotation])
    manager.save([template])
    restored = TemplateManager(manager.path).list()[0]
    assert restored.allowed_rotations == [rotation]
    assert restored.width_mm == 70
    assert restored.height_mm == 30


@pytest.mark.parametrize("strategy", ["accurate", "shared_side"])
def test_vertical_only_cannot_be_turned_horizontal_to_fit(strategy):
    template = CuttingTemplate("rect", "Vertical plate", 70, 30, allowed_rotations=[90])
    result = NestingEngine().nest(list(box(0, 0, 80, 40).exterior.coords), [template], spacing_mm=0, edge_clearance_mm=0, strategy=strategy)
    assert result.placements == []


@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("format", ["svg", "dxf"])
def test_locked_direction_preserved_in_real_export(tmp_path, rotation, format):
    import re
    import xml.etree.ElementTree as ET
    import ezdxf
    from app.export.svg_export import export_svg
    from app.export.dxf_export import export_dxf
    sheath = list(box(0, 0, 100, 100).exterior.coords)
    template = CuttingTemplate("rect", "Plate", 70, 30, allowed_rotations=[rotation])
    result = NestingEngine().nest(sheath, [template], spacing_mm=0, edge_clearance_mm=2, strategy="accurate")
    placements = result.to_dict()["placements"]
    path = tmp_path / ("plate." + format)
    if format == "dxf":
        export_dxf(path, sheath, placements, include_sheath=False)
        entity = next(iter(ezdxf.readfile(path).modelspace().query('LWPOLYLINE[layer=="CUT"]')))
        points = list(entity.get_points("xy"))
    else:
        export_svg(path, sheath, placements, include_sheath=False)
        element = ET.parse(path).find('.//{http://www.w3.org/2000/svg}g[@id="CUT"]/{http://www.w3.org/2000/svg}path')
        numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", element.attrib["d"])]
        points = list(zip(numbers[::2], numbers[1::2]))
    x0, y0, x1, y1 = Polygon(points).bounds
    assert x1 - x0 == pytest.approx(70 if rotation == 0 else 30, abs=.002)
    assert y1 - y0 == pytest.approx(30 if rotation == 0 else 70, abs=.002)
