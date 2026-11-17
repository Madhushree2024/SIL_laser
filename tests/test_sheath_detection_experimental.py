"""Checks for the standalone experiment only; the production detector is unchanged."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from sheath_detection_experimental import detect, main


def frame():
    image = np.full((240, 320, 3), (40, 150, 40), np.uint8)
    return image


def run(image, **options):
    return detect(image, marker_mask=np.zeros(image.shape[:2], np.uint8), min_area_px=100, **options)


def test_empty_green_bed_is_not_material():
    result = run(frame())
    assert result.status == "no_material"
    assert not result.sheaths
    assert cv2.countNonZero(result.mask) == 0


def test_notch_and_green_hole_preserved_dark_grain_retained():
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    image[40:100, 140:180] = (40, 150, 40)  # genuine open notch
    image[130:136, 100:106] = (40, 150, 40)  # even a small green hole must remain
    image[145:170, 180:240] = (25, 45, 75)  # dark reddish grain is still material
    result = run(image)
    assert len(result.sheaths) == 1
    assert result.mask[60, 160] == 0
    assert result.mask[132, 102] == 0
    assert result.mask[155, 200] == 255
    assert result.sheaths[0]["holes_px"]


def test_one_pixel_gap_keeps_materials_separate():
    image = frame()
    image[40:200, 45:150] = (90, 150, 195)
    image[40:200, 151:270] = (90, 150, 195)
    result = run(image)
    assert len(result.sheaths) == 2
    assert not np.any(result.mask[:, 150])


def test_small_dark_noise_filled_but_large_dark_hole_preserved():
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    image[60:64, 70:74] = 0
    image[120:140, 110:130] = 0
    result = run(image)
    assert result.mask[61, 71] == 255
    assert result.mask[125, 115] == 0
    unchanged = run(image, max_dark_hole_px=0)
    assert unchanged.mask[61, 71] == 0


def test_dark_or_non_green_frame_rejected_without_full_frame_fallback():
    assert run(np.full((240, 320, 3), 8, np.uint8)).status == "rejected"
    result = run(np.full((240, 320, 3), (80, 120, 180), np.uint8))
    assert result.status == "rejected"
    assert not np.any(result.mask)


def test_masks_are_respected_and_inputs_never_modified():
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    allowed = np.full(image.shape[:2], 255, np.uint8)
    allowed[:, 180:] = 0
    original_image, original_mask = image.copy(), allowed.copy()
    result = run(image, workspace_mask=allowed)
    assert np.array_equal(image, original_image)
    assert np.array_equal(allowed, original_mask)
    assert not np.any(result.mask[:, 180:])
    assert result.sheaths[0]["warnings"]
    assert result.status == "review"


def test_background_reference_validation_and_empty_bed_subtraction():
    image = frame()
    image[50:180, 80:220] = (90, 150, 195)
    assert len(run(image, background=frame()).sheaths) == 1
    with pytest.raises(ValueError, match="dimensions"):
        run(image, background=frame()[:100])


def test_explicit_marker_exclusion_and_input_validation():
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    markers = np.zeros(image.shape[:2], np.uint8)
    markers[100:120, 100:120] = 255
    result = detect(image, marker_mask=markers, min_area_px=100)
    assert not np.any(result.mask[100:120, 100:120])
    with pytest.raises(ValueError, match="positive integer"):
        detect(image, min_area_px=0)
    with pytest.raises(ValueError, match="same-size"):
        detect(image, workspace_mask=np.zeros((10, 10), np.uint8))
    with pytest.raises(ValueError, match="uint8 BGR"):
        detect(image.astype(float))


def test_cli_creates_unique_outputs_and_preserves_source(tmp_path):
    path = tmp_path / "source.png"
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    assert cv2.imwrite(str(path), image)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    arguments = [str(path), "--min-area-px", "100", "--output", str(tmp_path / "runs")]
    assert main(arguments) == 0
    assert main(arguments) == 0
    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 2
    for run_path in runs:
        report = json.loads((run_path / "result.json").read_text())
        assert report["units"] == "pixels"
        assert report["sheath_count"] == 1
        assert (run_path / "experimental_mask.png").is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

def test_dark_neutral_background_patch_is_not_an_extra_sheath():
    image = frame()
    image[40:200, 140:270] = (90, 150, 195)
    image[80:160, 40:100] = (65, 65, 65)
    result = run(image)
    assert len(result.sheaths) == 1
    assert result.mask[100, 60] == 0
    assert result.diagnostics["excluded_regions_without_material_colour"] == 1


def test_partial_material_at_image_edge_is_flagged_and_excluded():
    image = frame()
    image[40:200, 140:270] = (90, 150, 195)
    image[90:180, :45] = (90, 150, 195)
    result = run(image)
    assert len(result.sheaths) == 1
    assert result.diagnostics["excluded_regions_at_image_edge"] == 1
    assert not np.any(result.mask[:, :45])
    assert any("image edge" in warning for warning in result.warnings)

class FakeCamera:
    def __init__(self, image=None, fail=False):
        self.image = frame() if image is None else image
        self.fail = fail
        self.released = False
    def read(self):
        return (False, None) if self.fail else (True, self.image.copy())
    def release(self):
        self.released = True


def fake_live(monkeypatch, camera, keys):
    import sheath_detection_experimental as experiment
    observations = []
    sequence = iter(keys)
    monkeypatch.setattr(experiment, "_open_camera", lambda *args: camera)
    monkeypatch.setattr(cv2, "namedWindow", lambda *args: None)
    monkeypatch.setattr(cv2, "resizeWindow", lambda *args: None)
    monkeypatch.setattr(cv2, "imshow", lambda title, image: observations.append(image.copy()))
    monkeypatch.setattr(cv2, "waitKey", lambda *args: next(sequence, ord("q")))
    monkeypatch.setattr(cv2, "getWindowProperty", lambda *args: 1)
    monkeypatch.setattr(cv2, "destroyWindow", lambda *args: None)
    return observations


def test_live_quit_releases_camera_without_writing_files(monkeypatch, tmp_path):
    camera = FakeCamera()
    observations = fake_live(monkeypatch, camera, [ord("q")])
    assert main(["--live", "--camera", "1", "--output", str(tmp_path)]) == 0
    assert camera.released
    assert observations
    assert list(tmp_path.iterdir()) == []


def test_live_save_writes_the_exact_detected_frame_and_comparison(monkeypatch, tmp_path):
    image = frame()
    image[40:200, 50:270] = (90, 150, 195)
    camera = FakeCamera(image)
    fake_live(monkeypatch, camera, [ord("s"), ord("q")])
    assert main(["--live", "--camera", "1", "--min-area-px", "100",
                 "--compare-current", "--output", str(tmp_path)]) == 0
    runs = list(tmp_path.iterdir())
    assert len(runs) == 1
    report = json.loads((runs[0] / "result.json").read_text())
    assert report["live_camera"] is True
    assert report["camera_index"] == 1
    assert report["lens_correction_applied"] is False
    assert report["sheath_count"] == 1
    assert (runs[0] / "comparison.png").is_file()
    saved = cv2.imread(str(runs[0] / "camera_frame.png"))
    assert np.array_equal(saved, image)
    assert camera.released


def test_live_frame_failure_releases_camera(monkeypatch, tmp_path):
    camera = FakeCamera(fail=True)
    fake_live(monkeypatch, camera, [0] * 15)
    with pytest.raises(SystemExit) as error:
        main(["--live", "--camera", "1", "--output", str(tmp_path)])
    assert error.value.code == 2
    assert camera.released


def test_live_invalid_workspace_mask_releases_camera(monkeypatch, tmp_path):
    mask = tmp_path / "wrong.png"
    assert cv2.imwrite(str(mask), np.zeros((10, 10), np.uint8))
    camera = FakeCamera()
    fake_live(monkeypatch, camera, [ord("q")])
    with pytest.raises(SystemExit):
        main(["--live", "--camera", "1", "--workspace-mask", str(mask)])
    assert camera.released


def test_camera_auto_selection_never_guesses_a_builtin(monkeypatch):
    import sheath_detection_experimental as experiment
    monkeypatch.setattr(experiment, "_camera_list", lambda: [
        {"index": 0, "external": False}, {"index": 2, "external": True}])
    assert experiment._select_camera(None) == 2
    assert experiment._select_camera(0) == 0
    monkeypatch.setattr(experiment, "_camera_list", lambda: [{"index": 0, "external": False}])
    with pytest.raises(ValueError, match="No unique"):
        experiment._select_camera(None)


def test_list_cameras_does_not_open_capture(monkeypatch):
    import sheath_detection_experimental as experiment
    monkeypatch.setattr(experiment, "_camera_list", lambda: [{"index": 1, "external": True}])
    def unexpected_open(*args):
        raise AssertionError("Camera should not be opened during identity listing")
    monkeypatch.setattr(experiment, "_open_camera", unexpected_open)
    assert main(["--list-cameras"]) == 0


def test_saved_image_and_live_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        main(["example.jpg", "--live"])
    assert error.value.code == 2