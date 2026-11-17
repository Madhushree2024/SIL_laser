from pathlib import Path
import hashlib,json,math
import numpy as np
import ezdxf
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.calibration.machine_placement import load_machine_placement,save_machine_placement,profile_path,make_profile
from app.calibration.aruco_bed import BedCalibrationRequest
from app.export.lasercad_bed_mapping import prepare_bed_copy,verify_import_snapshot
from test_lasercad_bed_mapping import layout


def root_for(path):return path.parents[4]


def test_missing_measurement_never_substitutes_machine_origin(layout):
    exe,path,config=layout;profile_path(root_for(path)).unlink()
    original=path.read_bytes()
    with pytest.raises(ValueError,match='not measured'):prepare_bed_copy(path,config,exe)
    assert path.read_bytes()==original and not (path.parent/'lasercad-import').exists()


def test_old_disable_switch_cannot_bypass_measured_placement(layout):
    from app.export.lasercad_bed_mapping import enabled
    exe,path,config=layout;config['lasercad']['preserve_top_right_on_bed']=False
    profile_path(root_for(path)).unlink()
    assert enabled(config)
    with pytest.raises(ValueError,match='not measured'):prepare_bed_copy(path,config,exe)


def test_source_reference_required_not_inferred_from_normalized_dxf(layout):
    exe,path,config=layout;path.with_suffix('.alignment.json').unlink()
    with pytest.raises(ValueError,match='reference.*missing'):prepare_bed_copy(path,config,exe)


def test_marker_change_invalidates_the_machine_survey(layout):
    exe,path,config=layout;p=root_for(path)/'data/calibration/aruco_viewer_bed_layout.json'
    data=json.loads(p.read_text());data['markers'][0]['x_mm']-=5;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='positions changed'):prepare_bed_copy(path,config,exe)


def test_old_photo_geometry_cannot_use_a_new_marker_survey(layout):
    exe,path,config=layout;p=path.with_suffix('.alignment.json')
    data=json.loads(p.read_text());data['measurement_calibration']['bed_markers'][0]['x_mm']-=5;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='different marker measurements'):prepare_bed_copy(path,config,exe)


def test_changed_survey_after_handoff_blocks_download_and_frame(layout):
    exe,path,config=layout;mapping=prepare_bed_copy(path,config,exe)
    p=profile_path(root_for(path));p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='placement changed'):verify_import_snapshot(path,mapping)


def test_rotated_survey_preserves_all_photographed_vertices_and_dimensions(layout):
    exe,path,config=layout;root=root_for(path)
    saved=load_machine_placement(root,config);working=saved['working_markers']
    angle=.1;r=np.array([[-math.cos(angle),math.sin(angle)],[-math.sin(angle),-math.cos(angle)]])
    measured=[]
    for m in working:
        value=r@np.array([m['x_mm'],m['y_mm']])+[100,80]
        measured.append({'marker_id':m['marker_id'],'x_mm':float(value[0]),'y_mm':float(value[1])})
    save_machine_placement(root,BedCalibrationRequest(markers=measured),config)
    mapping=prepare_bed_copy(path,config,exe)
    old=list(ezdxf.readfile(path).modelspace());new=list(ezdxf.readfile(mapping['import_path']).modelspace())
    for a,b in zip(old,new):
        previous=np.array(list(a.get_points()))[:,:2];converted=np.array(list(b.get_points()))[:,:2]
        for i,(p,q) in enumerate(zip(previous,converted)):
            expected=r@(p+[-50,-60])+[100,80]
            assert np.array([950-q[0],650-q[1]])==pytest.approx(expected)
            assert np.linalg.norm(previous[i]-previous[i-1])==pytest.approx(np.linalg.norm(converted[i]-converted[i-1]))
    assert np.linalg.det(np.array(mapping['matrix'])[:2,:2])==pytest.approx(1)
    assert mapping['machine_origin_changed'] is False and mapping['physical_job_origin_automatically_set'] is False


def test_inconsistent_measurements_are_not_fixed_with_scaling(layout):
    exe,path,config=layout;root=root_for(path);p=load_machine_placement(root,config)
    measured=p['machine_markers'];measured[0]['x_mm']-=20
    before=profile_path(root).read_bytes()
    with pytest.raises(ValueError,match='disagrees'):save_machine_placement(root,BedCalibrationRequest(markers=measured),config)
    assert profile_path(root).read_bytes()==before


def test_api_saves_survey_without_photo_or_machine_commands(layout,monkeypatch):
    exe,path,config=layout;root=root_for(path);saved=load_machine_placement(root,config)
    monkeypatch.setattr(main,'ROOT',root);monkeypatch.setattr(main,'load_config',lambda:config)
    client=TestClient(main.app)
    response=client.post('/api/calibration/machine-placement',json={'dictionary':saved['dictionary'],'markers':saved['machine_markers']})
    assert response.status_code==200,response.text
    status=response.json()['calibration']['machine_placement']
    assert status['ready'] and status['max_fit_error_mm']<1e-6
    assert status['machine_markers']==saved['machine_markers']
