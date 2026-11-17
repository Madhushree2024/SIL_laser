import cv2
import numpy as np
import pytest
from aruco_visual_check import analyze_frame, apply_bed_layout

LAYOUT = {'dictionary':'DICT_4X4_50','markers':[
    {'marker_id':11,'x_mm':0.,'y_mm':0.}, {'marker_id':12,'x_mm':1250.,'y_mm':0.},
    {'marker_id':13,'x_mm':1250.,'y_mm':560.}, {'marker_id':10,'x_mm':0.,'y_mm':560.}]}


def scene():
    image=np.full((800,1500),255,np.uint8)
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id,x,y in ((11,125,125),(12,1375,125),(10,125,685),(13,1375,685)):
        image[y-47:y+48,x-47:x+48]=cv2.aruco.generateImageMarker(dictionary,marker_id,95)
    return image


@pytest.mark.parametrize('tilted',[False,True])
def test_surveyed_centres_and_independent_marker_edges(tilted):
    image=scene()
    if tilted:
        transform=cv2.getPerspectiveTransform(np.array([[0,0],[1499,0],[1499,799],[0,799]],np.float32),np.array([[80,20],[1420,60],[1480,760],[10,730]],np.float32))
        image=cv2.warpPerspective(image,transform,(1500,800),borderValue=255)
    report=apply_bed_layout(analyze_frame(image),LAYOUT)
    markers={m['id']:m for m in report['markers']}
    assert markers[11]['center_mm']==pytest.approx([0,0],abs=.01)
    assert markers[13]['center_mm']==pytest.approx([1250,560],abs=.01)
    for marker in markers.values():
        assert marker['edges_mm']==pytest.approx([95]*4,abs=2)
    pairs={frozenset(p['ids']):p for p in report['pairs']}
    assert pairs[frozenset([10,13])]['center_distance_mm']==pytest.approx(1250,abs=.01)
    assert pairs[frozenset([11,12])]['center_distance_mm']==pytest.approx(1250,abs=.01)
    assert pairs[frozenset([10,11])]['center_distance_mm']==pytest.approx(560,abs=.01)
    assert pairs[frozenset([12,13])]['center_distance_mm']==pytest.approx(560,abs=.01)
    assert pairs[frozenset([10,12])]['center_distance_mm']==pytest.approx(np.hypot(1250,560),abs=.01)
    assert report['warning'] is None


def test_missing_bed_marker_clears_mm_instead_of_using_old_reference():
    image=scene()
    image[78:173,1328:1423]=255
    report=apply_bed_layout(analyze_frame(image),LAYOUT)
    assert '12' in report['warning']
    assert all(m['edges_mm'] is None for m in report['markers'])
    assert all('center_distance_mm' not in pair for pair in report['pairs'])


def test_wrong_marker_layout_is_rejected():
    wrong={'dictionary':LAYOUT['dictionary'],'markers':[dict(m) for m in LAYOUT['markers']]}
    wrong['markers'][1]['marker_id'],wrong['markers'][2]['marker_id']=13,12
    with pytest.raises(ValueError,match='configured rectangle'):
        apply_bed_layout(analyze_frame(scene()),wrong)
