import cv2
import numpy as np
from app.camera.green_preview import green_preview, LiveGreenPreview


def photo():
    image=np.full((400,600,3),(10,10,50),np.uint8)
    image[80:330,100:500]=(40,160,40)
    image[140:220,230:330]=255
    return image


def test_green_crop_retains_full_coordinates_and_white_objects():
    image=photo()
    preview,metadata=green_preview(image)
    assert preview.shape==image.shape
    assert metadata['green_found']
    assert metadata['x']==100 and metadata['y']==80
    assert metadata['width']==400 and metadata['height']==250
    assert not preview[:70].any()
    assert (preview[160,250]==[255,255,255]).all()


def test_live_crop_stays_fixed_when_material_covers_an_edge():
    processor=LiveGreenPreview()
    processor.apply(photo())
    original=dict(processor.metadata)
    frame=photo();frame[90:250,100:150]=255
    processor.apply(frame)
    assert processor.metadata==original
    processor.reset()
    assert processor.metadata is None


def test_no_green_uses_full_image_coordinates():
    image=np.full((100,150,3),255,np.uint8)
    result,metadata=green_preview(image)
    assert not metadata['green_found']
    assert metadata['width']==150 and metadata['height']==100
    assert np.array_equal(result,image)


def test_edge_marker_and_white_margin_survive_background_removal():
    image = photo()
    image[115:195, 465:545] = 255
    marker = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 12, 60)
    image[125:185, 475:535] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    preview, metadata = green_preview(image)
    assert np.all(preview[120, 540] == 255)
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    _, ids, _ = detector.detectMarkers(preview)
    assert ids is not None and 12 in ids
    assert not preview[20, 550].any()


def test_shadowed_preview_is_brighter_without_mutating_raw_pixels():
    image = photo()
    image[80:330, 100:500] = (10, 45, 10)
    image[140:220, 230:330] = 255
    original = image.copy()
    preview, metadata = green_preview(image)
    assert 0.45 <= metadata['display_gamma'] < 1
    assert preview[100, 200, 1] > 90
    assert np.array_equal(image, original)
    assert np.all(preview[160, 250] == 255)
    assert not preview[:70].any()


def test_live_mask_recovers_after_initial_dark_frame_and_board_move():
    processor = LiveGreenPreview()
    dark = np.zeros((400, 600, 3), np.uint8)
    processor.apply(dark)
    assert not processor.metadata['green_found']
    processor.apply(photo())
    assert processor.metadata['green_found']
    moved = np.zeros_like(dark)
    moved[50:300, 150:550] = (40, 160, 40)
    for _ in range(15):
        processor.apply(moved)
    assert processor.metadata['x'] == 150
    assert processor.metadata['y'] == 50


def test_shadow_does_not_split_the_board_and_clip_material():
    image = photo()
    image[80:330, 350:500] = (4, 20, 4)
    image[140:220, 410:460] = 180
    preview, metadata = green_preview(image)
    assert metadata['x'] == 100 and metadata['width'] == 400
    assert preview[160, 430].min() >= 180


def test_four_marker_envelope_excludes_connected_green_background():
    image = np.zeros((500, 700, 3), np.uint8)
    image[80:420, 100:600] = (40, 160, 40)
    # Nearby green furniture touches the board but is outside its references.
    image[350:490, 500:690] = (40, 160, 40)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, x, y in [(11,110,90),(12,530,90),(10,110,350),(13,530,350)]:
        image[y:y+60,x:x+60] = 255
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 40)
        image[y+10:y+50,x+10:x+50] = cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    preview,_ = green_preview(image)
    assert not preview[470,670].any()
    assert preview[250,350].any()
    detector = cv2.aruco.ArucoDetector(dictionary)
    _,ids,_ = detector.detectMarkers(preview)
    assert set(ids.flatten()) == {10,11,12,13}
