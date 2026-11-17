import copy
import json
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.laser.coordinate_alignment import CoordinateAlignment, align_layout

BED = [[-50, -50], [500, -50], [500, 500], [-50, 500]]
SHAPES = [
    {'template_id': 'left', 'polygon': [[0, 10], [100, 10], [100, 110], [0, 110]]},
    {'template_id': 'right', 'polygon': [[100, 20], [200, 20], [200, 120], [100, 120]]},
]


def policy(**kwargs):
    return CoordinateAlignment(enabled=True, reference='rightmost_template_top_right', flip_y=True, **kwargs)


def test_circled_vertex_is_origin_even_when_left_template_is_higher_and_bed_is_larger():
    before = copy.deepcopy(SHAPES)
    output = align_layout(BED, SHAPES, policy())
    assert output['design_reference_mm'] == [200, 20]
    assert output['placements'][1]['polygon'][1] == [0, 0]
    assert output['placements'][0]['polygon'][0] == [-200, 10]
    assert output['placements'][1]['polygon'][2] == [0, -100]
    assert SHAPES == before


def test_origin_recomputed_for_every_new_layout_and_material_island():
    moved = copy.deepcopy(SHAPES)
    for point in moved[1]['polygon']:
        point[0] += 70
        point[1] += 30
    output = align_layout(BED, moved, policy(), sheaths=[BED, [[900, 900], [1000, 900], [1000, 1000]]])
    assert output['design_reference_mm'] == [270, 50]
    assert output['placements'][1]['polygon'][1] == [0, 0]


def test_nearly_vertical_edges_and_vertex_order_have_stable_top_endpoint():
    shape = [{'polygon': [[200, 120], [100, 120], [100, 20], [200 - 1e-10, 20]]}]
    output = align_layout(BED, shape, policy())
    assert output['design_reference_mm'] == [200 - 1e-10, 20]
    assert output['placements'][0]['polygon'][-1] == pytest.approx([0, 0])


def test_rotated_polygon_uses_real_cut_vertex_not_empty_bounding_box_corner():
    shape = [{'polygon': [[10, 0], [20, 10], [10, 20], [0, 10]]}]
    output = align_layout(BED, shape, policy())
    assert output['design_reference_mm'] == [20, 10]
    assert output['placements'][0]['polygon'][1] == [0, 0]


def test_no_templates_is_explicit_error_and_disabled_alignment_keeps_geometry():
    with pytest.raises(ValueError, match='at least one template'):
        align_layout(BED, [], policy())
    disabled = CoordinateAlignment(enabled=False, reference='rightmost_template_top_right')
    assert align_layout(BED, [], disabled)['sheath'] == BED


@pytest.mark.parametrize('shared', [False, True])
def test_real_dxf_preview_and_persisted_defaults_use_identical_dynamic_origin(tmp_path, monkeypatch, shared):
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    nesting = {'placements': copy.deepcopy(SHAPES), 'settings': {'strategy': 'common_line' if shared else 'accurate'}}
    monkeypatch.setattr(main, 'STATE', {'project_dir': str(tmp_path), 'sheath_polygon': BED, 'nesting_result': nesting})
    monkeypatch.setattr(main, '_handoff_dxf', lambda *args: None)
    client = TestClient(main.app)
    assert client.post('/api/laser/alignment', json=policy().model_dump()).status_code == 200
    preview = client.post('/api/laser/alignment/preview', json=policy().model_dump())
    assert preview.status_code == 200
    response = client.post('/api/export', json={'format': 'dxf', 'include_sheath': False})
    assert response.status_code == 200, response.text
    vector = Path(response.json()['path'])
    metadata = json.loads(vector.with_suffix('.alignment.json').read_text())
    assert metadata['design_reference_mm'] == [200, 20]
    assert metadata['matrix'] == preview.json()['matrix']
    entities = list(ezdxf.readfile(vector).modelspace())
    vertices = [point for entity in entities for point in entity.get_points('xy')]
    assert any(abs(x) < 1e-8 and abs(y) < 1e-8 for x, y in vertices)
    assert max(x for x, y in vertices) == pytest.approx(0)
    assert all(entity.dxf.layer == 'CUT' for entity in entities)
    assert main.STATE['nesting_result']['placements'] == SHAPES
    # No changes to the SVG geometry or the source measurement frame.
    svg = client.post('/api/export', json={'format': 'svg', 'include_sheath': False})
    assert svg.status_code == 200
    assert svg.json()['alignment'] is None


def test_empty_layout_preview_reports_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'STATE', {'sheath_polygon': BED, 'nesting_result': {'placements': []}})
    response = TestClient(main.app).post('/api/laser/alignment/preview', json=policy().model_dump())
    assert response.status_code == 400
    assert 'at least one template' in response.json()['detail']
