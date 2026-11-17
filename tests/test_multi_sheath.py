from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
import cv2
import ezdxf
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon, box
import app.main as main
from app.calibration.scale_calibration import ScaleCalibrationProfile, save_profile
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager
from app.vision.sheath_detection import ClassicalSheathDetector
from app.laser.coordinate_alignment import CoordinateAlignment, align_layout


def image_scene(gap=25):
    image=np.full((280,420,3),(40,150,40),np.uint8)
    cv2.rectangle(image,(70,80),(180,180),(100,170,190),-1)
    cv2.rectangle(image,(180+gap,90),(285+gap,170),(100,170,190),-1)
    return image


@pytest.mark.parametrize("gap", [15,25,80])
def test_small_visible_gaps_are_not_closed_between_separate_sheaths(tmp_path,gap):
    image=image_scene(gap)
    result=ClassicalSheathDetector().detect(image,tmp_path,min_area_px=1000)
    assert len(result.sheaths)==2
    assert result.mm_polygon==result.sheaths[0]["mm_polygon"]
    polygons=[Polygon(piece["pixel_polygon"]) for piece in result.sheaths]
    assert polygons[0].distance(polygons[1])>0
    assert sorted(round(p.area) for p in polygons)==pytest.approx([105*80,110*100],abs=1800)
    for piece in result.sheaths:
        assert len(piece["pixel_polygon"])>=4


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"ROOT",tmp_path)
    monkeypatch.setattr(main,"STATE",{})
    monkeypatch.setattr(main,"WORKFLOW",{"id":None,"busy":False,"stage":"Ready","error":None,"result":None})
    monkeypatch.setattr(main,"load_config",lambda:{"calibration":{"aruco":{"enabled":False}},"vision":{"min_area_px":1000}})
    manager=TemplateManager(tmp_path/"data/templates/templates.json")
    manager.save([CuttingTemplate("small","20 mm square",20,20,shape="square",quantity=2,priority=1)])
    monkeypatch.setattr(main,"template_manager",manager)
    save_profile(ScaleCalibrationProfile(1,420,280,None,420,280),tmp_path/"data/calibration/scale_profile.json")
    corrections=[]
    monkeypatch.setattr(main,"_correct_camera_frame",lambda image:corrections.append(1) or image.copy())
    def save_frame(path):
        path.parent.mkdir(parents=True,exist_ok=True)
        assert cv2.imwrite(str(path),image_scene())
        return path
    monkeypatch.setattr(main,"camera_manager",SimpleNamespace(save_frame=save_frame))
    yield TestClient(main.app),manager,tmp_path,corrections
    if main.workflow_thread: main.workflow_thread.join(10)
    assert not main.operation_lock.locked()


def wait(client):
    for _ in range(1000):
        result=client.get("/api/workflow").json()
        if not result["busy"]:
            main.workflow_thread.join(1)
            return result
        time.sleep(.01)
    pytest.fail("Workflow did not finish")


@pytest.mark.parametrize("strategy",["accurate","shared_side","first_fit"])
@pytest.mark.parametrize("format",["svg","dxf"])
def test_each_sheath_is_measured_nested_and_exported_in_one_workflow(workspace,strategy,format):
    client,manager,root,corrections=workspace
    payload={"templates":[asdict(item) for item in manager.list()],"strategy":strategy,"export":{"format":format,"include_sheath":True}}
    assert client.post("/api/workflow",json=payload).status_code==202
    status=wait(client)
    assert status["stage"]=="Completed",status
    assert len(corrections)==1
    detected=status["result"]["detection"]["sheaths"]
    nested=status["result"]["nesting"]
    assert len(detected)==len(nested["sheaths"])==2
    assert nested["requested_pieces"]==4
    assert len(nested["placements"])==4
    assert nested["quantity_scope"]=="per_sheath"
    for piece in nested["sheaths"]:
        assert len(piece["placements"])==2
        assert piece["measurements"]["area_mm2"]>5000
        material=Polygon(piece["mm_polygon"])
        for placement in piece["placements"]:
            assert placement["sheath_id"]==piece["id"]
            assert material.covers(Polygon(placement["polygon"]))
    output=Path(status["result"]["export"]["path"])
    if format=="dxf":
        doc=ezdxf.readfile(output)
        assert len(doc.modelspace().query('LWPOLYLINE[layer=="SHEATH"]'))==2
        assert len(doc.modelspace().query('LWPOLYLINE[layer=="CUT"]'))==4
    else:
        import xml.etree.ElementTree as ET
        ns={"s":"http://www.w3.org/2000/svg"}
        xml=ET.parse(output)
        assert len(xml.findall('.//s:g[@id="REFERENCE"]/s:path',ns))==2
        assert len(xml.findall('.//s:g[@id="CUT"]/s:path',ns))==4
    assert len(client.get('/api/state').json()['sheaths'])==2
    assert len(list((output.parent).glob('*.svg' if format=='svg' else '*.dxf')))==1


def test_global_alignment_keeps_gap_and_uses_bounds_of_all_sheaths():
    boundaries=[list(box(10,20,80,100).exterior.coords),list(box(150,30,220,110).exterior.coords)]
    aligned=align_layout(boundaries[0],[],CoordinateAlignment(enabled=True,reference='max_x_min_y',laser_x_mm=500,laser_y_mm=600),sheaths=boundaries)
    assert aligned['design_reference_mm']==[220,20]
    polygons=[Polygon(points) for points in aligned['sheaths']]
    assert polygons[0].distance(polygons[1])==pytest.approx(70)
    assert polygons[1].bounds[2]==pytest.approx(500)


def test_unplaceable_second_sheath_does_not_receive_a_cross_gap_template(workspace):
    client,manager,root,_=workspace
    polygons=[[[0,0],[80,0],[80,80],[0,80]],[[150,0],[165,0],[165,15],[150,15]]]
    main.STATE.update(project_dir=str(root),sheath_polygon=polygons[0])
    response=client.post('/api/nest',json={'sheath_polygons':polygons,'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side'})
    assert response.status_code==200,response.text
    result=response.json()
    assert len(result['sheaths'][0]['placements'])==2
    assert result['sheaths'][1]['placements']==[]
    assert result['unplaced_templates'][0]['sheath_id']=='sheath-2'
    assert len(result['sheath_polygons'])==2


def test_priority_and_vertical_restrictions_apply_independently(workspace):
    client,manager,root,_=workspace
    manager.save([CuttingTemplate('vertical','Vertical plate',60,20,quantity=2,allowed_rotations=[90],priority=1),CuttingTemplate('small','20 square',20,20,shape='square',quantity=1,priority=2)])
    payload={'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side','export':{'format':'svg'}}
    assert client.post('/api/workflow',json=payload).status_code==202
    status=wait(client)
    assert status['stage']=='Completed',status
    for sheath in status['result']['nesting']['sheaths']:
        assert [p['template_id'] for p in sheath['placements'][:2]]==['vertical','vertical']
        for placement in sheath['placements'][:2]:
            assert placement['rotation_deg']==90
            x0,y0,x1,y1=Polygon(placement['polygon']).bounds
            assert x1-x0==pytest.approx(20)
            assert y1-y0==pytest.approx(60)


def test_invalid_additional_boundary_fails_without_partial_nesting(workspace):
    client,manager,root,_=workspace
    main.STATE.update(project_dir=str(root),sheath_polygon=[[0,0],[100,0],[100,100],[0,100]])
    response=client.post('/api/nest',json={'sheath_polygons':[main.STATE['sheath_polygon'],[]],'templates':[asdict(t) for t in manager.list()]})
    assert response.status_code==400
    assert not main.STATE.get('nesting_result')
    assert not (root/'exports').exists()


def test_workflow_reports_actual_sheath_being_processed(workspace,monkeypatch):
    client,manager,root,_=workspace
    original=main.nesting_engine.nest
    stages=[]
    def inspect(*args,**kwargs):
        status=main.workflow_status()
        stages.append((status['sheath_index'],status['sheath_count'],status['active_sheath']))
        return original(*args,**kwargs)
    monkeypatch.setattr(main.nesting_engine,'nest',inspect)
    payload={'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side','export':{'format':'svg'}}
    assert client.post('/api/workflow',json=payload).status_code==202
    assert wait(client)['stage']=='Completed'
    assert stages==[(1,2,'sheath-1'),(2,2,'sheath-2')]
