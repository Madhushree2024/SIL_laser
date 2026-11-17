from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import cv2
import ezdxf
import numpy as np
import pytest
import yaml
from test_common_origin import common_workspace,LAYOUT
from test_workspace_cutting import workspace,scene
from app.calibration.workspace_layout_store import profile_uses_layout,save_workspace_layout
from app.calibration.aruco_bed import BedCalibrationRequest
from app.calibration.scale_calibration import load_profile,pixel_points_to_mm

NEW={'dictionary':'DICT_4X4_50','markers':[
 {'marker_id':11,'x_mm':-800,'y_mm':0},{'marker_id':12,'x_mm':0,'y_mm':0},
 {'marker_id':13,'x_mm':0,'y_mm':-400},{'marker_id':10,'x_mm':-790,'y_mm':-390}]}


def test_camera_free_save_survives_restart_and_old_seed_cannot_replace_it(common_workspace):
    client,root,main,config=common_workspace
    assert client.post('/api/calibration/workspace',json=LAYOUT).status_code==200
    old_profile=(root/'data/calibration/scale_profile.json').read_bytes()
    main.STATE['captured_image']=None
    saved=client.post('/api/calibration/workspace-layout',json=NEW)
    assert saved.status_code==200,saved.text
    assert saved.json()['saved'] and saved.json()['changed']
    assert saved.json()['calibration']['workspace_calibration_pending']
    assert saved.json()['calibration']['scale'] is None
    assert client.get('/api/calibration/workspace-layout').json()['layout']==NEW
    assert (root/'data/calibration/scale_profile.json').read_bytes()==old_profile
    (root/'camera_calibration.npz').write_bytes(b'lens calibration unchanged')
    cfg=root/'app/config/config.yaml';cfg.parent.mkdir(parents=True);cfg.write_text(yaml.safe_dump(config))
    bundle=root/'old-bundle';seed=bundle/'seed/data/calibration';seed.mkdir(parents=True)
    (seed/'aruco_viewer_bed_layout.json').write_text(json.dumps(LAYOUT))
    from desktop_runtime import prepare_workspace
    prepare_workspace(root,bundle)
    assert client.get('/api/calibration/workspace-layout').json()['layout']==NEW
    status=client.get('/api/calibration/status').json()
    assert status['workspace_layout']==NEW and status['scale'] is None
    assert (root/'camera_calibration.npz').read_bytes()==b'lens calibration unchanged'
    assert client.post('/api/calibration/workspace-layout',json=NEW).json()['changed'] is False


def test_old_geometry_cannot_nest_preview_or_export_after_marker_change(common_workspace):
    client,root,main,config=common_workspace
    assert client.post('/api/calibration/workspace',json=LAYOUT).status_code==200
    old_scale=asdict(load_profile(root/'data/calibration/scale_profile.json'))
    assert client.post('/api/calibration/workspace-layout',json=NEW).status_code==200
    # Simulate an old project or another tab retaining its previous analysis.
    polygon=[[-150,-100],[-50,-100],[-50,-50],[-150,-50]]
    main.STATE.update(sheath_polygon=polygon,scale=old_scale,nesting_result={'scale':old_scale,'placements':[{'polygon':polygon}]})
    for endpoint,body,code in [('/api/nest',{'templates':[]},409),('/api/export',{'format':'dxf'},409),('/api/laser/alignment/preview',{},400)]:
        result=client.post(endpoint,json=body)
        assert result.status_code==code,result.text
        assert 'Marker positions changed' in result.json()['detail']
    config['automation']['refresh_workspace_calibration']=False
    assert client.post('/api/detect').status_code==409


def test_fresh_four_marker_detection_nesting_and_dxf_use_new_saved_positions(common_workspace):
    client,root,main,config=common_workspace
    assert client.post('/api/calibration/workspace',json=LAYOUT).status_code==200
    assert client.post('/api/calibration/workspace-layout',json=NEW).status_code==200
    cv2.imwrite(str(root/'photo.png'),scene(material=True))
    detected=client.post('/api/detect')
    assert detected.status_code==200,detected.text
    assert profile_uses_layout(detected.json()['scale'],BedCalibrationRequest(**NEW))
    status=client.get('/api/calibration/status').json()
    assert not status['workspace_calibration_pending'] and status['scale_calibrated']
    assert status['workspace_layout']==NEW
    profile=load_profile(root/'data/calibration/scale_profile.json')
    assert profile.working_width_mm==800 and profile.working_height_mm==400
    for actual,expected in zip(pixel_points_to_mm([[125,125],[1375,125],[1375,685],[125,685]],profile),[[-800,0],[0,0],[0,-400],[-790,-390]]):
        assert actual==pytest.approx(expected,abs=.8)
    nested=client.post('/api/nest',json={'templates':[{'id':'small','name':'small','width_mm':40,'height_mm':30,'quantity':2}], 'strategy':'first_fit','rotation_step_deg':90})
    assert nested.status_code==200,nested.text
    result=nested.json();assert len(result['placements'])==2
    assert profile_uses_layout(result['scale'],BedCalibrationRequest(**NEW))
    exported=client.post('/api/export',json={'format':'dxf'})
    assert exported.status_code==200,exported.text
    doc=ezdxf.readfile(exported.json()['path']);entities=list(doc.modelspace())
    points=np.array([point for entity in entities for point in entity.get_points('xy')])
    assert points.max(axis=0)==pytest.approx([0,0],abs=1e-8)
    assert all(e.dxf.layer=='CUT' for e in entities)
    origin=np.array([point for item in result['placements'] for point in item['polygon']]).max(axis=0)
    for entity,item in zip(entities,result['placements']):
        assert np.asarray(entity.get_points('xy'))==pytest.approx(np.asarray(item['polygon'])-origin)


def test_invalid_positions_or_failed_photo_do_not_replace_latest_coordinates(common_workspace):
    client,root,main,config=common_workspace
    assert client.post('/api/calibration/workspace-layout',json=NEW).status_code==200
    path=root/'data/calibration/aruco_viewer_bed_layout.json';before=path.read_bytes()
    bad=deepcopy(NEW);bad['markers'][1]['marker_id']=11
    assert client.post('/api/calibration/workspace-layout',json=bad).status_code==422
    main.STATE['captured_image']=None
    assert client.post('/api/calibration/workspace',json=LAYOUT).status_code==400
    assert path.read_bytes()==before


def test_atomic_failure_leaves_old_layout_intact(tmp_path,monkeypatch):
    path=tmp_path/'layout.json';path.write_text(json.dumps(LAYOUT));before=path.read_bytes()
    def fail(*args):raise OSError('simulated replace failure')
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError):save_workspace_layout(path,BedCalibrationRequest(**NEW))
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('*.tmp'))
