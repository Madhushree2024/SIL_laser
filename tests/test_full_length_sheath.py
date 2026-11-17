import cv2
import numpy as np
import pytest
from app.vision.sheath_detection import ClassicalSheathDetector


def test_clipped_sheath_at_camera_edge_is_rejected():
    image=np.full((400,500,3),(40,150,40),np.uint8)
    cv2.rectangle(image,(150,0),(280,350),(100,170,190),-1)
    with pytest.raises(ValueError,match='camera image edge'):
        ClassicalSheathDetector().detect(image,min_area_px=1000,reject_incomplete=True)


def test_clipped_sheath_at_board_boundary_is_rejected():
    image=np.full((500,600,3),(40,150,40),np.uint8)
    cv2.rectangle(image,(200,40),(350,460),(100,170,190),-1)
    workspace=np.zeros((500,600),np.uint8)
    workspace[80:420,80:520]=255
    with pytest.raises(ValueError,match='board or marker boundary'):
        ClassicalSheathDetector().detect(image,min_area_px=1000,workspace_mask=workspace,reject_incomplete=True)


def test_separate_full_length_materials_remain_separate():
    image=np.full((600,600,3),(40,150,40),np.uint8)
    cv2.rectangle(image,(100,50),(220,550),(100,170,190),-1)
    cv2.rectangle(image,(240,40),(360,560),(100,170,190),-1)
    result=ClassicalSheathDetector().detect(image,min_area_px=1000,reject_incomplete=True)
    assert len(result.sheaths)==2
    for piece in result.sheaths:
        points=np.array(piece['pixel_polygon'])
        assert np.ptp(points[:,1])>=495
