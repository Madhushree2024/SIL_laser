from pathlib import Path
from copy import deepcopy
import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
import app.main as main
import app.calibration.machine_placement as placement
from app.calibration.aruco_bed import BedCalibrationRequest
from app.calibration.common_origin import canonical_layout,profile_in_frame
from app.calibration.workspace import workspace_profile
from app.calibration.scale_calibration import pixel_points_to_mm
from app.export.lasercad_bed_mapping import prepare_bed_copy
from test_lasercad_bed_mapping import layout
from test_workspace_cutting import scene

MEASURED=BedCalibrationRequest(markers=[{'marker_id':i,'x_mm':x,'y_mm':y} for i,x,y in [(11,852,61),(12,93,67),(13,93,485),(10,850,485)]])
EXPECTED={11:[-759,6],12:[0,0],13:[0,-418],10:[-757,-418]}


def test_irregular_survey_preserves_distances_and_is_idempotent():
    working,matrix=canonical_layout(MEASURED)
    a=np.array([[m.x_mm,m.y_mm] for m in MEASURED.markers]);b=np.array([[m.x_mm,m.y_mm] for m in working.markers])
    for m in working.markers:assert [m.x_mm,m.y_mm]==EXPECTED[m.marker_id]
    assert np.linalg.norm(a[:,None]-a,axis=2)==pytest.approx(np.linalg.norm(b[:,None]-b,axis=2))
    assert matrix[:2,:2].T@matrix[:2,:2]==pytest.approx(np.eye(2))
    again,identity=canonical_layout(working)
    assert again==working and identity==pytest.approx(np.eye(3))


def test_derive_saves_both_surveys_with_zero_fit_and_keeps_intrinsics(layout):
    exe,path,config=layout;root=path.parents[4];lens=root/'camera_calibration.npz';lens.write_bytes(b'unchanged intrinsic profile')
    result=placement.save_layout_from_machine_positions(root,MEASURED,config)
    assert result['changed']
    saved=placement.load_machine_placement(root,config)
    assert saved['max_fit_error_mm']<1e-9
    assert saved['machine_markers']==[m.model_dump() for m in MEASURED.markers]
    assert lens.read_bytes()==b'unchanged intrinsic profile'
    assert np.asarray(saved['working_to_machine'])==pytest.approx(np.array([[-1,0,93],[0,-1,67],[0,0,1]]))
    assert not placement.save_layout_from_machine_positions(root,MEASURED,config)['changed']


def test_atomic_update_restores_both_files_on_save_failure(layout,monkeypatch):
    exe,path,config=layout;root=path.parents[4]
    files=[root/'data/calibration/aruco_viewer_bed_layout.json',placement.profile_path(root)]
    before={p:p.read_bytes() for p in files}
    def fail(*a):raise OSError('simulated disk failure')
    monkeypatch.setattr(placement,'save_machine_placement',fail)
    with pytest.raises(OSError):placement.save_layout_from_machine_positions(root,MEASURED,config)
    assert all(p.read_bytes()==old for p,old in before.items())


def test_invalid_machine_survey_does_not_replace_working_layout(layout):
    exe,path,config=layout;root=path.parents[4];p=root/'data/calibration/aruco_viewer_bed_layout.json';before=p.read_bytes()
    bad=MEASURED.model_dump();bad['markers'][0]['x_mm']=1052
    with pytest.raises(ValueError,match='inside'):placement.save_layout_from_machine_positions(root,BedCalibrationRequest(**bad),config)
    assert p.read_bytes()==before


def test_api_replaces_layout_resets_old_geometry_and_requires_fresh_photo(layout,monkeypatch):
    exe,path,config=layout;root=path.parents[4]
    monkeypatch.setattr(main,'ROOT',root);monkeypatch.setattr(main,'load_config',lambda:config)
    monkeypatch.setattr(main,'STATE',{'nesting_result':{'old':True},'scale':{'old':True}})
    response=TestClient(main.app).post('/api/calibration/layout-from-machine',json=MEASURED.model_dump())
    assert response.status_code==200,response.text
    data=response.json();assert data['calibration']['machine_placement']['ready']
    assert data['calibration']['workspace_calibration_pending']
    assert main.STATE['nesting_result'] is None and main.STATE['scale'] is None
    assert {m['marker_id']:[m['x_mm'],m['y_mm']] for m in data['layout']['markers']}==EXPECTED


def test_irregular_camera_homography_uses_individual_coordinates():
    working,_=canonical_layout(MEASURED)
    p=workspace_profile(scene(),working)
    config={'workspace':{'coordinate_frame':'id12_negative_xy'}}
    p=profile_in_frame(p,config)
    points=pixel_points_to_mm([[125,125],[1375,125],[1375,685],[125,685]],p)
    assert np.asarray(points)==pytest.approx(np.asarray([EXPECTED[i] for i in [11,12,13,10]]),abs=.8)
    assert p.working_width_mm==759 and p.working_height_mm==424


def test_old_dxf_cannot_reuse_new_survey_but_fresh_reference_maps_correctly(layout):
    exe,path,config=layout;root=path.parents[4]
    result=placement.save_layout_from_machine_positions(root,MEASURED,config)
    with pytest.raises(ValueError,match='different marker'):prepare_bed_copy(path,config,exe)
    sidecar=path.with_suffix('.alignment.json');data=json.loads(sidecar.read_text())
    data['measurement_calibration']['bed_markers']=result['layout']['markers'];sidecar.write_text(json.dumps(data))
    mapping=prepare_bed_copy(path,config,exe)
    assert mapping['machine_job_origin_mm']==pytest.approx([143,127])
    assert not mapping['mirrored'] and not mapping['scaled']
