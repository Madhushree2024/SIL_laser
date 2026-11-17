"""Production path with real marker detection, measurement, nesting, and DXF parsing.

Camera frames are synthetic. The external LaserCAD process is isolated.
"""
import json
from pathlib import Path
import cv2
import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon
import app.main as main
from app.laser.bed_limits import validate_cut_bounds, validate_bed_settings
from test_automated_workflow import workspace, payload, finish


CONFIG = {
    'workspace': {'width_mm': 900, 'height_mm': 680, 'enforce_bed_bounds': True},
    'automation': {'refresh_workspace_calibration': True},
    'vision': {'min_area_px': 1000},
    'lasercad': {'auto_open_dxf': True, 'auto_frame_after_dxf': False},
}
LAYOUT = {'dictionary': 'DICT_4X4_50', 'markers': [
    {'marker_id': 11, 'x_mm': 0, 'y_mm': 0},
    {'marker_id': 12, 'x_mm': 900, 'y_mm': 0},
    {'marker_id': 13, 'x_mm': 900, 'y_mm': 680},
    {'marker_id': 10, 'x_mm': 0, 'y_mm': 680},
]}


def scene(missing=False, shifted=False):
    image = np.full((980, 1220, 3), (40, 150, 40), np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, x, y in [(11, 150, 150), (12, 1050, 150), (13, 1050, 830), (10, 150, 830)]:
        if missing and marker_id == 13:
            continue
        image[y-60:y+61, x-60:x+61] = 255
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 95)
        image[y-47:y+48, x-47:x+48] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    cv2.rectangle(image, (350, 350), (850, 650), (100, 170, 190), -1)
    if shifted:
        image = cv2.warpAffine(image, np.float32([[1, 0, 20], [0, 1, 15]]), (1220, 980), borderValue=(40, 150, 40))
    return image


def production_setup(workspace, monkeypatch, image):
    client, manager, root = workspace
    monkeypatch.setattr(main, 'load_config', lambda: CONFIG)
    layout_path = root / 'data/calibration/aruco_viewer_bed_layout.json'
    layout_path.write_text(json.dumps(LAYOUT))
    def save_frame(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(path), image)
        return path
    monkeypatch.setattr(main.camera_manager, 'save_frame', save_frame)
    return client, manager, root


@pytest.mark.parametrize('shifted', [False, True])
def test_four_markers_to_lasercad_with_real_dxf(workspace, monkeypatch, shifted):
    client, manager, root = production_setup(workspace, monkeypatch, scene(shifted=shifted))
    calls = []
    monkeypatch.setattr(main, 'open_dxf_in_lasercad', lambda path, config: calls.append(Path(path)) or {'status': 'launch_requested'})
    assert client.post('/api/workflow', json=payload(manager)).status_code == 202
    status = finish(client)
    assert status['stage'] == 'Completed', status
    result = status['result']
    assert result['detection']['scale_source'] == 'aruco_workspace'
    material = Polygon(result['detection']['mm_polygon'])
    assert material.bounds == pytest.approx((200, 200, 700, 500), abs=5)
    output = result['export']
    path = Path(output['path'])
    assert calls == [path]
    doc = ezdxf.readfile(path)
    assert doc.units == ezdxf.units.MM
    entities = list(doc.modelspace())
    assert len(entities) == 2
    for entity in entities:
        assert entity.dxf.layer == 'CUT' and entity.closed
        polygon = Polygon([(point[0], point[1]) for point in entity.get_points()])
        assert material.covers(polygon)
        assert polygon.area == pytest.approx(400)
    check = output['alignment']['bed_check']
    assert check['within_bed'] is None and check['checked_cuts'] == 2
    assert check['bounds_enforced'] is False
    assert check['width_mm'] == 900 and check['height_mm'] == 680
    assert path.with_suffix('.csv').is_file()
    assert list(root.glob('data/projects/*/aruco_scale_profile.json'))


def test_missing_marker_stops_before_export_or_handoff(workspace, monkeypatch):
    client, manager, root = production_setup(workspace, monkeypatch, scene(missing=True))
    monkeypatch.setattr(main, 'open_dxf_in_lasercad', lambda *a: pytest.fail('Must not launch'))
    assert client.post('/api/workflow', json=payload(manager)).status_code == 202
    status = finish(client)
    assert status['stage'] == 'Failed'
    assert 'Four-marker calibration failed' in status['error']
    assert not list(root.glob('data/projects/*/exports/*.dxf'))


def test_negative_laser_coordinates_export_unchanged_and_handoff(workspace, monkeypatch):
    client, manager, root = production_setup(workspace, monkeypatch, scene())
    assert client.post('/api/laser/alignment', json={'enabled': True, 'laser_x_mm': -1000}).status_code == 200
    calls=[]
    monkeypatch.setattr(main, 'open_dxf_in_lasercad', lambda path, config: calls.append(path) or {'status':'launch_requested'})
    assert client.post('/api/workflow', json=payload(manager)).status_code == 202
    status = finish(client)
    assert status['stage'] == 'Completed',status
    path=Path(status['result']['export']['path'])
    assert calls==[str(path)] or calls==[path]
    assert all(point[0]<0 for entity in ezdxf.readfile(path).modelspace() for point in entity.get_points())


@pytest.mark.parametrize('polygon', [
    [[-1, 0], [20, 0], [20, 20], [-1, 20]],
    [[880, 0], [901, 0], [901, 20], [880, 20]],
    [[0, 670], [20, 670], [20, 681], [0, 681]],
])
def test_final_cut_coordinates_are_not_rejected_by_nominal_bed(polygon):
    original=[p[:] for p in polygon]
    result=validate_cut_bounds([{'polygon':polygon}],CONFIG)
    assert result['bounds_enforced'] is False
    assert polygon==original


@pytest.mark.parametrize('value', [0, -1, True, 'NaN', float('inf')])
def test_invalid_bed_sizes_rejected(value):
    with pytest.raises(ValueError, match='positive finite'):
        validate_bed_settings({'workspace': {'width_mm': value}})


def test_empty_job_does_not_export():
    with pytest.raises(ValueError, match='No cut templates fit'):
        validate_cut_bounds([], CONFIG)


def test_exact_bed_corners_are_allowed():
    polygon = [[0, 0], [900, 0], [900, 680], [0, 680]]
    assert validate_cut_bounds([{'polygon': polygon}], CONFIG)['bounds_enforced'] is False


def test_reported_negative_coordinates_do_not_raise_bed_warning():
    polygon=[[-654.83,-356.13],[-502.43,-356.13],[-502.43,-203.73],[-654.83,-203.73]]
    result=validate_cut_bounds([{'polygon':polygon}],CONFIG)
    assert result['export_bounds_mm']=={'min_x':-654.83,'min_y':-356.13,'max_x':-502.43,'max_y':-203.73}
