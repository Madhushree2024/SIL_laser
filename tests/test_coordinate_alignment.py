import copy
import json

import ezdxf
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.laser.coordinate_alignment import CoordinateAlignment, align_layout


SHEATH = [[-20, -10], [80, -10], [80, 40], [-20, 40]]
PLACEMENTS = [{"template_id": "a", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}]


def test_disabled_alignment_preserves_negative_design_coordinates():
    result = align_layout(SHEATH, PLACEMENTS, CoordinateAlignment(laser_x_mm=300, flip_y=True))
    assert result["sheath"] == SHEATH
    assert result["placements"] == PLACEMENTS


def test_reference_maps_to_laser_after_reflection_and_rotation():
    original = copy.deepcopy(PLACEMENTS)
    result = align_layout(SHEATH, PLACEMENTS, CoordinateAlignment(enabled=True, reference="custom", design_x_mm=10, design_y_mm=10, laser_x_mm=100, laser_y_mm=200, flip_y=True, rotation_deg=90))
    polygon = result["placements"][0]["polygon"]
    assert polygon[2] == pytest.approx([100, 200])
    assert polygon[1] == pytest.approx([90, 200])
    assert polygon[0] == pytest.approx([90, 190])
    assert PLACEMENTS == original


@pytest.mark.parametrize("reference, expected", [("min_x_min_y", [-20, -10]), ("min_x_max_y", [-20, 40]), ("max_x_min_y", [80, -10]), ("max_x_max_y", [80, 40]), ("center", [30, 15])])
def test_bounds_references(reference, expected):
    result = align_layout(SHEATH, PLACEMENTS, CoordinateAlignment(enabled=True, reference=reference))
    assert result["design_reference_mm"] == expected
    assert result["laser_reference_mm"] == [0, 0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_alignment_rejected(value):
    with pytest.raises(ValidationError):
        CoordinateAlignment(laser_x_mm=value)


def test_saved_alignment_preview_and_export_agree(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "STATE", {"project_dir": str(tmp_path), "sheath_polygon": SHEATH, "nesting_result": {"placements": PLACEMENTS}})
    client = TestClient(main.app)
    alignment = dict(enabled=True, reference="min_x_max_y", laser_x_mm=50, laser_y_mm=60, flip_y=True)
    assert client.post("/api/laser/alignment", json=alignment).status_code == 200
    assert client.get("/api/laser/alignment").json()["laser_x_mm"] == 50
    preview = client.post("/api/laser/alignment/preview", json=alignment).json()
    response = client.post("/api/export", json={"format": "dxf", "include_sheath": False})
    assert response.status_code == 200
    from pathlib import Path
    path = Path(response.json()["path"])
    doc = ezdxf.readfile(path)
    assert doc.units == ezdxf.units.MM
    entities = list(doc.modelspace())
    assert len(entities) == 1
    assert entities[0].dxf.layer == "CUT"
    for actual, expected in zip(entities[0].get_points("xy"), preview["placements"][0]["polygon"]):
        assert actual == pytest.approx(expected)
    assert json.loads(path.with_suffix(".alignment.json").read_text())["matrix"] == preview["matrix"]
    assert main.STATE["sheath_polygon"] == SHEATH
    # Request-specific settings override persisted machine defaults.
    exported = client.post("/api/export", json={"format": "dxf", "alignment": {"enabled": False}}).json()
    assert exported["alignment"]["matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_preview_requires_layout_and_api_rejects_bad_coordinates(monkeypatch):
    monkeypatch.setattr(main, "STATE", {})
    client = TestClient(main.app)
    assert client.post("/api/laser/alignment/preview", json={}).status_code == 400
    assert client.post("/api/laser/alignment", json={"laser_x_mm": "NaN"}).status_code == 422


@pytest.mark.parametrize('format', ['svg','dxf'])
def test_default_export_contains_only_template_cut_paths(tmp_path,monkeypatch,format):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(main,'ROOT',tmp_path)
    monkeypatch.setattr(main,'STATE',{'project_dir':str(tmp_path),'sheath_polygon':SHEATH,'nesting_result':{'placements':PLACEMENTS}})
    client=TestClient(main.app)
    response=client.post('/api/export',json={'format':format})
    assert response.status_code==200,response.text
    path=Path(response.json()['path'])
    if format=='dxf':
        entities=list(ezdxf.readfile(path).modelspace())
        assert len(entities)==1
        assert entities[0].dxf.layer=='CUT'
        for actual,expected in zip(entities[0].get_points('xy'),PLACEMENTS[0]['polygon']):
            assert actual==pytest.approx(expected)
    else:
        paths=ET.parse(path).getroot().findall('.//{http://www.w3.org/2000/svg}path')
        assert len(paths)==1
        assert paths[0].attrib['id'].startswith('CUT_')
    assert main.STATE['sheath_polygon']==SHEATH


def test_top_right_cad_origin_preserves_visual_orientation_and_shared_paths(tmp_path):
    from app.export.dxf_export import export_dxf
    from shapely.geometry import Polygon, LineString
    import shapely
    pieces = [
        {"template_id": "large", "sheath_id": "s1", "polygon": [[100, 30], [140, 30], [140, 90], [100, 90]]},
        {"template_id": "small", "sheath_id": "s1", "polygon": [[140, 50], [160, 50], [160, 70], [140, 70]]},
    ]
    policy = CoordinateAlignment(enabled=True, reference="custom", design_x_mm=1250, design_y_mm=0, flip_x=False, flip_y=True)
    result = align_layout([[0, 0], [1250, 0], [1250, 560], [0, 560]], pieces, policy)
    assert result["sheath"][1] == [0, 0]  # ID 12 remains zero.
    assert result["matrix"] == [[1, 0, -1250], [0, -1, 0], [0, 0, 1]]
    path = export_dxf(tmp_path / "top-right.dxf", result["sheath"], result["placements"], include_sheath=False, shared_cut=True)
    lines = [LineString(list(e.get_points("xy"))) for e in ezdxf.readfile(path).modelspace()]
    reference = shapely.union_all([Polygon(p["polygon"]).boundary for p in result["placements"]])
    assert sum(line.length for line in lines) == pytest.approx(reference.length)
    assert shapely.union_all(lines).equals(reference)
    # CAD displays +Y upward; reversing only Y preserves the preview's downwards Y.
    for source, target in zip(pieces, result["placements"]):
        for (x, y), (cad_x, cad_y) in zip(source["polygon"], target["polygon"]):
            assert cad_x + 1250 == pytest.approx(x)
            assert -cad_y == pytest.approx(y)


def test_template_box_origin_ignores_sheath_and_uses_all_template_extents():
    # The rightmost template is lower; its own top corner is NOT the box top-right.
    pieces=[{'template_id':'upper','polygon':[[-90,-20],[-50,-20],[-50,-10],[-90,-10]]},
            {'template_id':'right','polygon':[[-40,-70],[-10,-70],[-10,-40],[-40,-40]]}]
    original=copy.deepcopy(pieces)
    result=align_layout([[-1050,0],[0,0],[0,-510],[-1050,-510]],pieces,
                        CoordinateAlignment(enabled=True,reference='templates_top_right'))
    assert result['design_reference_mm']==[-10,-10]
    assert result['matrix']==[[1,0,10],[0,1,10],[0,0,1]]
    points=[pt for p in result['placements'] for pt in p['polygon']]
    assert max(p[0] for p in points)==0 and max(p[1] for p in points)==0
    assert [0,0] not in points # A bounding-box corner need not be a real template vertex.
    assert pieces==original
    for a,b in zip(pieces,result['placements']):
        from shapely.geometry import Polygon
        assert Polygon(a['polygon']).area==pytest.approx(Polygon(b['polygon']).area)
    with pytest.raises(ValueError,match='at least one template'):
        align_layout(SHEATH,[],CoordinateAlignment(enabled=True,reference='templates_top_right'))
