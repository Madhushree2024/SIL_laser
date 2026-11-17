from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import shutil
import json
import time

import cv2
import ezdxf
import numpy as np
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.calibration.scale_calibration import ScaleCalibrationProfile, save_profile
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager

REAL_ROOT = main.ROOT


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "STATE", {})
    monkeypatch.setattr(main, "WORKFLOW", {"id": None, "busy": False, "stage": "Ready", "error": None, "result": None})
    manager = TemplateManager(tmp_path / "data/templates/templates.json")
    manager.save([CuttingTemplate("legacy-id", "Small", 20, 20, quantity=2, priority=1)])
    monkeypatch.setattr(main, "template_manager", manager)
    monkeypatch.setattr(main, "load_config", lambda: {"calibration": {"aruco": {"enabled": False}}, "vision": {"min_area_px": 100}})
    save_profile(ScaleCalibrationProfile(1, 320, 180, None, 320, 180), tmp_path / "data/calibration/scale_profile.json")
    image = np.full((180, 320, 3), (40, 150, 40), np.uint8)
    cv2.rectangle(image, (60, 50), (240, 140), (100, 170, 190), -1)
    def save_frame(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(path), image)
        return path
    monkeypatch.setattr(main, "camera_manager", SimpleNamespace(save_frame=save_frame))
    # Isolate lens scaling from the geometry fixture; real saved calibration is tested separately.
    monkeypatch.setattr(main, "_correct_camera_frame", lambda frame: frame.copy())
    client = TestClient(main.app)
    yield client, manager, tmp_path
    if main.workflow_thread:
        main.workflow_thread.join(10)
    assert not main.operation_lock.locked()


def payload(manager, **changes):
    value = {"templates": [asdict(item) for item in manager.list()], "strategy": "first_fit", "export": {"format": "dxf"}}
    value.update(changes)
    return value


def finish(client):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get("/api/workflow").json()
        if not status["busy"]:
            main.workflow_thread.join(1)
            return status
        time.sleep(.01)
    pytest.fail("Worker did not complete")


@pytest.mark.parametrize("format", ["svg", "dxf"])
def test_real_detection_nesting_and_export(workspace, monkeypatch, format):
    client, manager, root = workspace
    calls = []
    def correct(image):
        calls.append(image.copy())
        return image.copy()
    monkeypatch.setattr(main, "_correct_camera_frame", correct)
    response = client.post("/api/workflow", json=payload(manager, request_id="e892cc98-c8ac-433a-ac25-b2d315d5b7b3", selected_template_id="legacy-id", export={"format": format}))
    assert response.status_code == 202, response.text
    result = finish(client)
    assert result["stage"] == "Completed", result
    assert result["id"] == "e892cc98-c8ac-433a-ac25-b2d315d5b7b3"
    assert len(calls) == 1
    assert result["result"]["template_ids"] == ["legacy-id"]
    assert {p["template_id"] for p in result["result"]["nesting"]["placements"]} == {"legacy-id"}
    path = Path(result["result"]["export"]["path"])
    assert path.exists()
    if format == "dxf":
        assert len(list(ezdxf.readfile(path).modelspace())) == 2
        assert path.with_suffix(".alignment.json").exists()
    else:
        assert path.read_text().count('<path id="CUT_') == 2
    assert len(list(root.glob("data/projects/*/exports/*." + format))) == 1


def test_duplicate_starts_mutations_and_completion_wait_for_export(workspace, monkeypatch):
    client, manager, root = workspace
    entered, release = Event(), Event()
    original = main.export
    count = []
    def waiting_export(request):
        count.append(1)
        entered.set()
        assert release.wait(5)
        return original(request)
    monkeypatch.setattr(main, "export", waiting_export)
    try:
        assert client.post("/api/workflow", json=payload(manager)).status_code == 202
        assert entered.wait(5)
        status = client.get("/api/workflow").json()
        assert status["busy"] and status["stage"] == "Exporting" and status["result"] is None
        for _ in range(5):
            assert client.post("/api/workflow", json=payload(manager)).status_code == 409
        for endpoint in ["/api/capture", "/api/nest", "/api/export", "/api/config", "/api/templates", "/api/camera/close", "/api/calibration/workspace"]:
            assert client.post(endpoint, json={}).status_code == 409
        assert client.delete("/api/templates/legacy-id").status_code == 409
        assert client.get("/api/templates").status_code == 200
    finally:
        release.set()
        result = finish(client)
    assert result["stage"] == "Completed"
    assert len(count) == 1


@pytest.mark.parametrize("stage", ["capture", "correction", "detection", "nesting", "export"])
def test_stage_failures_release_lock_and_allow_retry(workspace, monkeypatch, stage, caplog):
    client, manager, root = workspace
    targets = {"capture": "_capture_raw", "correction": "_correct_camera_frame", "detection": "_detect_corrected", "nesting": "nest", "export": "export"}
    target = targets[stage]
    original = getattr(main, target)
    def fail(*args):
        raise OSError("controlled " + stage + " failure")
    monkeypatch.setattr(main, target, fail)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    result = finish(client)
    assert result["stage"] == "Failed" and result["result"] is None
    assert stage + " failure" in result["error"]
    assert "Automated workflow failed" in caplog.text
    assert not main.operation_lock.locked()
    monkeypatch.setattr(main, target, original)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    assert finish(client)["stage"] == "Completed"


@pytest.mark.parametrize("changes", [{"templates": []}, {"selected_template_id": "missing"}, {"export": {"format": "bad"}}])
def test_preflight_failures_do_not_capture(workspace, monkeypatch, changes):
    client, manager, root = workspace
    monkeypatch.setattr(main, "_capture_raw", lambda: pytest.fail("Must validate before camera capture"))
    assert client.post("/api/workflow", json=payload(manager, **changes)).status_code == 400
    assert not main.operation_lock.locked()


def test_missing_library_failure(workspace):
    client, manager, root = workspace
    manager.path.unlink()
    assert client.get("/api/templates").status_code == 400
    assert client.post("/api/workflow", json=payload_from_missing()).status_code == 400
    assert not main.operation_lock.locked()


def payload_from_missing():
    return {"templates": [], "export": {"format": "dxf"}}


def test_empty_nesting_is_failure_without_export(workspace, monkeypatch):
    client, manager, root = workspace
    manager.save([CuttingTemplate("huge", "Too large", 1000, 1000)])
    monkeypatch.setattr(main, "export", lambda request: pytest.fail("Cannot export empty placement set"))
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    assert finish(client)["stage"] == "Failed"


def test_export_missing_output_is_failure(workspace, monkeypatch):
    client, manager, root = workspace
    monkeypatch.setattr(main, "export", lambda request: {"path": str(root / "absent.dxf")})
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    assert finish(client)["stage"] == "Failed"


@pytest.mark.parametrize("first", ["large", "small"])
def test_priority_changes_exported_geometry(workspace, monkeypatch, first):
    client, manager, root = workspace
    manager.save([CuttingTemplate("large", "Large", 80, 80, priority=1 if first == "large" else 2),
                  CuttingTemplate("small", "Small", 20, 20, priority=1 if first == "small" else 2)])
    # Controlled boundary makes priority effects unambiguous, using the real nesting engine and exporter.
    original = main._detect_corrected
    def constrained(image):
        result = original(image)
        main.STATE["sheath_polygon"] = [[0, 0], [90, 0], [90, 90], [0, 90]]
        return result
    monkeypatch.setattr(main, "_detect_corrected", constrained)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    result = finish(client)
    assert result["stage"] == "Completed", result
    assert [p["template_id"] for p in result["result"]["nesting"]["placements"]] == [first]
    entity = list(ezdxf.readfile(result["result"]["export"]["path"]).modelspace())[0]
    points = list(entity.get_points())
    assert max(p[0] for p in points) - min(p[0] for p in points) == pytest.approx(80 if first == "large" else 20)


@pytest.mark.parametrize("count", [0, 1, 17])
def test_dynamic_library_and_legacy_ids(workspace, count):
    client, manager, root = workspace
    templates = [CuttingTemplate(str(i), "Legacy " + str(i), 10, 10) for i in range(count)]
    assert client.post("/api/templates", json=[asdict(item) for item in templates]).status_code == 200
    assert len(client.get("/api/templates").json()["templates"]) == count
    if count:
        assert client.post("/api/workflow", json=payload(manager, selected_template_id=str(count - 1))).status_code == 202
        result = finish(client)
        assert result["stage"] == "Completed"
        assert result["result"]["template_ids"] == [str(count - 1)]
        assert client.delete("/api/templates/" + str(count - 1)).status_code == 200
        assert len(manager.list()) == count - 1
    assert client.delete("/api/templates/unknown").status_code == 400


def test_duplicate_names_and_storage_failure_preserve_library(workspace, monkeypatch):
    client, manager, root = workspace
    before = manager.path.read_bytes()
    duplicate = [asdict(item) for item in manager.list()] + [asdict(CuttingTemplate("other", " SMALL ", 30, 30))]
    assert client.post("/api/templates", json=duplicate).status_code == 400
    assert manager.path.read_bytes() == before
    def fail(*args):
        raise PermissionError("controlled storage denial")
    monkeypatch.setattr("app.templates.template_manager.os.replace", fail)
    assert client.delete("/api/templates/legacy-id").status_code == 400
    assert client.post("/api/templates", json=[]).status_code == 400
    assert manager.path.read_bytes() == before
    assert list(manager.path.parent.iterdir()) == [manager.path]


def test_real_saved_photo_and_calibration_match_manual_path(workspace, monkeypatch):
    client, manager, root = workspace
    photo = REAL_ROOT / "data/projects/2026-10-14_092009/original.jpg"
    assert photo.exists(), "Representative saved camera image is required"
    # The 1280x720 real photo uses the production area threshold; 100px belongs
    # to the tiny synthetic fixture and admits board-edge image noise here.
    monkeypatch.setattr(main, "load_config", lambda: {"calibration": {"aruco": {"enabled": False}}, "vision": {"min_area_px": 5000}})
    shutil.copy(REAL_ROOT / "data/calibration/scale_profile.json", root / "data/calibration/scale_profile.json")
    # A saved surveyed perspective must carry its matching marker layout.
    profile = json.loads((root / "data/calibration/scale_profile.json").read_text())
    if profile.get("bed_markers"):
        (root / "data/calibration/aruco_viewer_bed_layout.json").write_text(json.dumps({"markers": profile["bed_markers"], "dictionary": profile.get("dictionary") or "DICT_4X4_50"}))
    def capture(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(photo, path)
        return path
    monkeypatch.setattr(main.camera_manager, "save_frame", capture)
    corrected = []
    def correct(image):
        value = main.npz_camera_calibrator.undistort(image)
        corrected.append(value.copy())
        return value
    monkeypatch.setattr(main, "_correct_camera_frame", correct)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    result = finish(client)
    assert result["stage"] == "Completed", result
    assert len(corrected) == 1
    # Rerun the unchanged manual correction/detection path on the same original photo.
    manual = main.detect()
    np.testing.assert_allclose(manual["mm_polygon"], result["result"]["detection"]["mm_polygon"])
    np.testing.assert_array_equal(corrected[0], corrected[1])


def test_corrected_frame_reaches_detector_without_second_lens_application(workspace, monkeypatch):
    client, manager, root = workspace
    original = main.detector.detect
    seen = []
    frame = np.full((180, 320, 3), (40, 150, 40), np.uint8)
    cv2.rectangle(frame, (60, 50), (240, 140), (100, 170, 190), -1)
    def correct(image):
        seen.append("correct")
        return frame
    def detect(image, *args, **kwargs):
        assert image is frame
        assert kwargs["camera_profile"] is None
        seen.append("detect")
        return original(image, *args, **kwargs)
    monkeypatch.setattr(main, "_correct_camera_frame", correct)
    monkeypatch.setattr(main.detector, "detect", detect)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    assert finish(client)["stage"] == "Completed"
    assert seen == ["correct", "detect"]


def test_snapshot_ignores_later_changes_and_stale_boundary(workspace, monkeypatch):
    client, manager, root = workspace
    entered, release = Event(), Event()
    original = main._capture_raw
    def wait_capture():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(main, "_capture_raw", wait_capture)
    request = main.WorkflowRequest(**payload(manager, selected_template_id="legacy-id", sheath_polygon=[[0, 0], [1, 0], [1, 1], [0, 1]]))
    try:
        main.start_workflow(request)
        assert entered.wait(5)
        request.templates[0].id = "changed"
        request.templates[0].quantity = 0
    finally:
        release.set()
        result = finish(client)
    assert result["stage"] == "Completed", result
    assert result["result"]["template_ids"] == ["legacy-id"]
    assert len(result["result"]["nesting"]["placements"]) == 2


def test_unreadable_capture_recovers(workspace, monkeypatch):
    client, manager, root = workspace
    def invalid_capture(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not an image")
        return path
    monkeypatch.setattr(main.camera_manager, "save_frame", invalid_capture)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    result = finish(client)
    assert result["stage"] == "Failed"
    assert "Unable to read captured image" in result["error"]


def test_unavailable_worker_start_releases_lock(workspace, monkeypatch):
    client, manager, root = workspace
    def unavailable(*args, **kwargs):
        raise RuntimeError("controlled worker start failure")
    monkeypatch.setattr(main, "Thread", unavailable)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 400
    assert not main.operation_lock.locked()
    assert main.workflow_status()["result"] is None


def test_delete_id_is_exact_and_never_a_filesystem_path(workspace):
    client, manager, root = workspace
    unrelated = root / "unrelated.txt"
    unrelated.write_text("keep")
    manager.save([CuttingTemplate("folder/template", "Path-like legacy ID", 10, 10),
                  CuttingTemplate("remaining", "Keep", 10, 10)])
    assert client.delete("/api/templates/folder%2Ftemplate").status_code == 200
    assert [item.id for item in manager.list()] == ["remaining"]
    assert client.delete("/api/templates/unknown%2Ftemplate").status_code == 400
    assert unrelated.read_text() == "keep"


def test_old_metadata_loads_without_migration(workspace):
    client, manager, root = workspace
    import json
    old = asdict(CuttingTemplate("old-number-18", "Old format", 10, 10))
    old.pop("priority")
    manager.path.write_text(json.dumps([old]))
    assert manager.list()[0].priority is None
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    assert finish(client)["stage"] == "Completed"
    assert "priority" not in json.loads(manager.path.read_text())[0]


def test_successful_cycle_has_real_stage_history_and_visual_outputs(workspace):
    client, manager, root = workspace
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    status = finish(client)
    expected = ["Capturing image", "Correcting distortion", "Detecting material and converting coordinates",
                "Processing and applying nesting priority", "Exporting"]
    assert [event["stage"] for event in status["events"]] == expected
    assert all(event["status"] == "completed" for event in status["events"])
    assert all(event["elapsed_seconds"] >= 0 for event in status["events"])
    assert "_stage_started" not in status
    assert status["cycle_number"] == 1
    artifacts = status["artifacts"]
    for url in [artifacts["captured_image"], artifacts["corrected_image"], artifacts["detection"]["mask"], artifacts["detection"]["overlay"]]:
        assert (root / url.removeprefix("/")).exists()
    assert artifacts["nesting"]["placements"] == status["result"]["nesting"]["placements"]
    assert artifacts["export"] == status["result"]["export"]


def test_next_cycle_is_a_new_capture_after_finished_export(workspace, monkeypatch):
    client, manager, root = workspace
    calls = []
    original = main._capture_raw
    def capture():
        calls.append(1)
        return original()
    monkeypatch.setattr(main, "_capture_raw", capture)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    first = finish(client)
    assert len(calls) == 1
    # Reading completed status does not start another capture or export.
    assert client.get("/api/workflow").json()["cycle_number"] == 1
    assert len(calls) == 1
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    second = finish(client)
    assert second["cycle_number"] == 2
    assert first["id"] != second["id"]
    assert len(calls) == 2
    assert first["result"]["export"]["path"] != second["result"]["export"]["path"]
    assert Path(first["result"]["export"]["path"]).exists()
    assert Path(second["result"]["export"]["path"]).exists()
    assert len(second["events"]) == 5


def test_failed_export_is_a_failed_visual_stage_without_success_output(workspace, monkeypatch):
    client, manager, root = workspace
    def fail(request): raise OSError("controlled disk full")
    monkeypatch.setattr(main, "export", fail)
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    status = finish(client)
    assert status["stage"] == "Failed" and not status["busy"]
    assert status["events"][-1]["stage"] == "Exporting"
    assert status["events"][-1]["status"] == "failed"
    assert "export" not in status["artifacts"]
    assert status["artifacts"]["nesting"]["placements"]
    assert status["result"] is None


@pytest.mark.parametrize("level", [0, 255])
def test_uniform_blank_capture_fails_before_detection_or_export(workspace, monkeypatch, level):
    client, manager, root = workspace
    def blank_capture(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(path), np.full((180, 320, 3), level, np.uint8))
        return path
    monkeypatch.setattr(main.camera_manager, "save_frame", blank_capture)
    monkeypatch.setattr(main, "_detect_corrected", lambda image: pytest.fail("Blank image must not enter detection"))
    monkeypatch.setattr(main, "export", lambda request: pytest.fail("Blank image must not export"))
    assert client.post("/api/workflow", json=payload(manager)).status_code == 202
    status = finish(client)
    assert status["stage"] == "Failed" and status["result"] is None
    assert "Captured image is blank" in status["error"]
    assert status["events"][0]["stage"] == "Capturing image"
    assert status["events"][0]["status"] == "failed"
    assert len(status["events"]) == 1
    assert "captured_image" in status["artifacts"]
    assert not main.operation_lock.locked()
