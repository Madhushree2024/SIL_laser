import numpy as np
import pytest
from shapely.geometry import Polygon
from app.calibration.scale_calibration import ScaleCalibrationProfile, pixel_points_to_mm
from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate


def test_coordinate_conversion_retains_double_precision_of_saved_homography():
    matrix=np.array([[1.7832459123,-.0557123412,-600.1234567],[.1012345678,1.7145689234,-450.1234567],[.0000918123,-.0000280512,1]],dtype=np.float64)
    points=np.array([[778.12512345,305.543210987],[850.789012345,410.123456789]],dtype=np.float64)
    profile=ScaleCalibrationProfile(1,1250,560,matrix.tolist(),1280,720)
    projected=np.column_stack((points,np.ones(len(points))))@matrix.T
    expected=projected[:,:2]/projected[:,2,None]
    actual=np.array(pixel_points_to_mm(points.tolist(),profile))
    np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-10)
    import cv2
    single=cv2.perspectiveTransform(points.astype(np.float32).reshape(-1,1,2),matrix.astype(np.float32)).reshape(-1,2)
    assert np.max(np.abs(single-expected))>1e-6
    assert profile.homography==matrix.tolist()


def test_scalar_scale_conversion_does_not_truncate_fractional_pixels():
    point=[[101.123456789,75.987654321]]
    profile=ScaleCalibrationProfile(1.6683299541473389,1250,560,None,1280,720)
    expected=np.array(point,dtype=np.float64)*profile.mm_per_pixel
    np.testing.assert_array_equal(pixel_points_to_mm(point,profile),expected)


@pytest.mark.parametrize("strategy",["accurate","shared_side"])
def test_fine_refinement_moves_closer_to_edge_without_changing_sizes_or_spacing(strategy):
    sheath=Polygon([[0,0],[70,0],[80,60],[10,60]])
    template=CuttingTemplate('square','10 square',10,10,shape='square',quantity=3,priority=1)
    engine=NestingEngine()
    coarse=engine.nest(list(sheath.exterior.coords),[template],strategy=strategy,precision_mm=.5,spacing_mm=2,edge_clearance_mm=0)
    fine=engine.nest(list(sheath.exterior.coords),[template],strategy=strategy,precision_mm=.1,spacing_mm=2,edge_clearance_mm=0)
    assert len(coarse.placements)==len(fine.placements)==3
    coarse_x=Polygon(coarse.placements[0].polygon).bounds[0]
    polygons=[Polygon(p.polygon) for p in fine.placements]
    assert coarse_x-polygons[0].bounds[0]==pytest.approx(.25)
    assert polygons[0].bounds[0]-(10*10/60)<.1
    for i,polygon in enumerate(polygons):
        assert sheath.buffer(2e-7).covers(polygon)
        assert polygon.area==pytest.approx(100)
        for other in polygons[:i]: assert polygon.distance(other)>=2-2e-7
