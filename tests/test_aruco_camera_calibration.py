import json
import cv2
import numpy as np
import pytest
from aruco_camera_calibration import calibrate_views, calibrated_corrector, write_checkerboard
from aruco_visual_check import main


def test_calibration_recovers_known_lens_from_varied_views():
    matrix = np.array([[1100., 0, 640.], [0, 1080., 360.], [0, 0, 1]])
    distortion = np.array([-.12, .03, .001, -.002, 0.])
    obj = np.zeros((54, 3), np.float32)
    obj[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2) * 30
    views = []
    for i in range(15):
        rotation = np.array([-.25 + .035 * i, .3 * np.sin(i), .1 * np.cos(i)])
        translation = np.array([-130. + 12 * i, -90. + 8 * (i % 5), 650. + 20 * (i % 4)])
        corners, _ = cv2.projectPoints(obj, rotation, translation, matrix, distortion)
        views.append(corners)
    profile, errors = calibrate_views(views, (1280, 720))
    assert profile.rms_error < .001
    assert len(errors) == 15
    assert np.array(profile.camera_matrix) == pytest.approx(matrix, abs=.2)
    assert profile.distortion_coefficients == pytest.approx(distortion, abs=.002)


def test_too_few_views_cannot_start_measurement():
    with pytest.raises(ValueError, match='12'):
        calibrate_views([], (1280, 720))


def test_corrector_preserves_size_and_rejects_wrong_camera_resolution(tmp_path):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({'camera_matrix': [[100, 0, 40], [0, 100, 30], [0, 0, 1]],
                                'distortion_coefficients': [0] * 5, 'image_width': 80,
                                'image_height': 60, 'camera_index': 1}))
    correct = calibrated_corrector(path, 1)
    image = np.full((60, 80, 3), 150, np.uint8)
    assert np.array_equal(correct(image), image)
    with pytest.raises(ValueError, match='resolution'):
        correct(np.zeros((120, 160, 3), np.uint8))
    with pytest.raises(ValueError, match='another camera'):
        calibrated_corrector(path, 0)


def test_saved_photo_requires_profile(tmp_path, monkeypatch):
    path = tmp_path / 'photo.png'
    cv2.imwrite(str(path), np.full((60, 80, 3), 255, np.uint8))
    monkeypatch.setattr('sys.argv', ['aruco_visual_check.py', '--image', str(path), '--lens-profile', str(tmp_path / 'missing.json'), '--headless'])
    assert main() == 1


def test_printable_board_dimensions(tmp_path):
    path = write_checkerboard(tmp_path / 'board.svg')
    text = path.read_text()
    assert 'width="420mm" height="297mm"' in text
    assert text.count('width="30" height="30"') == 35


def test_rejects_lens_fit_that_folds_outside_checkerboard_region():
    from aruco_camera_calibration import validate_lens_mapping
    matrix = np.array([[3372., 0, 670.], [0, 3395., 376.], [0, 0, 1.]])
    distortion = np.array([-20.7865, 6553.8986, -.0873, .1469, -1250753.8102])
    with pytest.raises(ValueError, match='folds'):
        validate_lens_mapping(matrix, distortion, (1280, 720))


def test_npz_profile_uses_saved_output_matrix(tmp_path):
    from aruco_camera_calibration import read_lens_profile
    path = tmp_path / 'camera.npz'
    matrix = np.array([[100., 0, 40], [0, 100, 30], [0, 0, 1]])
    output_matrix = np.array([[105., 0, 39], [0, 106., 29], [0, 0, 1]])
    distortion = np.zeros((1, 5))
    np.savez(path, camera_matrix=matrix, new_camera_matrix=output_matrix,
             distortion=distortion, image_width=80, image_height=60, rms_error=1.52)
    metadata = read_lens_profile(path)
    assert metadata['image_width'] == 80
    image = np.arange(60 * 80 * 3, dtype=np.uint8).reshape(60, 80, 3)
    maps = cv2.initUndistortRectifyMap(matrix, distortion, None, output_matrix, (80, 60), cv2.CV_32FC1)
    expected = cv2.remap(image, maps[0], maps[1], cv2.INTER_LINEAR)
    assert np.array_equal(calibrated_corrector(path)(image), expected)


def test_existing_npz_bypasses_checkerboard_capture_and_uses_native_size(tmp_path, monkeypatch):
    import aruco_visual_check as viewer
    path = tmp_path / 'camera.npz'
    np.savez(path, camera_matrix=np.array([[100., 0, 40], [0, 100, 30], [0, 0, 1]]),
             distortion=np.zeros(5), image_width=80, image_height=60)
    class Capture:
        def read(self):
            return True, np.full((60, 80, 3), 255, np.uint8)
        def release(self):
            pass
    def open_camera(index, width, height):
        assert (width, height) == (80, 60)
        return Capture()
    def calibrate(*args):
        pytest.fail('Existing NPZ must bypass checkerboard capture')
    monkeypatch.setattr(viewer, 'open_camera', open_camera)
    monkeypatch.setattr(viewer, 'run_calibration', calibrate)
    monkeypatch.setattr('sys.argv', ['aruco_visual_check.py', '--lens-profile', str(path), '--headless', '--output-dir', str(tmp_path / 'out')])
    assert main() == 0
    report = json.loads(next((tmp_path / 'out').glob('*.json')).read_text())
    assert report['lens_profile'] == str(path.resolve())
