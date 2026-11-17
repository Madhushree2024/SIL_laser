import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import Polygon,box
from app.nesting.precision_nesting import _valid_candidates,_find_position
from app.nesting.shared_search import rectangle_context,possible_contacts


def reference_position(base, usable, placed):
    """Independent exhaustive bounding-box contact search; no scratch files needed."""
    bx0, by0, bx1, by1 = base.bounds
    ux0, uy0, ux1, uy1 = usable.bounds
    x0, x1, y0, y1 = ux0-bx0, ux1-bx1, uy0-by0, uy1-by1
    xs, ys = {x0,x1}, {y0,y1}
    for x,y in shapely.get_coordinates(usable):
        xs.update((x-bx0,x-bx1));ys.update((y-by0,y-by1))
    for other in placed:
        ox0,oy0,ox1,oy1=other.bounds
        xs.update((ox0-bx0,ox1-bx1,ox1-bx0,ox0-bx1))
        ys.update((oy0-by0,oy1-by1,oy1-by0,oy0-by1))
    boundary=shapely.union_all([p.boundary for p in placed],grid_size=1e-6)
    candidates=[]
    for y in sorted(ys):
        for x in sorted(xs):
            poly=affinity.translate(base,x,y)
            if not usable.buffer(2e-7).covers(poly) or any(poly.intersection(other).area>1e-7 for other in placed):
                continue
            contact=shapely.intersection(poly.boundary,boundary,grid_size=1e-6).length
            candidates.append(((-contact,y,x),(poly,x,y)))
    return min(candidates,key=lambda item:item[0])[1] if candidates else None


@pytest.mark.parametrize('angle',[0,13,90])
@pytest.mark.parametrize('count',[0,1,3])
def test_fast_rectangle_filter_matches_exact_geometry_predicates(angle,count):
    base=affinity.rotate(box(-9,-4,9,4),angle,origin=(0,0))
    placed=[affinity.translate(base,20,20),affinity.translate(base,47,40),affinity.translate(base,40,70)][:count]
    usable=Polygon([[0,0],[100,0],[100,35],[65,35],[65,95],[0,95]])
    centers=np.random.default_rng(17).uniform(-10,110,(700,2))
    ordinary,expected=_valid_candidates(base,centers,usable,placed,0)
    fast,actual=_valid_candidates(base,centers,usable,placed,0,fast_rectangles=True)
    np.testing.assert_array_equal(actual,expected)
    for i in np.flatnonzero(actual):assert fast[i].equals_exact(ordinary[i],0)


def test_fast_collision_retains_original_near_tolerance_decisions():
    base=box(0,0,10,10);centers=np.array([[10,0],[10-1e-8,0],[10-1e-9,0],[10-1e-6,0],[50,0],[0,0]])
    _,expected=_valid_candidates(base,centers,box(-1,-1,100,100),[base],0)
    _,actual=_valid_candidates(base,centers,box(-1,-1,100,100),[base],0,fast_rectangles=True)
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.parametrize('angle',[0,13,90])
def test_contact_filter_does_not_remove_a_shared_edge_candidate(angle):
    base=affinity.rotate(box(0,0,20,10),angle,origin=(0,0));other=affinity.translate(base,40,40)
    # Exact translations along either rectangle axis plus surrounding misses.
    u=np.array([np.cos(np.radians(angle)),np.sin(np.radians(angle))]);v=np.array([-u[1],u[0]])
    centers=np.array([[40,40]+u*x+v*y for x in [-20,0,20,50] for y in [-10,0,10,30]])
    context=rectangle_context(base,[other]);assert context is not None
    flags=possible_contacts(context,centers)
    geometries=shapely.polygons(np.asarray(base.exterior.coords)[None,:,:]+centers[:,None,:])
    lengths=shapely.length(shapely.intersection(shapely.boundary(geometries),other.boundary,grid_size=1e-6))
    nonoverlap=shapely.area(shapely.intersection(geometries,other))<=1e-7
    assert flags[(lengths>1e-7)&nonoverlap].all()


@pytest.mark.parametrize('angle',[0,13])
def test_shared_contact_choice_matches_exhaustive_bounding_box_search(angle):
    base=affinity.rotate(box(0,0,30,40),angle,origin=(0,0));placed=[affinity.translate(base,70,70)]
    expected=reference_position(base,box(0,0,200,200),placed)
    actual=_find_position(base,box(0,0,200,200),placed,0,.1,prefer_shared=True)
    if angle == 0:
        assert actual[0].equals_exact(expected[0],0)
        assert actual[1:]==expected[1:]
    else:
        # The former search aligned rotated bounding boxes, missing real edges.
        def contact(poly):
            return shapely.intersection(poly.boundary, placed[0].boundary, grid_size=1e-6).length
        assert contact(actual[0]) >= contact(expected[0])
        assert contact(actual[0]) > 0
        assert box(0,0,200,200).buffer(2e-7).covers(actual[0])
        assert actual[0].intersection(placed[0]).area <= 1e-7


def test_nonparallel_custom_geometry_keeps_the_exact_fallback():
    base=box(0,0,20,10);other=affinity.rotate(box(30,30,60,60),17)
    assert rectangle_context(base,[other]) is None
    centers=np.array([[0,0],[30,30],[70,70]])
    _,ordinary=_valid_candidates(base,centers,box(0,0,100,100),[other],0)
    _,fast=_valid_candidates(base,centers,box(0,0,100,100),[other],0,fast_rectangles=True)
    np.testing.assert_array_equal(fast,ordinary)



def test_feasible_region_keeps_exact_fits_and_permitted_overlap_tolerance():
    from app.nesting.shared_search import feasible_center_region
    base=box(0,0,10,10);usable=box(0,0,20,10);placed=[box(0,0,10,10)]
    context=rectangle_context(base,placed);region=feasible_center_region(base,usable,context)
    for x in [10,10-1e-9]:assert region.covers(shapely.Point(x,0))
    centers=np.array([[10,0],[10-1e-9,0],[10-1e-6,0],[0,0]])
    _,expected=_valid_candidates(base,centers,usable,placed,0)
    _,actual=_valid_candidates(base,centers,usable,placed,0,fast_rectangles=True,rectangle_cache=context,search_region=region)
    np.testing.assert_array_equal(actual,expected)


def test_impossible_fit_is_rejected_without_changing_material():
    from app.nesting.shared_search import feasible_center_region
    base=box(0,0,50,50);usable=box(0,0,100,20);before=usable.wkb
    assert feasible_center_region(base,usable,rectangle_context(base,[])).is_empty
    assert usable.wkb==before


@pytest.mark.parametrize("angle", [.031, -.047, 13, 44.99])
def test_angled_packing_frame_finds_real_edges_instead_of_bounding_box_contacts(angle):
    base=affinity.rotate(box(-38.1,-38.1,38.1,38.1),angle,origin=(0,0))
    other=affinity.rotate(box(282.7682199087,163.6699671345,536.7682199087,417.6699671345),angle,origin=(0,0))
    usable=affinity.rotate(box(280,160,540,520),angle,origin=(0,0))
    polygon,_,_=_find_position(base,usable,[other],0,.1,prefer_shared=True)
    contact=shapely.intersection(polygon.boundary,other.boundary,grid_size=1e-6).length
    assert contact == pytest.approx(76.2,abs=1e-5)
    assert polygon.intersection(other).area <= 1e-7
    assert usable.buffer(2e-7).covers(polygon)
    assert polygon.area == pytest.approx(76.2**2)
