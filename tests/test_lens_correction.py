import numpy as np
import pytest

from app.calibration.camera_calibration import CameraCalibrationProfile, calibrate_from_checkerboards
from app.calibration.distortion import undistort


def profile():
    return CameraCalibrationProfile([[100, 0, 50], [0, 100, 50], [0, 0, 1]], [-0.1, 0, 0, 0, 0], 100, 100, 0.1)


def test_lens_matrix_scales_to_frame_resolution(monkeypatch):
    seen = []
    image = np.zeros((200, 200, 3), np.uint8)
    def correct(frame, matrix, coefficients):
        seen.append(matrix)
        return frame.copy()
    monkeypatch.setattr("app.calibration.distortion.cv2.undistort", correct)
    undistort(image, profile())
    np.testing.assert_allclose(seen[0], [[200, 0, 100], [0, 200, 100], [0, 0, 1]])


def test_incompatible_aspect_ratio_skips_profile():
    image = np.zeros((100, 200, 3), np.uint8)
    assert undistort(image, profile()) is image


@pytest.mark.parametrize("board,size", [((1, 6), 25), ((9, 6), 0), ((9,), 25)])
def test_rejects_invalid_checkerboard_settings(board, size):
    with pytest.raises(ValueError):
        calibrate_from_checkerboards([], board, size)


def test_rejects_mixed_calibration_resolutions(monkeypatch):
    images = iter([np.zeros((100, 100, 3), np.uint8), np.zeros((200, 200, 3), np.uint8)])
    monkeypatch.setattr("app.calibration.camera_calibration.cv2.imread", lambda path: next(images))
    corners = np.zeros((54, 1, 2), np.float32)
    monkeypatch.setattr("app.calibration.camera_calibration.cv2.findChessboardCorners", lambda gray, board: (True, corners))
    monkeypatch.setattr("app.calibration.camera_calibration.cv2.cornerSubPix", lambda *args: corners)
    with pytest.raises(ValueError, match="same resolution"):
        calibrate_from_checkerboards(["one", "two"])
