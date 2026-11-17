import csv
import json
from dataclasses import asdict
from pathlib import Path
import ezdxf
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon
import app.main as main
from app.export.csv_report import write_csv_report
from app.vision.measurements import polygon_measurements
from app.templates.template_geometry import CuttingTemplate
from test_multi_sheath import workspace, wait


def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream: return list(csv.DictReader(stream))


@pytest.mark.parametrize('format',['svg','dxf'])
def test_successful_multi_sheath_workflow_saves_one_csv_with_actual_measurements_and_cuts(workspace,format):
    client,manager,root,_=workspace
    payload={'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side','precision_mm':.1,'export':{'format':format}}
    assert client.post('/api/workflow',json=payload).status_code==202
    status=wait(client)
    assert status['stage']=='Completed',status
    output=status['result']['export'];rows=read(output['csv']['path'])
    assert Path(output['path']).with_suffix('.csv')==Path(output['csv']['path'])
    summaries=[r for r in rows if r['record_type']=='sheath'];cuts=[r for r in rows if r['record_type']=='cut']
    assert len(summaries)==2 and len(cuts)==4
    assert {r['sheath_id'] for r in summaries}=={'sheath-1','sheath-2'}
    assert all(r['workflow_id']==status['id'] for r in rows)
    assert all(r['refinement_mm']=='0.1' for r in rows)
    assert all(r['quantity_scope']=='per_sheath' for r in rows)
    assert all(r['requested_quantity']=='2' and r['priority']=='1' for r in cuts)
    for summary,piece in zip(summaries,status['result']['nesting']['sheaths']):
        assert float(summary['sheath_width_mm'])==piece['measurements']['aligned_width_mm']
        assert float(summary['sheath_height_mm'])==piece['measurements']['aligned_height_mm']
        assert summary['sheath_cut_count']=='2'
    assert len(list(Path(output['path']).parent.glob('*.csv')))==1
    if format=='svg':
        import re,xml.etree.ElementTree as ET
        elements=ET.parse(output['path']).findall('.//{http://www.w3.org/2000/svg}g[@id="CUT"]/{http://www.w3.org/2000/svg}path')
        for element,row in zip(elements,cuts):
            values=[float(v) for v in re.findall(r'-?\d+(?:\.\d+)?',element.attrib['d'])]
            assert json.loads(row['export_polygon_mm'])==[list(pair) for pair in zip(values[::2],values[1::2])]



def test_csv_records_actual_aligned_dxf_coordinates_and_original_geometry(workspace):
    client,manager,root,_=workspace
    client.post('/api/capture');assert client.post('/api/detect').status_code==200
    nested=client.post('/api/nest',json={'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side'}).json()
    response=client.post('/api/export',json={'format':'dxf','alignment':{'enabled':True,'reference':'min_x_min_y','laser_x_mm':100,'laser_y_mm':200,'rotation_deg':90}})
    assert response.status_code==200,response.text
    output=response.json();cuts=[r for r in read(output['csv']['path']) if r['record_type']=='cut']
    entities=list(ezdxf.readfile(output['path']).modelspace().query('LWPOLYLINE[layer=="CUT"]'))
    for row,entity,original in zip(cuts,entities,nested['placements']):
        expected=[list(point) for point in entity.get_points('xy')]
        assert json.loads(row['export_polygon_mm'])==expected
        assert json.loads(row['design_polygon_mm'])==original['polygon']
        center=Polygon(expected).centroid
        assert float(row['export_x_mm'])==pytest.approx(center.x)
        assert float(row['export_y_mm'])==pytest.approx(center.y)


def test_csv_keeps_full_precision_and_unicode_names_and_unplaced_sheath(tmp_path):
    polygons=[[[0,0],[263.123456789,0],[263.123456789,262.987654321],[0,262.987654321]],[[300,0],[310,0],[310,10],[300,10]]]
    template={'id':'t','name':'Plate, café','width_mm':25.4123456789,'height_mm':25.4,'quantity':2,'priority':1,'shape':'rectangle','allowed_rotations':[90]}
    cut={'template_id':'t','template_name':template['name'],'sheath_id':'sheath-1','rotation_deg':90,'polygon':[[1,1],[26.4,1],[26.4,26.4123456789],[1,26.4123456789]]}
    pieces=[{'id':'sheath-1','mm_polygon':polygons[0],'placements':[cut],'measurements':polygon_measurements(polygons[0])},
            {'id':'sheath-2','mm_polygon':polygons[1],'placements':[],'unplaced_templates':[{'template_id':'t','name':template['name'],'requested':2,'placed':0,'unplaced':2}]}]
    result={'sheaths':pieces,'placements':[cut],'templates':[template]}
    path=tmp_path/'report.csv'
    write_csv_report(path,result,polygons[0],vector_path=tmp_path/'layout.dxf',export_format='dxf',exported_placements=[cut])
    rows=read(path)
    assert float(rows[0]['sheath_width_mm'])==263.123456789
    row=next(r for r in rows if r['record_type']=='cut')
    assert row['template_name']=='Plate, café'
    assert float(row['template_width_mm'])==25.4123456789
    missing=next(r for r in rows if r['record_type']=='unplaced_template')
    assert missing['sheath_id']=='sheath-2' and missing['unplaced_quantity']=='2'
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')


def test_csv_atomic_replace_failure_preserves_previous_data_and_cleans_temp(tmp_path,monkeypatch):
    import app.export.csv_report as report
    path=tmp_path/'report.csv';path.write_text('original records',encoding='utf-8')
    polygon=[[0,0],[10,0],[10,10],[0,10]]
    monkeypatch.setattr(report.os,'replace',lambda *args:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError,match='disk full'):
        write_csv_report(path,{'placements':[]},polygon,vector_path=tmp_path/'x.svg',export_format='svg',exported_placements=[])
    assert path.read_text(encoding='utf-8')=='original records'
    assert list(tmp_path.glob('*.tmp'))==[]


def test_csv_failure_marks_workflow_failed_and_releases_lock_after_vector_saved(workspace,monkeypatch):
    client,manager,root,_=workspace
    monkeypatch.setattr(main,'write_csv_report',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('disk full')))
    payload={'templates':[asdict(t) for t in manager.list()],'strategy':'shared_side','export':{'format':'svg'}}
    assert client.post('/api/workflow',json=payload).status_code==202
    status=wait(client)
    assert status['stage']=='Failed' and status['result'] is None
    assert 'CSV data could not be saved' in status['error']
    assert not main.operation_lock.locked()
    assert list(root.rglob('*.svg'))
    assert not list(root.rglob('*.csv'))


def test_template_edits_after_nesting_do_not_change_csv_snapshot(workspace):
    client,manager,root,_=workspace
    client.post('/api/capture');client.post('/api/detect')
    templates=[asdict(t) for t in manager.list()]
    assert client.post('/api/nest',json={'templates':templates,'strategy':'shared_side','precision_mm':.1}).status_code==200
    manager.save([CuttingTemplate('small','Changed name',35,35,quantity=8,priority=9)])
    response=client.post('/api/export',json={'format':'svg'})
    assert response.status_code==200,response.text
    cuts=[r for r in read(response.json()['csv']['path']) if r['record_type']=='cut']
    assert all(r['template_name']=='20 mm square' and r['requested_quantity']=='2' and r['priority']=='1' for r in cuts)
