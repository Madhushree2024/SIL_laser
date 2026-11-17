import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
import cv2
import ezdxf
import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import Polygon,LineString,box
from app.export.common_line import common_cut_paths
from app.export.svg_export import export_svg
from app.export.dxf_export import export_dxf
from app.nesting.nesting_engine import NestingEngine
from app.nesting.precision_nesting import _find_position
from app.templates.template_geometry import CuttingTemplate
from test_multi_sheath import workspace,wait


def sketch():
    # Controlled dimensions reproducing the user's partial-edge T junctions.
    shapes=[box(0,40,40,80),box(40,40,140,140),box(140,40,170,140),box(60,0,110,40)]
    return [{'template_id':str(i+1),'sheath_id':'s1','polygon':[list(p) for p in polygon.exterior.coords[:-1]]} for i,polygon in enumerate(shapes)]


def lines_for_export(path,format):
    if format=='dxf':
        return [LineString(list(entity.get_points('xy'))) for entity in ezdxf.readfile(path).modelspace().query('LWPOLYLINE[layer=="CUT"]')]
    lines=[]
    for element in ET.parse(path).findall('.//{http://www.w3.org/2000/svg}g[@id="CUT"]/{http://www.w3.org/2000/svg}path'):
        values=[float(value) for value in re.findall(r'-?\d+(?:\.\d+)?',element.attrib['d'])]
        lines.append(LineString(list(zip(values[::2],values[1::2]))))
    return lines


def assert_unique_complete_paths(lines,placements,tolerance=.005):
    reference=shapely.union_all([Polygon(p['polygon']).boundary for p in placements],grid_size=1e-6)
    network=shapely.union_all(lines,grid_size=1e-6)
    assert sum(line.length for line in lines)==pytest.approx(network.length,abs=tolerance)
    assert network.hausdorff_distance(reference)<tolerance
    assert network.length==pytest.approx(reference.length,abs=tolerance)


def test_sketch_shared_edges_stay_as_single_internal_cut_segments():
    placements=sketch();paths=common_cut_paths(placements);lines=[LineString(points) for points in paths]
    assert sum(line.length for line in lines)==pytest.approx(810)
    assert_unique_complete_paths(lines,placements)
    # All three dividers remain, including the middle of the larger bottom edge.
    network=shapely.union_all(lines)
    for divider in [LineString([(40,40),(40,80)]),LineString([(140,40),(140,140)]),LineString([(60,40),(110,40)])]:
        assert divider.difference(network).length<1e-6


@pytest.mark.parametrize('format',['svg','dxf'])
@pytest.mark.parametrize('angle',[0,13])
def test_real_exports_cut_each_shared_segment_once(tmp_path,format,angle):
    placements=sketch()
    if angle:
        for placement in placements:placement['polygon']=[list(p) for p in affinity.rotate(Polygon(placement['polygon']),angle,origin=(0,0)).exterior.coords[:-1]]
    path=tmp_path/('shared.'+format)
    exporter=export_svg if format=='svg' else export_dxf
    exporter(path,list(box(0,0,200,200).exterior.coords),placements,include_sheath=False,shared_cut=True)
    lines=lines_for_export(path,format);assert_unique_complete_paths(lines,placements,tolerance=.02)
    assert sum(line.length for line in lines)==pytest.approx(810,abs=.02)


def test_separate_sheaths_are_never_merged_even_if_coordinates_touch():
    placements=sketch()[:2];placements[1]['sheath_id']='s2'
    paths=common_cut_paths(placements)
    assert sum(LineString(points).length for points in paths)==pytest.approx(560)


def test_exact_contact_is_preferred_over_a_disconnected_bottom_left_fit():
    base=box(0,0,30,40);placed=[box(70,70,150,150)]
    poly,x,y=_find_position(base,box(0,0,200,200),placed,0,.1,prefer_shared=True)
    assert poly.boundary.intersection(placed[0].boundary).length==pytest.approx(40)
    assert poly.intersection(placed[0]).area==0


@pytest.mark.parametrize('precision',[.1,.5])
def test_sketch_can_nest_with_partial_shared_edges_and_locked_directions(precision):
    placements=sketch();material=shapely.union_all([Polygon(p['polygon']) for p in placements])
    templates=[CuttingTemplate('2','Middle',100,100,quantity=1,allowed_rotations=[0],priority=1),CuttingTemplate('3','Right',30,100,quantity=1,allowed_rotations=[0],priority=2),CuttingTemplate('4','Bottom',50,40,quantity=1,allowed_rotations=[0],priority=3),CuttingTemplate('1','Left',40,40,quantity=1,allowed_rotations=[0],priority=4)]
    result=NestingEngine().nest(list(material.exterior.coords),templates,strategy='common_line',spacing_mm=8,edge_clearance_mm=0,precision_mm=precision)
    assert len(result.placements)==4
    data=result.to_dict()['placements'];lines=[LineString(p) for p in common_cut_paths(data)]
    assert_unique_complete_paths(lines,data)
    assert sum(line.length for line in lines)==pytest.approx(810,abs=.001)
    for piece in result.placements:
        assert piece.rotation_deg==0
        assert material.buffer(2e-7).covers(Polygon(piece.polygon))


@pytest.mark.parametrize('rotation',[0,90])
def test_common_line_preserves_locked_rectangle_direction_and_clearance(rotation):
    item=CuttingTemplate('rect','Plate',70,30,quantity=2,allowed_rotations=[rotation],priority=1)
    result=NestingEngine().nest(list(box(0,0,160,160).exterior.coords),[item],strategy='common_line',spacing_mm=5,edge_clearance_mm=3)
    assert len(result.placements)==2
    polygons=[Polygon(p.polygon) for p in result.placements]
    assert polygons[0].boundary.intersection(polygons[1].boundary).length>0
    for placement,polygon in zip(result.placements,polygons):
        assert placement.rotation_deg==rotation
        assert box(3,3,157,157).buffer(2e-7).covers(polygon)
        assert polygon.area==pytest.approx(2100)


@pytest.mark.parametrize('format',['svg','dxf'])
def test_common_cut_automatic_workflow_keeps_independent_sheaths_and_csv(workspace,format):
    client,manager,root,corrections=workspace
    payload={'templates':[asdict(t) for t in manager.list()],'strategy':'common_line','spacing_mm':7,'precision_mm':.1,'export':{'format':format}}
    assert client.post('/api/workflow',json=payload).status_code==202
    result=wait(client);assert result['stage']=='Completed',result
    nesting=result['result']['nesting'];output=result['result']['export'];assert len(nesting['sheaths'])==2
    assert nesting['settings']['spacing_mm']==0 and nesting['settings']['strategy']=='common_line'
    assert len(nesting['placements'])==4 and len(corrections)==1
    for piece in nesting['sheaths']:
        polygons=[Polygon(p['polygon']) for p in piece['placements']]
        assert len(polygons)==2
        assert polygons[0].intersection(polygons[1]).area<1e-7
        assert polygons[0].boundary.intersection(polygons[1].boundary).length==pytest.approx(20)
    from pathlib import Path
    path=Path(output['path']);lines=lines_for_export(path,format)
    assert_unique_complete_paths(lines,nesting['placements'])
    assert Path(output['csv']['path']).is_file() and Path(output['daily_production']['path']).is_file()
    assert not main_busy(client)


def main_busy(client):return client.get('/api/workflow').json()['busy']


def test_invalid_geometry_cannot_generate_common_cut_paths():
    with pytest.raises(ValueError):common_cut_paths([{'polygon':[[0,0],[0,0],[0,0]]}])


@pytest.mark.parametrize("offset", [0, 1000.123456789])
def test_fractional_contact_coordinates_are_not_rounded_into_overlap(offset):
    # A photographed sheath yields arbitrary sub-micron coordinates. Centre
    # rounding used to reject this edge and leave a disconnected square.
    top = 417.6699671345435 + offset
    other = box(282.7682199087051, top - 254, 536.7682199087051, top)
    usable = box(280, top - 260, 540, top + 100)
    base = box(-38.1, -38.1, 38.1, 38.1)
    polygon, _, _ = _find_position(base, usable, [other], 0, .1, prefer_shared=True)
    assert polygon.intersection(other).area <= 1e-7
    assert usable.buffer(2e-7).covers(polygon)
    assert shapely.intersection(polygon.boundary, other.boundary, grid_size=1e-6).length == pytest.approx(76.2, abs=1e-6)


def test_photographed_sheath_does_not_leave_small_gaps_in_angled_search_frames():
    import json
    from pathlib import Path
    data=json.loads((Path(__file__).parent/'fixtures/shared_cut_angled_sheath.json').read_text(encoding='utf-8'))
    templates=[CuttingTemplate(**t) for t in data['templates']]
    result=NestingEngine().nest(data['sheath'],templates,strategy='common_line',spacing_mm=0,edge_clearance_mm=3,precision_mm=.1)
    polygons=[Polygon(p.polygon) for p in result.placements]
    assert len(polygons)==5
    from collections import Counter
    assert Counter(p.template_name for p in result.placements)==Counter({'10" square':1,'6" square':1,'Spoon':1,'5" square':1,'3" square':1})
    usable=Polygon(data['sheath']).buffer(-3)
    for i,polygon in enumerate(polygons):
        others=polygons[:i]+polygons[i+1:]
        assert sum(shapely.intersection(polygon.boundary,q.boundary,grid_size=1e-6).length for q in others)>1
        assert usable.buffer(2e-7).covers(polygon)
        assert all(polygon.intersection(q).area<=1e-7 for q in others)
