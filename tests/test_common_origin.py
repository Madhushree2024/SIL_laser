from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import cv2
import ezdxf
import numpy as np
import pytest
import yaml
from shapely.geometry import Polygon,LineString
from app.calibration.aruco_bed import BedCalibrationRequest
from app.calibration.workspace import workspace_profile
from app.calibration.scale_calibration import pixel_points_to_mm,save_profile,load_profile
from app.calibration.common_origin import FRAME,canonical_layout,profile_in_frame,migrate_workspace,identity_alignment,export_alignment
from test_workspace_cutting import scene,workspace

LAYOUT={'dictionary':'DICT_4X4_50','markers':[
 {'marker_id':11,'x_mm':0,'y_mm':0},{'marker_id':12,'x_mm':1050,'y_mm':0},
 {'marker_id':13,'x_mm':1050,'y_mm':510},{'marker_id':10,'x_mm':0,'y_mm':510}]}
CONFIG={'workspace':{'coordinate_frame':FRAME,'width_mm':900,'height_mm':680},
        'automation':{'refresh_workspace_calibration':True},'vision':{'min_area_px':1000}}
EXPECTED={11:(-1050,0),12:(0,0),13:(0,-510),10:(-1050,-510)}

def test_saved_marker_distances_and_requested_axis_directions_are_preserved():
    request=BedCalibrationRequest(**LAYOUT)
    layout,matrix=canonical_layout(request)
    assert matrix.tolist()==[[1,0,-1050],[0,-1,0],[0,0,1]]
    for m in layout.markers:assert (m.x_mm,m.y_mm)==EXPECTED[m.marker_id]
    a=np.array([[m.x_mm,m.y_mm] for m in request.markers]);b=np.array([[m.x_mm,m.y_mm] for m in layout.markers])
    assert np.linalg.norm(a[:,None]-a,axis=2)==pytest.approx(np.linalg.norm(b[:,None]-b,axis=2))
    again,identity=canonical_layout(layout)
    assert again==layout and identity.tolist()==np.eye(3).tolist()

def test_pixel_measurements_share_id12_origin_and_round_trip_without_mirroring_photo():
    old=workspace_profile(scene(),BedCalibrationRequest(**LAYOUT))
    new=profile_in_frame(old,CONFIG)
    assert new.coordinate_frame==FRAME and new.origin_marker_id==12
    pixels=[[125,125],[1375,125],[1375,685],[125,685]]
    for actual,expected in zip(pixel_points_to_mm(pixels,new),[EXPECTED[i] for i in [11,12,13,10]]):
        assert actual==pytest.approx(expected,abs=.8)
    probes=np.array([[450,300],[850,300],[850,450],[450,450]],dtype=float)
    mm=np.array(pixel_points_to_mm(probes.tolist(),new))
    restored=cv2.perspectiveTransform(mm.reshape(-1,1,2),np.linalg.inv(new.homography)).reshape(-1,2)
    assert restored==pytest.approx(probes,abs=1e-7)
    assert mm[1,0]>mm[0,0] and mm[2,1]<mm[1,1]
    assert Polygon(mm).area==pytest.approx(Polygon(pixel_points_to_mm(probes.tolist(),old)).area)

def test_migration_is_idempotent_backs_up_settings_and_keeps_lens_and_history(tmp_path):
    calibration=tmp_path/'data/calibration';calibration.mkdir(parents=True)
    config=tmp_path/'app/config/config.yaml';config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({'workspace':{'width_mm':900,'height_mm':680}}))
    layout=calibration/'aruco_viewer_bed_layout.json';layout.write_text(json.dumps(LAYOUT))
    save_profile(workspace_profile(scene(),BedCalibrationRequest(**LAYOUT)),calibration/'scale_profile.json')
    alignment=calibration/'laser_alignment.json';alignment.write_text(json.dumps({'enabled':True,'reference':'rightmost_template_top_right','flip_y':True}))
    lens=tmp_path/'camera_calibration.npz';lens.write_bytes(b'unchanged lens model')
    history=tmp_path/'old-layout.dxf';history.write_bytes(b'unchanged historical job')
    originals={p:p.read_bytes() for p in [config,layout,alignment]}
    result=migrate_workspace(tmp_path)
    assert result['migrated'] and not result['lens_calibration_changed']
    backup=Path(result['backup'])
    for p,data in originals.items():assert (backup/p.relative_to(tmp_path)).read_bytes()==data
    assert lens.read_bytes()==b'unchanged lens model' and history.read_bytes()==b'unchanged historical job'
    assert yaml.safe_load(config.read_text())['workspace']['coordinate_frame']==FRAME
    assert json.loads(alignment.read_text())==export_alignment().model_dump()
    assert load_profile(calibration/'scale_profile.json').origin_marker_id==12
    assert migrate_workspace(tmp_path)['migrated'] is False

@pytest.mark.parametrize('case',['missing_axis_id'])
def test_bad_axis_surveys_are_rejected_without_shearing(case):
    layout=deepcopy(LAYOUT)
    if case=='missing_axis_id':layout['markers'][0]['marker_id']=21
    else:layout['markers'][2]['x_mm']=1000
    with pytest.raises(ValueError):canonical_layout(BedCalibrationRequest(**layout))

@pytest.fixture
def common_workspace(workspace,monkeypatch):
    import app.main as main
    client,root=workspace
    config=deepcopy(CONFIG)
    monkeypatch.setattr(main,'load_config',lambda:config)
    monkeypatch.setattr(main,'WORKFLOW',{'busy':False,'id':None})
    project=root/'data/projects/current';project.mkdir(parents=True)
    main.STATE['project_dir']=str(project)
    (root/'data/calibration/aruco_viewer_bed_layout.json').write_text(json.dumps(LAYOUT))
    return client,root,main,config


@pytest.mark.parametrize('strategy',['first_fit','common_line'])
def test_camera_and_nesting_use_id12_but_every_dxf_uses_template_box_origin(common_workspace,strategy):
    client,root,main,config=common_workspace
    saved=client.post('/api/calibration/workspace',json=LAYOUT)
    assert saved.status_code==200,saved.text
    assert saved.json()['scale']['origin_marker_id']==12
    assert client.get('/api/laser/alignment').json()==export_alignment().model_dump()
    cv2.imwrite(str(root/'photo.png'),scene(material=True))
    detected=client.post('/api/detect')
    assert detected.status_code==200,detected.text
    assert detected.json()['scale']['coordinate_frame']==FRAME
    assert all(x<0 and y<0 for x,y in detected.json()['mm_polygon'])
    payload={'templates':[{'id':'rect','name':'Rectangle','width_mm':50,'height_mm':40,'quantity':2,'enabled':True}],
             'strategy':strategy,'edge_clearance_mm':3,'rotation_step_deg':90}
    nested=client.post('/api/nest',json=payload)
    assert nested.status_code==200,nested.text
    result=nested.json();assert len(result['placements'])==2
    assert result['scale']['coordinate_frame']==FRAME
    for placement in result['placements']:
        assert Polygon(result['sheath_polygon']).covers(Polygon(placement['polygon']))
        assert all(x<0 and y<0 for x,y in placement['polygon'])
    # A stale client asking for the old template-corner origin/Y reversal cannot
    # override the template-box policy, introduce a reflection or change scale.
    old={'enabled':True,'reference':'rightmost_template_top_right','flip_y':True,'laser_x_mm':123,'laser_y_mm':456}
    response=client.post('/api/export',json={'format':'dxf','alignment':old})
    assert response.status_code==200,response.text
    source_points=np.array([point for p in result['placements'] for point in p['polygon']])
    origin=source_points.max(axis=0)
    assert response.json()['alignment']['design_reference_mm']==pytest.approx(origin)
    assert response.json()['alignment']['matrix']==[[1,0,-origin[0]],[0,1,-origin[1]],[0,0,1]]
    assert main.STATE['nesting_result']['placements']==result['placements']
    preview=client.post('/api/laser/alignment/preview',json=old).json()
    assert preview['matrix']==response.json()['alignment']['matrix']
    entities=list(ezdxf.readfile(response.json()['path']).modelspace())
    assert entities and all(x<=1e-8 and y<=1e-8 for e in entities for x,y in e.get_points('xy'))
    assert all(e.dxf.layer=='CUT' for e in entities)
    cut_points=np.array([point for e in entities for point in e.get_points('xy')])
    assert cut_points.max(axis=0)==pytest.approx([0,0],abs=1e-8)
    if strategy=='first_fit':
        for entity,placement in zip(entities,result['placements']):
            assert np.array(entity.get_points('xy'))==pytest.approx(np.array(placement['polygon'])-origin)
    else:
        from shapely import union_all
        actual=union_all([LineString(e.get_points('xy')) for e in entities])
        expected=union_all([Polygon(np.array(p['polygon'])-origin).boundary for p in result['placements']])
        assert actual.hausdorff_distance(expected)<1e-6
    svg=client.post('/api/export',json={'format':'svg'})
    assert svg.status_code==200,svg.text
    import xml.etree.ElementTree as ET
    tree=ET.parse(svg.json()['path']).getroot()
    assert float(tree.attrib['width'].removesuffix('mm'))>0
    assert tree.find('{http://www.w3.org/2000/svg}g').attrib['transform']=='scale(1,-1)'


def test_old_geometry_and_secondary_calibration_cannot_change_shared_origin(common_workspace):
    client,root,main,config=common_workspace
    old=client.post('/api/laser/alignment',json={'enabled':True,'reference':'rightmost_template_top_right','flip_y':True})
    assert old.status_code==409
    assert client.post('/api/calibration/aruco-scale',json={}).status_code==409
    assert client.post('/api/calibration/scale',json={'image_points':[[0,0],[100,0],[100,100],[0,100]],'square_size_mm':100,'image_width':1280,'image_height':720}).status_code==409
    main.STATE.update(sheath_polygon=[[-100,0],[0,0],[0,-100],[-100,-100]],
                      nesting_result={'placements':[{'polygon':[[-50,-10],[-10,-10],[-10,-50],[-50,-50]]}]})
    assert client.post('/api/export',json={'format':'dxf'}).status_code==409
