import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon
import app.main as main
from app.calibration.scale_calibration import load_profile

LAYOUT={'dictionary':'DICT_4X4_50','markers':[
    {'marker_id':11,'x_mm':0,'y_mm':0}, {'marker_id':12,'x_mm':1250,'y_mm':0},
    {'marker_id':13,'x_mm':1250,'y_mm':560}, {'marker_id':10,'x_mm':0,'y_mm':560}]}


def scene(material=False):
    image=np.full((900,1500,3),(10,10,50),np.uint8)
    image[55:755,55:1445]=(40,150,40)
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id,x,y in ((11,125,125),(12,1375,125),(10,125,685),(13,1375,685)):
        image[y-60:y+61,x-60:x+61]=255
        marker=cv2.aruco.generateImageMarker(dictionary,marker_id,95)
        image[y-47:y+48,x-47:x+48]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    if material:
        cv2.rectangle(image,(450,300),(850,450),(100,170,190),-1)
        # A larger object outside the calibrated work area must not win detection.
        cv2.rectangle(image,(250,780),(1400,890),(100,170,190),-1)
    return image


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'ROOT',tmp_path)
    monkeypatch.setattr(main,'_correct_camera_frame',lambda image:image)
    monkeypatch.setattr(main,'STATE',{'project_dir':str(tmp_path),'captured_image':str(tmp_path/'photo.png')})
    monkeypatch.setattr(main,'load_config',lambda:{'calibration':{'aruco':{'enabled':True,'marker_size_mm':95,'marker_id':10}},'vision':{'min_area_px':1000}})
    path=tmp_path/'data/calibration/aruco_viewer_bed_layout.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps(LAYOUT))
    cv2.imwrite(str(tmp_path/'photo.png'),scene())
    return TestClient(main.app),tmp_path


def test_capture_calibration_material_nesting_and_vector_export(workspace,monkeypatch):
    client,root=workspace
    assert client.post('/api/laser/alignment',json={'enabled':True,'laser_x_mm':123}).status_code==200
    response=client.post('/api/calibration/workspace')
    assert response.status_code==200,response.text
    profile=load_profile(root/'data/calibration/scale_profile.json')
    assert profile.source=='aruco_workspace'
    assert profile.working_width_mm==1250 and profile.working_height_mm==560
    assert main.get_laser_alignment()['laser_x_mm']==123
    monkeypatch.setattr(main,'detect_aruco_scale_profile',lambda *a,**kw:pytest.fail('Single marker must not override surveyed workspace'))
    cv2.imwrite(str(root/'photo.png'),scene(material=True))
    response=client.post('/api/detect')
    assert response.status_code==200,response.text
    result=response.json()
    assert result['scale_source']=='aruco_workspace'
    material=Polygon(result['mm_polygon'])
    x0,y0,x1,y1=material.bounds
    assert x1-x0==pytest.approx(400,abs=10)
    assert y1-y0==pytest.approx(150,abs=10)
    assert x0>250 and y1<400
    template={'id':'test-square','name':'100 mm square','shape':'square','width_mm':100,'height_mm':100,'quantity':2,'allowed_rotations':[0]}
    response=client.post('/api/nest',json={'templates':[template],'strategy':'first_fit','spacing_mm':2,'edge_clearance_mm':3})
    assert response.status_code==200,response.text
    placements=response.json()['placements'];assert len(placements)==2
    for item in placements:
        polygon=Polygon(item['polygon'])
        assert material.covers(polygon)
        assert polygon.area==pytest.approx(10000)
    for format in ('svg','dxf'):
        response=client.post('/api/export',json={'format':format})
        assert response.status_code==200,response.text
        assert Path(response.json()['path']).is_file()


def test_missing_marker_keeps_previous_scale_and_alignment(workspace):
    client,root=workspace
    existing=root/'data/calibration/scale_profile.json';existing.write_text('{"preserved":true}')
    image=scene();image[60:190,1300:1450]=(40,150,40)
    cv2.imwrite(str(root/'photo.png'),image)
    assert client.post('/api/calibration/workspace').status_code==400
    assert existing.read_text()=='{"preserved":true}'


def test_manual_workspace_dimensions_persist_and_preserve_alignment(workspace):
    client, root = workspace
    layout = json.loads(json.dumps(LAYOUT))
    for marker in layout['markers']:
        marker['x_mm'] *= 950 / 1250
        marker['y_mm'] *= 650 / 560
    assert client.post('/api/laser/alignment', json={'enabled': True, 'laser_x_mm': 123}).status_code == 200
    response = client.post('/api/calibration/workspace', json=layout)
    assert response.status_code == 200, response.text
    profile = load_profile(root / 'data/calibration/scale_profile.json')
    assert profile.working_width_mm == 950
    assert profile.working_height_mm == 650
    assert client.get('/api/calibration/workspace-layout').json()['layout'] == layout
    assert main.get_laser_alignment()['laser_x_mm'] == 123
    assert list((root / 'data/calibration').glob('workspace_layout_before_*.json'))


def test_manual_workspace_invalid_or_missing_markers_preserve_saved_files(workspace):
    client, root = workspace
    assert client.post('/api/calibration/workspace').status_code == 200
    scale = root / 'data/calibration/scale_profile.json'
    layout_path = scale.with_name('aruco_viewer_bed_layout.json')
    before = (scale.read_bytes(), layout_path.read_bytes())
    invalid = json.loads(json.dumps(LAYOUT))
    invalid['markers'][1]['marker_id'] = 11
    assert client.post('/api/calibration/workspace', json=invalid).status_code == 422
    for index, marker in enumerate(invalid['markers']):
        marker.update(marker_id=index + 10, x_mm=index * 100, y_mm=0)
    assert client.post('/api/calibration/workspace', json=invalid).status_code == 422
    missing = json.loads(json.dumps(LAYOUT))
    missing['markers'][0]['marker_id'] = 20
    assert client.post('/api/calibration/workspace', json=missing).status_code == 400
    assert (scale.read_bytes(), layout_path.read_bytes()) == before


def test_manual_workspace_supports_nonrectangular_positions(workspace):
    client, root = workspace
    layout = json.loads(json.dumps(LAYOUT))
    for marker, (x, y) in zip(layout['markers'], [(0, 0), (950, 25), (900, 650), (20, 600)]):
        marker.update(x_mm=x, y_mm=y)
    response = client.post('/api/calibration/workspace', json=layout)
    assert response.status_code == 200, response.text
    assert load_profile(root / 'data/calibration/scale_profile.json').bed_markers == layout['markers']


def test_workspace_resolution_change_is_rejected(workspace):
    client,root=workspace
    assert client.post('/api/calibration/workspace').status_code==200
    cv2.imwrite(str(root/'photo.png'),cv2.resize(scene(material=True),(750,450)))
    response=client.post('/api/detect')
    assert response.status_code==400
    assert 'resolution' in response.json()['detail']


def test_multiple_materials_ignore_markers_and_objects_outside_workspace(workspace):
    client,root=workspace
    assert client.post('/api/calibration/workspace').status_code==200
    image=scene(material=True)
    cv2.rectangle(image,(950,300),(1120,430),(100,170,190),-1)
    cv2.imwrite(str(root/'photo.png'),image)
    response=client.post('/api/detect')
    assert response.status_code==200,response.text
    pieces=response.json()['sheaths']
    assert len(pieces)==2
    for piece in pieces:
        x0,y0,x1,y1=Polygon(piece['mm_polygon']).bounds
        assert x0>250 and y1<400
        assert piece['measurements']['area_mm2']>10000
    template={'id':'square','name':'100 mm square','shape':'square','width_mm':100,'height_mm':100,'quantity':2,'allowed_rotations':[0]}
    nested=client.post('/api/nest',json={'templates':[template],'strategy':'shared_side','edge_clearance_mm':3,'spacing_mm':2})
    assert nested.status_code==200,nested.text
    result=nested.json()
    assert sorted(len(piece['placements']) for piece in result['sheaths'])==[1,2]


def test_long_sheath_beyond_marker_centres_is_not_clipped(workspace):
    client,root=workspace
    assert client.post('/api/calibration/workspace').status_code==200
    calibration=root/'data/calibration/scale_profile.json'
    before=calibration.read_bytes()
    image=scene()
    cv2.rectangle(image,(500,100),(700,735),(100,170,190),-1)
    cv2.imwrite(str(root/'photo.png'),image)
    response=client.post('/api/detect')
    assert response.status_code==200,response.text
    result=response.json()
    assert len(result['sheaths'])==1
    points=np.array(result['pixel_polygon'])
    assert points[:,1].min()<=103
    assert points[:,1].max()>=732
    assert result['measurements']['aligned_height_mm']>620
    assert calibration.read_bytes()==before


def test_missing_green_board_does_not_fall_back_to_clipped_detection(workspace):
    client,root=workspace
    assert client.post('/api/calibration/workspace').status_code==200
    cv2.imwrite(str(root/'photo.png'),np.full((900,1500,3),150,np.uint8))
    response=client.post('/api/detect')
    assert response.status_code==400
    assert 'Green board not found' in response.json()['detail']
