import pytest
from shapely import affinity
from shapely.geometry import Polygon, box
import app.nesting.nesting_engine as engine
from app.templates.template_geometry import CuttingTemplate


def exhaustive(usable, items, spacing_mm=0):
    minx,miny,maxx,maxy=usable.bounds
    placed=[]
    for item in items:
        best=None;score=None
        for x,y in engine._shared_edge_candidates(minx,miny,maxx,maxy,placed,item,2,spacing_mm):
            poly=Polygon([(x,y),(x+item.width_mm,y),(x+item.width_mm,y+item.height_mm),(x,y+item.height_mm)])
            if not usable.contains(poly): continue
            if any(poly.intersection(other).area>1e-7 or poly.distance(other)<spacing_mm-1e-7 for _,other in placed): continue
            candidate_score=(y,x,abs(x+item.width_mm-maxx))
            if score is None or candidate_score<score: score,best=candidate_score,poly
        if best is not None: placed.append((item,best))
    return placed


@pytest.mark.parametrize("spacing",[0,1e-8,2])
@pytest.mark.parametrize("material",["rectangle","rotated","concave"])
def test_shared_cut_batch_preserves_exhaustive_placement_choice(monkeypatch,spacing,material):
    sheath=box(0,0,100,70)
    if material=="rotated": sheath=affinity.rotate(sheath,13,origin=(0,0))
    if material=="concave": sheath=Polygon([(0,0),(100,0),(100,40),(50,40),(50,70),(0,70)])
    templates=[CuttingTemplate('first','25 square',25,25,shape='square',quantity=3,priority=1),CuttingTemplate('second','15 square',15,15,shape='square',quantity=2,priority=2)]
    points=list(sheath.exterior.coords)
    optimized=engine.NestingEngine().nest(points,templates,strategy='shared_side',spacing_mm=spacing,edge_clearance_mm=2).to_dict()
    monkeypatch.setattr(engine,'_pack_shared_edge_order',exhaustive)
    reference=engine.NestingEngine().nest(points,templates,strategy='shared_side',spacing_mm=spacing,edge_clearance_mm=2).to_dict()
    assert optimized==reference
