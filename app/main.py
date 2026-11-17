from __future__ import annotations

import json
import logging
from time import monotonic
from copy import deepcopy
from threading import Event, Lock, Thread
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, PrivateAttr

from app.calibration.aruco_scale import detect_aruco_scale_profile, draw_detected_markers, write_marker_png
from app.calibration.common_origin import enabled as shared_origin_enabled, working_layout, profile_in_frame, require_geometry, export_alignment
from app.calibration.workspace import read_workspace_layout, workspace_profile, workspace_mask, material_workspace_mask
from app.calibration.workspace_layout_store import profile_uses_layout, save_workspace_layout
from app.calibration.machine_placement import save_machine_placement, machine_placement_status, save_layout_from_machine_positions
from app.calibration.aruco_bed import BedCalibrationRequest, profile_from_bed_markers
from app.calibration.camera_calibration import calibrate_from_checkerboards, load_profile as load_camera_profile, save_profile as save_camera_profile
from app.calibration.distortion import undistort
from app.calibration.scale_calibration import load_profile as load_scale_profile, profile_from_reference_square, save_profile as save_scale_profile
from app.camera.green_preview import green_preview, LiveGreenPreview
from app.camera.camera_manager import CameraManager
from app.camera.windows_devices import is_usb_camera
from app.camera.frame_capture import timestamped_capture_dir
from app.config.settings import ASSET_ROOT, ROOT, data_path, load_config, save_config
from app.export.dxf_export import export_dxf
from app.laser_control.lasercad_export_automation import ExportAutomation
from app.laser_control.machine_workflow import MachineWorkflow, enabled as machine_workflow_enabled
from app.export.lasercad import open_dxf_in_lasercad
from app.export.svg_export import export_svg
from app.export.csv_report import write_csv_report
from app.export.daily_production import record_daily_production, recover_daily_production
from app.laser.laser_controller import LaserController
from app.laser.lasercad_control import LaserCADController, ControlError
from app.laser.coordinate_alignment import CoordinateAlignment, align_layout
from app.laser.bed_limits import validate_bed_settings, validate_cut_bounds
from app.laser_control.service import LaserControlService
from app.laser_control.api import create_router as create_laser_control_router
from app.laser_control.trocen_protocol import controller_configuration
from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager
from app.vision.measurements import polygon_measurements
from app.vision.sheath_detection import ClassicalSheathDetector


CAMERA_CALIBRATION_FILE = ROOT / "camera_calibration.npz"


@dataclass
class NpzCameraCalibrationProfile:
    camera_matrix: list[list[float]]
    distortion_coefficients: list[float]
    image_width: int
    image_height: int
    rms_error: float
    mean_reprojection_error: float
    new_camera_matrix: list[list[float]]


class NpzCameraCalibrator:
    def __init__(self, calibration_file: Path = CAMERA_CALIBRATION_FILE) -> None:
        self.enabled = False
        self.camera_matrix: np.ndarray | None = None
        self.distortion: np.ndarray | None = None
        self.new_camera_matrix: np.ndarray | None = None
        self.calibration_width: int | None = None
        self.calibration_height: int | None = None
        self.rms_error: float | None = None
        self.mean_reprojection_error: float | None = None
        self._map_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

        if not calibration_file.exists():
            return

        with np.load(calibration_file) as data:
            self.camera_matrix = data["camera_matrix"].astype(np.float64)
            self.distortion = data["distortion"].astype(np.float64)
            self.new_camera_matrix = data["new_camera_matrix"].astype(np.float64)
            self.calibration_width = int(data["image_width"])
            self.calibration_height = int(data["image_height"])
            self.rms_error = float(data["rms_error"])
            self.mean_reprojection_error = float(data["mean_reprojection_error"])

        self.enabled = True

    def _scaled_matrices(self, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        if self.camera_matrix is None or self.new_camera_matrix is None:
            raise RuntimeError("Camera calibration is not loaded")
        if width == self.calibration_width and height == self.calibration_height:
            return self.camera_matrix, self.new_camera_matrix

        sx = width / float(self.calibration_width)
        sy = height / float(self.calibration_height)
        camera_matrix = self.camera_matrix.copy()
        new_camera_matrix = self.new_camera_matrix.copy()
        for matrix in (camera_matrix, new_camera_matrix):
            matrix[0, 0] *= sx
            matrix[1, 1] *= sy
            matrix[0, 2] *= sx
            matrix[1, 2] *= sy
        return camera_matrix, new_camera_matrix

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled or frame is None or not self.is_compatible(frame):
            return frame

        height, width = frame.shape[:2]
        key = (width, height)
        maps = self._map_cache.get(key)
        if maps is None:
            camera_matrix, new_camera_matrix = self._scaled_matrices(width, height)
            maps = cv2.initUndistortRectifyMap(
                camera_matrix,
                self.distortion,
                None,
                new_camera_matrix,
                key,
                cv2.CV_16SC2,
            )
            self._map_cache[key] = maps

        return cv2.remap(frame, maps[0], maps[1], interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    def is_compatible(self, frame: np.ndarray) -> bool:
        if not self.enabled or frame is None:
            return False
        height, width = frame.shape[:2]
        calibration_aspect = float(self.calibration_width) / float(self.calibration_height)
        frame_aspect = width / float(height)
        return abs(frame_aspect - calibration_aspect) / calibration_aspect < 0.15

    def profile(self) -> NpzCameraCalibrationProfile | None:
        if not self.enabled:
            return None
        return NpzCameraCalibrationProfile(
            camera_matrix=self.camera_matrix.tolist(),
            distortion_coefficients=self.distortion.ravel().tolist(),
            image_width=int(self.calibration_width),
            image_height=int(self.calibration_height),
            rms_error=float(self.rms_error),
            mean_reprojection_error=float(self.mean_reprojection_error),
            new_camera_matrix=self.new_camera_matrix.tolist(),
        )


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        if workflow_thread is not None:
            await run_in_threadpool(workflow_thread.join)
        operator_recorder.shutdown()
        camera_manager.close()


from app.application_info import APP_VERSION

app = FastAPI(title="Areca Cutter", version=APP_VERSION, lifespan=lifespan)
logger = logging.getLogger("uvicorn.error")
operation_lock = Lock()
workflow_status_lock = Lock()
workflow_thread: Thread | None = None
from app.laser_control.space_cycle import SpaceCycle, config_fingerprint
space_cycle = SpaceCycle(lambda: ROOT)

WORKFLOW = {"id": None, "busy": False, "stage": "Ready", "error": None, "result": None}


@app.middleware("http")
async def serialize_mutations(request, call_next):
    # Temporarily disable the custom dashboard's commands; keep CAD hosting,
    # DXF handoff, inspection and priority Stop available.
    disabled = load_config().get('laser_control', {}).get('dashboard_enabled', True) is False
    blocked = {'/api/laser-control/command', '/api/laser-control/fallback-command',
               '/api/laser/control/command', '/api/laser-control/layout', '/api/laser-control/cycle/review'}
    if disabled and request.method == 'POST' and request.url.path in blocked:
        return JSONResponse(status_code=409, content={'detail':'The custom Laser Control dashboard and split view are temporarily disabled. Use the LaserCAD tab.'})
    # Shared STATE is also used by the manual controls and other browser tabs.
    mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in {"/api/workflow", "/api/laser-control/space-cycle/action", "/api/laser-control/space-cycle/reset", "/api/laser/control/stop", "/api/laser-control/stop", "/api/laser-control/fallback-stop", "/api/laser-control/lasercad-host"} and not request.url.path.startswith("/api/auth/") and not request.url.path.startswith("/api/laser-control/recordings")
    if mutation and not operation_lock.acquire(blocking=False):
        return JSONResponse(status_code=409, content={"detail": "Processing is active. Wait for the current operation to finish."})
    try:
        priority_stop = request.url.path in {'/api/laser/control/stop','/api/laser-control/fallback-stop','/api/laser-control/stop'}
        if priority_stop and request.method=='POST':
            try:space_cycle.interrupt('Stop requested. Confirm machine idle before resetting.')
            except Exception:logger.exception('Could not journal Space interruption; priority Stop will still be delivered')
        if request.method in {'POST','PUT','PATCH','DELETE'} and not request.url.path.startswith('/api/auth/') and not priority_stop:
            active = laser_control_service.cycles().status()['active']
            safe = {'/api/laser-control/fallback-stop','/api/laser/control/stop','/api/laser-control/cycle/confirm',
                    '/api/laser-control/fallback-command','/api/laser/control/command','/api/laser-control/layout','/api/laser-control/lasercad-host'}
            changes_piece = request.url.path in {'/api/capture','/api/upload','/api/detect','/api/nest','/api/export'}
            if active and (changes_piece or (active['state'] in {'start_pending','awaiting_completion','interrupted'} and request.url.path not in safe)):
                return JSONResponse(status_code=409, content={'detail':'A supervised cutting cycle is active. Observe the machine and confirm completion or abort before changing the sheath/job.'})
        if request.method in {'POST','PUT','PATCH','DELETE'} and not request.url.path.startswith(('/api/auth/','/api/laser-control/recordings')) and not priority_stop:
            stage = space_cycle.status()['stage']
            safe_space = {'/api/laser-control/space-cycle/action','/api/laser-control/space-cycle/reset','/api/laser-control/lasercad-host','/api/laser-control/manual-datum'}
            if stage in {'datum_pending','processing','frame_pending','ready_frame','ready_start','start_pending','awaiting_completion','awaiting_manual_completion','interrupted'} and request.url.path not in safe_space:
                return JSONResponse(status_code=409,content={'detail':'The Space cycle owns this job. Finish or confirm machine idle and reset before changing the piece, settings or drawing.'})
        return await call_next(request)
    finally:
        if mutation:
            operation_lock.release()
camera_manager = CameraManager()
live_green_preview = LiveGreenPreview()
template_manager = TemplateManager(ROOT / "data" / "templates" / "templates.json")
detector = ClassicalSheathDetector()
nesting_engine = NestingEngine()
laser_controller = LaserController()
lasercad_controller = LaserCADController(load_config)
machine_workflow = MachineWorkflow(lambda: ROOT, lambda: load_config())
export_automation = ExportAutomation(lasercad_controller, lambda: ROOT, lambda: load_config(),
                                    machine_workflow=machine_workflow, photo_getter=lambda:STATE.get('captured_image'))
def _open_lasercad_job(path, config):
    # Do not display a relative-origin job for manual download while its physical
    # positioning branch is unavailable. This also covers reopening saved jobs.
    if machine_workflow_enabled(config):machine_workflow.require_available()
    return open_dxf_in_lasercad(path, config)


laser_control_service = LaserControlService(lambda: ROOT, lambda: load_config(),
    _open_lasercad_job, lambda path: lasercad_controller.set_job(path),
    lambda: lasercad_controller.status(job_scoped=True),
    lambda action, request_id, confirmed=False, expected=None, cycle_id=None: (laser_control_stop if action == 'stop' else laser_control_command)(
        LaserCADCommand(action=action, request_id=request_id, confirm_start=confirmed, job_scoped=True, expected_panel=expected, supervised_cycle_id=cycle_id)))
laser_control_service.export_automation_status = export_automation.status

app.include_router(create_laser_control_router(laser_control_service))
from app.laser_control.operator_recording import OperatorRecorder
from app.laser_control.operator_recording_api import create_recording_router

def operator_recording_context():
    import sys
    from app.application_info import APP_VERSION
    config=load_config()
    selected=laser_control_service.current()
    return {
        'app_version':APP_VERSION,
        'app_path':str(Path(sys.executable).resolve()) if getattr(sys,'frozen',False) else str(ASSET_ROOT/'desktop.py'),
        'workspace':str(ROOT.resolve()),
        'settings':{key:deepcopy(config.get(key,{})) for key in
                    ('camera','workspace','calibration','laser_control','lasercad','machine_workflow')},
        'job':{'path':str(lasercad_controller.job) if lasercad_controller.job else None,
               'source_path':selected.get('source_path') if selected else None,
               'job_id':selected.get('id') if selected else None},
        'export_automation':export_automation.status(),
        'positioning_mode_verified_on_controller':False,
    }

operator_recorder=OperatorRecorder(lambda:ROOT,operator_recording_context,lasercad_controller.observation_snapshot)
app.include_router(create_recording_router(operator_recorder))

npz_camera_calibrator = NpzCameraCalibrator()

STATE: dict[str, Any] = {
    "project_dir": None,
    "captured_image": None,
    "sheath_polygon": None,
    "sheaths": [],
    "pixel_polygon": None,
    "nesting_result": None,
    "scale_source": None,
    "scale": None,
}


class CameraOpenRequest(BaseModel):
    index: int
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    allow_non_external: bool = False


class CameraControlRequest(BaseModel):
    name: str
    value: float


class ScaleCalibrationRequest(BaseModel):
    image_points: list[list[float]]
    square_size_mm: float = 100
    image_width: int
    image_height: int


class NestingRequest(BaseModel):
    sheath_polygon: list[list[float]] | None = None
    sheath_polygons: list[list[list[float]]] | None = None
    templates: list[CuttingTemplate]
    spacing_mm: float | None = None
    edge_clearance_mm: float | None = None
    rotation_step_deg: float | None = None
    strategy: str | None = None
    precision_mm: float | None = None


class ExportRequest(BaseModel):
    format: str
    include_sheath: bool = False
    alignment: CoordinateAlignment | None = None
    ui_request_id: UUID | None = None


class WorkflowRequest(NestingRequest):
    _assigned_workflow_mode: str = PrivateAttr(default="automated")
    request_id: UUID | None = None
    selected_template_id: str | None = None
    export: ExportRequest
    supervised: bool = False
    operator_loaded: bool = False
    space_cycle_id: UUID | None = None
    space_revision: int | None = None


class SaveProjectRequest(BaseModel):
    name: str | None = None


class ArucoMarkerRequest(BaseModel):
    marker_id: int = 0
    side_pixels: int = 1000
    dictionary: str = "DICT_4X4_50"


app.mount("/static", StaticFiles(directory=ASSET_ROOT / "frontend"), name="static")
app.mount("/data", StaticFiles(directory=ROOT / "data"), name="data")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ASSET_ROOT / "frontend" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health() -> dict:
    return {"application": "areca-cutter", "status": "ready", "data_root": str(ROOT.resolve())}


@app.get("/api/application-info")
def application_information() -> dict:
    from app.application_info import application_info
    return application_info(load_config(), lambda: lasercad_controller._api().windows())


@app.get("/api/config")
def get_config() -> dict:
    return load_config()


@app.post("/api/config")
def update_config(config: dict[str, Any]) -> dict:
    if shared_origin_enabled(load_config()) and not shared_origin_enabled(config):
        raise HTTPException(status_code=409,detail="ID 12 is the shared coordinate origin; this settings change cannot remove the coordinate-frame contract.")
    try:
        validate_bed_settings(config)
        from app.laser_control.cut_recipe import cut_recipe
        cad=config.get('lasercad',{})
        if not isinstance(cad,dict):raise ValueError('LaserCAD settings must be an object.')
        cut_recipe(cad.get('prepare_and_download',{}))
        controller_configuration(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_config(config)
    return config


@app.get("/api/state")
def current_state() -> dict:
    image_path = Path(STATE["captured_image"]) if STATE.get("captured_image") else None
    if image_path and image_path.exists():
        image_path = _write_corrected_preview(image_path)
    return {
        "project_dir": STATE.get("project_dir"),
        "image": _public_data_path(image_path) if image_path and image_path.exists() else None,
        "preview_crop": STATE.get("preview_crop"),
        "mm_polygon": STATE.get("sheath_polygon"),
        "sheaths": STATE.get("sheaths", []),
        "pixel_polygon": STATE.get("pixel_polygon"),
        "measurements": polygon_measurements(STATE["sheath_polygon"]) if STATE.get("sheath_polygon") else None,
        "scale": STATE.get("scale"),
        "scale_source": STATE.get("scale_source"),
        "nesting_result": STATE.get("nesting_result"),
    }


@app.get("/api/cameras")
def cameras() -> dict:
    return {"cameras": _visible_cameras(camera_manager.enumerate_cameras(usb_only=True))}


@app.post("/api/camera/open")
def open_camera(request: CameraOpenRequest) -> dict:
    try:
        cameras = _visible_cameras(camera_manager.enumerate_cameras(usb_only=True))
        selected = next((camera for camera in cameras if camera["index"] == request.index), None)
        if selected is None:
            raise RuntimeError(f"Camera index {request.index} is not available for this machine")
        if selected and not request.allow_non_external and _looks_like_non_machine_camera(selected["name"]):
            raise RuntimeError(f"Refusing to open non-machine camera: {selected['name']}")
        live_green_preview.reset()
        return camera_manager.open(request.index, request.width, request.height, request.fps)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/camera/open-machine")
def open_machine_camera() -> dict:
    cameras = _visible_cameras(camera_manager.enumerate_cameras(usb_only=True))
    preferred = next((camera for camera in cameras if camera["status"] == "CONNECTED"), None)
    if preferred is None:
        raise HTTPException(status_code=400, detail="USB camera not found. Connect it and click Refresh or Open USB / IMX.")
    try:
        current = camera_manager.status()
        if current.get("connected") and any(camera["index"] == current.get("index") for camera in cameras):
            return current
        settings = load_config().get("camera", {})
        resolution = settings.get("resolution", {})
        live_green_preview.reset()
        return camera_manager.open(preferred["index"], int(resolution.get("width", 1280)), int(resolution.get("height", 720)), int(settings.get("fps", 30)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/camera/close")
def close_camera() -> dict:
    camera_manager.close()
    live_green_preview.reset()
    return {"connected": False}


@app.get("/api/camera/status")
def camera_status() -> dict:
    return camera_manager.status()


@app.post("/api/camera/control")
def camera_control(request: CameraControlRequest) -> dict:
    try:
        ok = camera_manager.set_control(request.name, request.value)
        if ok:
            live_green_preview.reset()
        return {"ok": ok}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _correct_camera_frame(frame: np.ndarray) -> np.ndarray:
    profile = load_camera_profile(ROOT / "data" / "calibration" / "camera_profile.json")
    if profile is not None:
        return undistort(frame, profile)
    return npz_camera_calibrator.undistort(frame)


def _write_corrected_preview(image_path: Path) -> Path:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError("Unable to read image")
    return _save_corrected_preview(image_path, _correct_camera_frame(image))


def _save_corrected_preview(image_path: Path, corrected: np.ndarray) -> Path:
    path = image_path.with_name("corrected.jpg")
    preview, metadata = green_preview(corrected)
    STATE["preview_crop"] = metadata
    if not cv2.imwrite(str(path), preview):
        raise RuntimeError("Unable to save corrected preview")
    return path


@app.get("/api/camera/preview-crop")
def preview_crop() -> dict:
    return {"preview_crop": live_green_preview.metadata}


def _live_camera_preview(frame):
    return live_green_preview.apply(_correct_camera_frame(frame))


@app.get("/api/camera/stream")
def camera_stream() -> StreamingResponse:
    try:
        return StreamingResponse(camera_manager.mjpeg_stream(transform=_live_camera_preview), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _capture_raw() -> tuple[Path, Path]:
    motion=machine_workflow_enabled(load_config())
    if motion:machine_workflow.prepare_capture()
    project_dir = timestamped_capture_dir(ROOT / "data" / "projects")
    image_path = camera_manager.save_frame(project_dir / "original.jpg")
    _set_active_image(project_dir, image_path)
    if motion:machine_workflow.bind_capture(image_path)
    return project_dir, image_path


@app.post("/api/capture")
def capture() -> dict:
    try:
        project_dir, image_path = _capture_raw()
        return {"project_dir": str(project_dir), "image": _public_data_path(_write_corrected_preview(image_path)), "preview_crop": STATE.get("preview_crop")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)) -> dict:
    project_dir = timestamped_capture_dir(ROOT / "data" / "projects")
    image_path = project_dir / "original.jpg"
    image_path.write_bytes(await file.read())
    _set_active_image(project_dir, image_path)
    return {"project_dir": str(project_dir), "image": _public_data_path(_write_corrected_preview(image_path)), "preview_crop": STATE.get("preview_crop")}


@app.post("/api/detect")
def detect() -> dict:
    image_path = STATE.get("captured_image")
    if not image_path:
        raise HTTPException(status_code=400, detail="Capture or upload an image first")
    image = cv2.imread(image_path)
    if image is None:
        raise HTTPException(status_code=400, detail="Unable to read captured image")
    return _detect_corrected(_correct_camera_frame(image))


def _detect_corrected(calibration_image: np.ndarray) -> dict:
    config = load_config()
    scale_profile = load_scale_profile(ROOT / "data" / "calibration" / "scale_profile.json")
    aruco_config = config.get("calibration", {}).get("aruco", {})
    aruco_profile = None
    if config.get("automation", {}).get("refresh_workspace_calibration", False):
        try:
            layout = read_workspace_layout(ROOT / "data" / "calibration" / "aruco_viewer_bed_layout.json")
            layout = working_layout(layout,config)
            aruco_profile = profile_in_frame(workspace_profile(calibration_image, layout),config)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Four-marker calibration failed: {exc}. Keep all four surveyed markers visible.") from exc
        if STATE.get("project_dir"):
            save_scale_profile(aruco_profile, Path(STATE["project_dir"]) / "aruco_scale_profile.json")
    elif aruco_config.get("enabled", True) and not (scale_profile and scale_profile.source in {"aruco_bed", "aruco_workspace"}):
        aruco_profile = detect_aruco_scale_profile(
            calibration_image,
            marker_size_mm=float(aruco_config.get("marker_size_mm", 100)),
            dictionary_name=str(aruco_config.get("dictionary", "DICT_4X4_50")),
            marker_id=aruco_config.get("marker_id"),
        )
        if aruco_profile is not None and STATE.get("project_dir"):
            save_scale_profile(aruco_profile, Path(STATE["project_dir"]) / "aruco_scale_profile.json")
            marker_overlay = draw_detected_markers(calibration_image, str(aruco_config.get("dictionary", "DICT_4X4_50")))
            cv2.imwrite(str(Path(STATE["project_dir"]) / "aruco_overlay.jpg"), marker_overlay)
    if aruco_profile is None and scale_profile is not None:
        height, width = calibration_image.shape[:2]
        if (width, height) != (scale_profile.image_width, scale_profile.image_height):
            raise HTTPException(status_code=400, detail="Photo resolution differs from the saved scale. Capture the marker again at the current resolution and save its scale.")
    active_scale_profile = aruco_profile or scale_profile
    if active_scale_profile is None:
        raise HTTPException(status_code=400, detail="Save working-area calibration before measuring material and nesting in millimetres")
    try:
        active_scale_profile = profile_in_frame(active_scale_profile,config)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    try:
        _require_current_workspace_geometry(asdict(active_scale_profile))
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    detection_options = {}
    if active_scale_profile.source == "aruco_workspace":
        try:
            detection_options["workspace_mask"] = material_workspace_mask(calibration_image, active_scale_profile)
            detection_options["reject_incomplete"] = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = detector.detect(
            calibration_image,
            Path(STATE["project_dir"]),
            camera_profile=None,
            scale_profile=active_scale_profile,
            min_area_px=float(config.get("vision", {}).get("min_area_px", 5000)),
            **detection_options,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if aruco_profile is not None and aruco_profile.source=='aruco_workspace' and not profile_uses_layout(scale_profile,layout):
        # The first successful detection after editing marker positions activates
        # its verified four-marker homography; older scale data remains a backup.
        saved_path=ROOT/'data/calibration/scale_profile.json'
        if saved_path.exists():
            saved_path.with_name('scale_profile_before_workspace_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json').write_bytes(saved_path.read_bytes())
        save_scale_profile(active_scale_profile,saved_path)
    pieces = getattr(result, "sheaths", None) or [{"id": "sheath-1", "mm_polygon": result.mm_polygon, "pixel_polygon": result.pixel_polygon}]
    STATE["sheaths"] = [{**piece, "measurements": polygon_measurements(piece["mm_polygon"])} for piece in pieces]
    STATE["sheath_polygon"] = result.mm_polygon
    STATE["pixel_polygon"] = result.pixel_polygon
    STATE["scale_source"] = active_scale_profile.source if active_scale_profile else "pixels"
    STATE["scale"] = asdict(active_scale_profile) if active_scale_profile else None
    contour_path = Path(STATE["project_dir"]) / "contour.json"
    contour_path.write_text(json.dumps(asdict(result), indent=2))
    return {
        "pixel_polygon": result.pixel_polygon,
        "mm_polygon": result.mm_polygon,
        "sheaths": STATE["sheaths"],
        "measurements": polygon_measurements(result.mm_polygon),
        "scale": STATE["scale"],
        "scale_source": STATE["scale_source"],
        "mask": _public_data_path(Path(result.mask_path)) if result.mask_path else None,
        "overlay": _public_data_path(Path(result.overlay_path)) if result.overlay_path else None,
        "aruco_overlay": _public_data_path(Path(STATE["project_dir"]) / "aruco_overlay.jpg")
        if aruco_profile is not None and STATE.get("project_dir")
        else None,
    }


def _require_current_workspace_geometry(scale) -> None:
    config=load_config()
    require_geometry(scale,config)
    if shared_origin_enabled(config) or (scale and scale.get('source')=='aruco_workspace'):
        try:
            layout=working_layout(read_workspace_layout(ROOT/'data/calibration/aruco_viewer_bed_layout.json'),config)
        except (OSError,ValueError) as exc:
            raise ValueError('Save the current measured marker positions before processing: '+str(exc)) from exc
        if not profile_uses_layout(scale,layout):
            raise ValueError('Marker positions changed. Capture all four markers, detect and nest again using the latest saved coordinates.')


@app.get("/api/calibration/status")
def calibration_status() -> dict:
    camera_profile = load_camera_profile(ROOT / "data" / "calibration" / "camera_profile.json")
    npz_camera_profile = npz_camera_calibrator.profile()
    scale_profile = load_scale_profile(ROOT / "data" / "calibration" / "scale_profile.json")
    if scale_profile is not None and shared_origin_enabled(load_config()):
        try: scale_profile = profile_in_frame(scale_profile,load_config())
        except ValueError: scale_profile = None

    layout=None
    layout_error=None
    try:
        layout=working_layout(read_workspace_layout(ROOT/'data/calibration/aruco_viewer_bed_layout.json'),load_config())
    except (ValueError,OSError) as exc:layout_error=str(exc)
    pending=bool(layout and (scale_profile is None or not profile_uses_layout(scale_profile,layout)))
    if (shared_origin_enabled(load_config()) or (scale_profile and scale_profile.source=='aruco_workspace')) and (pending or layout is None):
        scale_profile=None
    return {
        "workspace_layout": layout.model_dump() if layout else None,
        "workspace_calibration_pending": pending,
        "workspace_layout_error": layout_error,
        "machine_placement": machine_placement_status(ROOT, load_config()),
        "camera_calibrated": npz_camera_calibrator.enabled or camera_profile is not None,
        "lens_profile_source": "checkerboard" if camera_profile else "imported" if npz_camera_calibrator.enabled else "none",
        "scale_calibrated": scale_profile is not None,
        "aruco": load_config().get("calibration", {}).get("aruco", {}),
        "camera": asdict(camera_profile) if camera_profile else asdict(npz_camera_profile) if npz_camera_profile else None,
        "camera_calibration_file": str(ROOT / "data" / "calibration" / "camera_profile.json") if camera_profile else str(CAMERA_CALIBRATION_FILE) if npz_camera_calibrator.enabled else None,
        "scale": asdict(scale_profile) if scale_profile else None,
    }


@app.post("/api/calibration/camera/capture")
def capture_checkerboard() -> dict:
    try:
        folder = ROOT / "data" / "calibration" / "checkerboards"
        path = folder / f"board_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        camera_manager.save_frame(path)
        return {"image_path": str(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/calibration/camera/upload")
async def upload_checkerboards(files: list[UploadFile] = File(...)) -> dict:
    folder = ROOT / "data" / "calibration" / "checkerboards"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, file in enumerate(files):
        content = await file.read()
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail=f"Invalid checkerboard image: {file.filename}")
        path = folder / f"board_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{index}.jpg"
        cv2.imwrite(str(path), image)
        paths.append(str(path))
    return {"image_paths": paths}


@app.post("/api/calibration/camera")
def camera_calibration(payload: dict[str, Any]) -> dict:
    paths = [Path(p) for p in payload.get("image_paths", [])]
    checkerboard = tuple(payload.get("checkerboard", [9, 6]))
    square_size_mm = float(payload.get("square_size_mm", 25))
    try:
        profile = calibrate_from_checkerboards(paths, checkerboard, square_size_mm)
        save_camera_profile(profile, ROOT / "data" / "calibration" / "camera_profile.json")
        # Geometry from the old lens model must be detected again.
        STATE.update({"sheath_polygon": None, "sheaths": [], "pixel_polygon": None, "nesting_result": None, "scale": None, "scale_source": None})
        # Keep the old scale profile as a backup; its coordinates used the old lens model.
        scale_path = ROOT / "data" / "calibration" / "scale_profile.json"
        if scale_path.exists():
            scale_path.rename(scale_path.with_name(f"scale_profile_before_lens_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"))
        return asdict(profile)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/calibration/aruco-scale")
def save_scale_from_aruco_photo() -> dict:
    if shared_origin_enabled(load_config()):
        raise HTTPException(status_code=409,detail="Use four-marker working-area calibration for the shared ID 12 origin.")
    image_path = STATE.get("captured_image")
    if not image_path:
        raise HTTPException(status_code=400, detail="Capture or upload the ArUco marker photo first")
    image = cv2.imread(image_path)
    if image is None:
        raise HTTPException(status_code=400, detail="Unable to read the marker photo")
    settings = load_config().get("calibration", {}).get("aruco", {})
    profile = detect_aruco_scale_profile(
        _correct_camera_frame(image),
        marker_size_mm=float(settings.get("marker_size_mm", 95)),
        dictionary_name=str(settings.get("dictionary", "DICT_4X4_50")),
        marker_id=settings.get("marker_id"),
    )
    if profile is None:
        raise HTTPException(status_code=400, detail="ArUco marker not detected. Show the full marker and check its configured ID and dictionary.")
    profile.source = "saved_aruco"
    save_scale_profile(profile, ROOT / "data" / "calibration" / "scale_profile.json")
    STATE.update({"sheath_polygon": None, "sheaths": [], "pixel_polygon": None, "nesting_result": None, "scale": None, "scale_source": None})
    return {"saved": True, "scale": asdict(profile)}


@app.get("/api/calibration/workspace-layout")
def get_workspace_layout() -> dict:
    try:
        request = read_workspace_layout(ROOT / "data" / "calibration" / "aruco_viewer_bed_layout.json")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"layout": working_layout(request,load_config()).model_dump(), "coordinate_system": "ID 12 centre; -X toward ID 11; -Y toward ID 13" if shared_origin_enabled(load_config()) else "Working-area coordinates, independent of laser alignment"}


@app.post('/api/calibration/layout-from-machine')
def save_surveyed_layout(request: BedCalibrationRequest) -> dict:
    try:
        result=save_layout_from_machine_positions(ROOT,request,load_config())
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    if result['changed']:
        STATE.update({'sheath_polygon':None,'sheaths':[],'pixel_polygon':None,'nesting_result':None,'scale':None,'scale_source':None})
    return {'saved':True,'changed':result['changed'],'layout':result['layout'],'calibration':calibration_status()}


@app.post("/api/calibration/machine-placement")
def save_measured_machine_placement(request: BedCalibrationRequest) -> dict:
    try:
        save_machine_placement(ROOT,request,load_config())
    except (ValueError,OSError,KeyError,TypeError) as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    return {"saved":True,"calibration":calibration_status()}


@app.post("/api/calibration/workspace-layout")
def save_marker_positions(request: BedCalibrationRequest) -> dict:
    try:
        request=working_layout(request,load_config())
        changed=save_workspace_layout(ROOT/'data/calibration/aruco_viewer_bed_layout.json',request)
    except (ValueError,OSError) as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    if changed:
        STATE.update({'sheath_polygon':None,'sheaths':[],'pixel_polygon':None,'nesting_result':None,'scale':None,'scale_source':None})
    return {'saved':True,'changed':changed,'layout':request.model_dump(),'calibration':calibration_status()}


@app.post("/api/calibration/workspace")
def calibrate_workspace(request: BedCalibrationRequest | None = None) -> dict:
    image_path = STATE.get("captured_image")
    image = cv2.imread(image_path) if image_path else None
    if image is None:
        raise HTTPException(status_code=400, detail="Capture a photo with all four working-area markers first")
    try:
        request = request or read_workspace_layout(ROOT / "data" / "calibration" / "aruco_viewer_bed_layout.json")
        request = working_layout(request,load_config())
        profile = profile_in_frame(workspace_profile(_correct_camera_frame(image), request),load_config())
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = ROOT / "data" / "calibration" / "scale_profile.json"
    if path.exists():
        backup = path.with_name(f"scale_profile_before_workspace_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json")
        backup.write_bytes(path.read_bytes())
    save_scale_profile(profile, path)
    layout_path = path.with_name("aruco_viewer_bed_layout.json")
    save_workspace_layout(layout_path,request)
    STATE.update({"sheath_polygon": None, "sheaths": [], "pixel_polygon": None, "nesting_result": None, "scale": None, "scale_source": None})
    if shared_origin_enabled(load_config()):
        save_laser_alignment(export_alignment())
    # The physical machine/job origin still requires alignment at the marker centre.
    return {"saved": True, "scale": asdict(profile), "layout":request.model_dump(), "coordinate_system": "working_area"}


@app.post("/api/calibration/aruco-bed")
def calibrate_aruco_bed(request: BedCalibrationRequest) -> dict:
    if shared_origin_enabled(load_config()): return calibrate_workspace(request)
    image_path = STATE.get("captured_image")
    image = cv2.imread(image_path) if image_path else None
    if image is None:
        raise HTTPException(status_code=400, detail="Capture or upload a photo showing all four bed markers first")
    try:
        profile = profile_from_bed_markers(_correct_camera_frame(image), request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_scale_profile(profile, ROOT / "data" / "calibration" / "scale_profile.json")
    # Bed geometry already uses laser coordinates; clear any secondary offset.
    save_laser_alignment(CoordinateAlignment())
    STATE.update({"sheath_polygon": None, "sheaths": [], "pixel_polygon": None, "nesting_result": None, "scale": None, "scale_source": None})
    return {"saved": True, "scale": asdict(profile)}


@app.post("/api/calibration/scale")
def scale_calibration(request: ScaleCalibrationRequest) -> dict:
    if shared_origin_enabled(load_config()):
        raise HTTPException(status_code=409,detail="A reference square does not establish the shared ID 12 origin; save four-marker working-area calibration.")
    profile = profile_from_reference_square(
        request.image_points,
        request.square_size_mm,
        (request.image_width, request.image_height),
    )
    save_scale_profile(profile, ROOT / "data" / "calibration" / "scale_profile.json")
    return asdict(profile)


@app.post("/api/calibration/aruco-marker")
def aruco_marker(request: ArucoMarkerRequest) -> dict:
    marker_size_mm = float(load_config().get("calibration", {}).get("aruco", {}).get("marker_size_mm", 100))
    path = write_marker_png(
        ROOT / "data" / "calibration" / f"aruco_{request.dictionary}_id{request.marker_id}_{request.side_pixels}px.png",
        marker_id=request.marker_id,
        side_pixels=request.side_pixels,
        dictionary_name=request.dictionary,
    )
    return {
        "path": str(path),
        "url": _public_data_path(path),
        "print_size_mm": marker_size_mm,
        "note": f"Print the outer black marker square at exactly {marker_size_mm:g} mm x {marker_size_mm:g} mm, excluding white margins. Do not scale-to-fit.",
    }


@app.get("/api/templates")
def list_templates() -> dict:
    try:
        return {"templates": [asdict(template) for template in template_manager.list()]}
    except (OSError, ValueError, TypeError) as exc:
        logger.exception("Unable to read template library")
        raise HTTPException(status_code=400, detail=f"Unable to read template library: {exc}") from exc


@app.post("/api/templates")
def save_templates(templates: list[CuttingTemplate]) -> dict:
    try:
        template_manager.save(templates)
    except (ValueError, OSError) as exc:
        logger.exception("Unable to save template library")
        raise HTTPException(status_code=400, detail=f"Unable to save templates: {exc}") from exc
    return {"templates": [asdict(template) for template in templates]}


@app.delete("/api/templates/{template_id:path}")
def delete_template(template_id: str) -> dict:
    try:
        return {"templates": [asdict(item) for item in template_manager.delete(template_id)]}
    except (ValueError, OSError, TypeError) as exc:
        logger.exception("Unable to delete template")
        raise HTTPException(status_code=400, detail=f"Unable to delete template: {exc}") from exc


@app.post("/api/nest")
def nest(request: NestingRequest) -> dict:
    try: _require_current_workspace_geometry(STATE.get('scale'))
    except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    config = load_config().get("nesting", {})
    if request.sheath_polygons is not None:
        pieces = [{"id": f"sheath-{i + 1}", "mm_polygon": polygon} for i, polygon in enumerate(request.sheath_polygons)]
    elif request.sheath_polygon:
        pieces = [{"id": "sheath-1", "mm_polygon": request.sheath_polygon}]
    else:
        pieces = STATE.get("sheaths") or ([{"id": "sheath-1", "mm_polygon": STATE["sheath_polygon"]}] if STATE.get("sheath_polygon") else [])
    if len(pieces) == 1 and request.sheath_polygons is None and not request.sheath_polygon and STATE.get("sheath_polygon"):
        # Preserve the legacy editable single-boundary state.
        pieces = [{**pieces[0], "mm_polygon": STATE["sheath_polygon"]}]
    if not pieces:
        raise HTTPException(status_code=400, detail="No sheath boundary available")
    results = []
    try:
        for index, piece in enumerate(pieces):
            if WORKFLOW.get("busy"):
                _workflow_update(sheath_index=index + 1, sheath_count=len(pieces), active_sheath=piece["id"])
            result = nesting_engine.nest(
                piece["mm_polygon"], request.templates,
                spacing_mm=float(request.spacing_mm if request.spacing_mm is not None else config.get("spacing_mm", 2)),
                edge_clearance_mm=float(request.edge_clearance_mm if request.edge_clearance_mm is not None else config.get("edge_clearance_mm", 3)),
                rotation_step_deg=float(request.rotation_step_deg if request.rotation_step_deg is not None else config.get("rotation_step_deg", 5)),
                strategy=request.strategy or config.get("strategy", "accurate"),
                precision_mm=float(request.precision_mm if request.precision_mm is not None else config.get("precision_mm", 0.5)),
            )
            row = {**piece, **result.to_dict(), "measurements": polygon_measurements(piece["mm_polygon"])}
            row["placements"] = [{**placement, "sheath_id": piece["id"]} for placement in row["placements"]]
            counts = {}
            for placement in row["placements"]:
                counts[placement["template_id"]] = counts.get(placement["template_id"], 0) + 1
            row["requested_pieces"] = sum(max(0, t.quantity) for t in request.templates if t.enabled)
            row["unplaced_templates"] = [
                {"sheath_id": piece["id"], "template_id": t.id, "name": t.name, "requested": t.quantity,
                 "placed": counts.get(t.id, 0), "unplaced": max(0, t.quantity - counts.get(t.id, 0))}
                for t in request.templates if t.enabled and t.quantity > counts.get(t.id, 0)]
            results.append(row)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    area = sum(row["sheath_area_mm2"] for row in results)
    used = sum(row["used_area_mm2"] for row in results)
    data = {"placements": [p for row in results for p in row["placements"]],
            "sheath_area_mm2": area, "used_area_mm2": used, "utilization": used / area if area else 0,
            "waste": 1 - used / area if area else 1, "requested_pieces": sum(row["requested_pieces"] for row in results),
            "unplaced_templates": [t for row in results for t in row["unplaced_templates"]],
            "sheath_polygon": pieces[0]["mm_polygon"], "sheath_polygons": [piece["mm_polygon"] for piece in pieces],
            "sheaths": results, "quantity_scope": "per_sheath"}
    data["templates"] = [asdict(template) for template in request.templates]
    data["settings"] = {"strategy": request.strategy or config.get("strategy", "accurate"),
                        "precision_mm": float(request.precision_mm if request.precision_mm is not None else config.get("precision_mm", 0.5)),
                        "spacing_mm": float(request.spacing_mm if request.spacing_mm is not None else config.get("spacing_mm", 2)),
                        "edge_clearance_mm": float(request.edge_clearance_mm if request.edge_clearance_mm is not None else config.get("edge_clearance_mm", 3))}
    if data["settings"]["strategy"] == "common_line":
        data["settings"]["spacing_mm"] = 0.0
    data["scale"] = STATE.get("scale")
    STATE["nesting_result"] = data
    if STATE.get("project_dir"):
        (Path(STATE["project_dir"]) / "nesting.json").write_text(json.dumps(data, indent=2))
    return data


@app.get("/api/laser/alignment")
def get_laser_alignment() -> dict:
    if shared_origin_enabled(load_config()): return export_alignment().model_dump()
    path = ROOT / "data" / "calibration" / "laser_alignment.json"
    return CoordinateAlignment.model_validate_json(path.read_text()).model_dump() if path.exists() else CoordinateAlignment().model_dump()


@app.post("/api/laser/alignment")
def save_laser_alignment(alignment: CoordinateAlignment) -> dict:
    if shared_origin_enabled(load_config()) and alignment != export_alignment():
        raise HTTPException(status_code=409,detail="Camera measurements and nesting use ID 12. DXF uses the top-right corner of the nested-template bounding box; extra offsets, rotations and axis reversals are disabled.")
    path = ROOT / "data" / "calibration" / "laser_alignment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(alignment.model_dump_json(indent=2))
    return alignment.model_dump()


@app.post("/api/laser/alignment/preview")
def preview_laser_alignment(alignment: CoordinateAlignment) -> dict:
    sheath = STATE.get("sheath_polygon")
    result = STATE.get("nesting_result")
    if not sheath or not result:
        raise HTTPException(status_code=400, detail="Run detection and nesting before alignment preview")
    try:
        _require_current_workspace_geometry(result.get('scale'))
        if shared_origin_enabled(load_config()): alignment=export_alignment()
        return align_layout(result.get("sheath_polygon", sheath), result["placements"], alignment, sheaths=result.get("sheath_polygons"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/export")
def export(request: ExportRequest) -> dict:
    sheath = STATE.get("sheath_polygon")
    result = STATE.get("nesting_result")
    if not sheath or not result:
        raise HTTPException(status_code=400, detail="Run detection and nesting before export")
    try: _require_current_workspace_geometry(result.get('scale'))
    except ValueError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    export_dir = Path(STATE.get("project_dir") or data_path("exports")).joinpath("exports")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    sheath = result.get("sheath_polygon", sheath)
    sheaths = result.get("sheath_polygons")
    alignment_info = None
    exported_placements = result["placements"]
    shared_cut = result.get("settings", {}).get("strategy") == "common_line"
    if request.format.lower() == "svg":
        path = export_svg(export_dir / f"areca_layout_{stamp}.svg", sheath, result["placements"], request.include_sheath, sheaths=sheaths, shared_cut=shared_cut, y_up=shared_origin_enabled(load_config()))
    elif request.format.lower() == "dxf":
        alignment = export_alignment() if shared_origin_enabled(load_config()) else request.alignment or CoordinateAlignment(**get_laser_alignment())
        try:
            aligned = align_layout(sheath, result["placements"], alignment, sheaths=sheaths)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        exported_placements = aligned["placements"]
        try:
            checked_geometry = exported_placements
            if request.include_sheath:
                checked_geometry = exported_placements + [{"polygon": polygon} for polygon in aligned["sheaths"]]
            bed_check = validate_cut_bounds(checked_geometry, load_config())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        path = export_dxf(export_dir / f"areca_layout_{stamp}.dxf", aligned["sheath"], aligned["placements"], request.include_sheath, sheaths=aligned["sheaths"], shared_cut=shared_cut)
        alignment_info = {key: value for key, value in aligned.items() if key not in {"sheath", "sheaths", "placements"}}
        alignment_info["measurement_calibration"] = STATE.get("scale")
        alignment_info["bed_check"] = bed_check
        path.with_suffix(".alignment.json").write_text(json.dumps(alignment_info, indent=2))
    else:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    if not path.is_file():
        raise HTTPException(status_code=400, detail="Vector export did not create an output file")
    if request.format.lower() == "svg":
        # Match the existing SVG's 0.001mm output rounding; source geometry stays full precision.
        exported_placements = [{**p, "polygon": [[float(f"{x:.3f}"), float(f"{y:.3f}")] for x, y in p["polygon"]]} for p in result["placements"]]
    report_path = path.with_suffix(".csv")
    try:
        write_csv_report(report_path, result, sheath, vector_path=path, export_format=request.format.lower(),
                         exported_placements=exported_placements, calibration=STATE.get("scale"), alignment=alignment_info,
                         workflow_id=WORKFLOW.get("id", "") if WORKFLOW.get("busy") else str(uuid4()),
                         cycle_number=WORKFLOW.get("cycle_number") if WORKFLOW.get("busy") else None,
                         project_id=Path(STATE.get("project_dir") or export_dir).name, source_image=STATE.get("captured_image") or "")
        if not report_path.is_file():
            raise OSError("CSV report was not created")
    except Exception as exc:
        logger.exception("CSV data save failed after vector export")
        raise HTTPException(status_code=400, detail=f"Vector file saved at {path}, but CSV data could not be saved: {exc}. Check available disk space and folder permissions; retry export.") from exc
    output = {"path": str(path), "url": _public_data_path(path), "alignment": alignment_info,
              "csv": {"path": str(report_path), "url": _public_data_path(report_path)}}
    if request.ui_request_id is not None:
        output['ui_request_id'] = str(request.ui_request_id)
    if not WORKFLOW.get("busy"):
        _handoff_dxf(output)
    return output


def _handoff_dxf(output: dict) -> None:
    # Called once after manual export, or after the automatic production record saves.
    if Path(output["path"]).suffix.lower() != ".dxf" or "lasercad" in output:
        return
    if 'laser_control' not in output:
        try:
            job = laser_control_service.receive_generated(output['path'], ui_request_id=output.get('ui_request_id'))
            output['laser_control'] = {'job_id': job['id'], 'status': job['status']}
        except Exception as exc:
            logger.exception('Laser Control job registration failed; export remains saved')
            output['laser_control'] = {'status': 'warning', 'message': str(exc)}
    # Publish the saved job before LaserCAD's window/preview readiness wait.
    # The UI can open Laser Control while this same worker completes the handoff.
    snapshot = workflow_status()
    if snapshot.get('busy') and snapshot.get('artifacts', {}).get('export', {}).get('path') == output['path']:
        artifacts = snapshot['artifacts']
        artifacts['export'] = deepcopy(output)
        _workflow_update(artifacts=artifacts)
    try:
        config = load_config()
        if laser_control_service.host_callback is None:
            config["laser_control"] = {**config.get("laser_control", {}), "host_lasercad":False}
        lasercad_controller.set_job(output["path"])
        output["lasercad"] = _open_lasercad_job(output["path"], config)
        if config.get('lasercad', {}).get('prepare_and_download', {}).get('enabled') is True:
            output['lasercad']['download_automation'] = export_automation.run(output['path'],output['lasercad'])
        else:
            _request_export_boundary_frame(output, config)
        # Existing workspaces keep their saved YAML on upgrade. Missing this new
        # presentation preference must use the requested automatic-split default.
        if not config.get('laser_control', {}).get('host_lasercad', False) and config.get('laser_control', {}).get('split_after_dxf', True) and output['lasercad'].get('status') == 'launch_requested':
            try:
                output['split_view'] = laser_control_service.layout('split')
            except (ValueError, OSError) as exc:
                output['split_view'] = {'status':'unavailable','message':str(exc)}
    except Exception as exc:
        logger.exception("LaserCAD handoff unavailable after successful export")
        output["lasercad"] = {"status": "warning", "message": f"DXF saved, but LaserCAD handoff is unavailable: {exc}"}




def _request_export_boundary_frame(output: dict, config: dict) -> None:
    """Opt-in Run Box after a ready DXF handoff, never Download or Start.

    There is no controller acknowledgement in the UI bridge: report delivery
    only. Neither a timeout nor a failed document match is retried automatically.
    """
    enabled = config.get("lasercad", {}).get("auto_frame_after_dxf", False)
    if enabled is False:
        return
    handoff = output["lasercad"]
    if enabled is not True:
        frame = {"status": "warning", "requested": False,
                 "message": "Automatic Run Box was not sent: auto_frame_after_dxf must be true or false."}
    elif (handoff.get("status") != "launch_requested"
          or handoff.get("preview", {}).get("status") != "refresh_requested"):
        frame = {"status": "skipped", "requested": False,
                 "message": "Automatic Run Box was not sent because the DXF window was not confirmed ready. Refresh LaserCAD status and use Run Box manually."}
    else:
        try:
            delivered = lasercad_controller.request("frame")
            if not delivered.get("requested"):
                raise ControlError("Run Box request delivery was not acknowledged.")
            frame = {**delivered, "status": "requested",
                     "message": "Run Box click delivered to LaserCAD. Boundary motion and completion are unverified. Inspect the traced boundary before pressing Start separately."}
        except Exception as exc:
            logger.exception("Automatic Run Box unavailable after DXF handoff")
            frame = {"status": "warning", "requested": False,
                     "message": f"Automatic Run Box outcome is unconfirmed: {exc}. No automatic retry or Start command was sent."}
    handoff["boundary_frame"] = frame
    logger.info("DXF boundary frame result: %s", frame["status"])


def _workflow_update(**changes) -> None:
    with workflow_status_lock:
        previous = WORKFLOW.get("stage")
        WORKFLOW.update(changes)
        stage = changes.get("stage")
        if (stage and stage != previous) or "events" in changes:
            events = WORKFLOW.setdefault("events", [])
            now = monotonic()
            if events and events[-1]["status"] == "running":
                events[-1]["status"] = "failed" if stage == "Failed" else "completed"
                events[-1]["elapsed_seconds"] = max(0, now - WORKFLOW.get("_stage_started", now))
            if stage not in {None, "Ready", "Completed", "Failed"}:
                events.append({"stage": stage, "status": "running", "elapsed_seconds": 0})
                WORKFLOW["_stage_started"] = now
    if "stage" in changes:
        logger.info("Workflow %s: %s", WORKFLOW["id"], changes["stage"])


@app.get("/api/workflow")
def workflow_status() -> dict:
    with workflow_status_lock:
        status = deepcopy(WORKFLOW)
        started = status.pop("_stage_started", None)
        if started is not None and status.get("events") and status["events"][-1]["status"] == "running":
            status["events"][-1]["elapsed_seconds"] = max(0, monotonic() - started)
        return status


@app.post("/api/workflow", status_code=202)
def start_workflow(request: WorkflowRequest, http_request: Request = None) -> dict:
    global workflow_thread
    identity=getattr(http_request.state,'identity',None) if http_request is not None else None
    user=(identity or {}).get('user') or {}
    request._assigned_workflow_mode='manual' if user.get('role')!='admin' and user.get('workflow_mode')=='manual' else 'automated'
    try:
        laser_control_service.cycles().require_idle()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not operation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Processing is already active. Wait for completion.")
    cycle_started = False
    try:
        stored = {item.id: item for item in template_manager.list()}
        templates = deepcopy(request.templates)
        if request.selected_template_id is not None:
            templates = [item for item in templates if item.id == request.selected_template_id]
        if not templates or not any(item.enabled and item.quantity > 0 for item in templates):
            raise ValueError("Load a valid template, or enable a library template with a positive quantity.")
        if any(item.id not in stored for item in templates):
            raise ValueError("A selected template is missing. Refresh the Template Library.")
        template_manager.validate(templates)
        if request.export.format.lower() not in {"svg", "dxf"}:
            raise ValueError("Choose SVG or DXF for automatic export.")
        snapshot = request.model_copy(deep=True, update={"templates": templates, "sheath_polygon": None, "sheath_polygons": None})
        cycle_id = str(request.request_id or uuid4())
        if request.supervised:
            if load_config().get('laser_control', {}).get('dashboard_enabled', True) is False:
                raise ValueError('Supervised Laser Control is temporarily disabled. Use the standard DXF workflow and LaserCAD tab.')
            if not request.operator_loaded or request.export.format.lower() != 'dxf':
                raise ValueError('Confirm sheath loading and choose DXF for supervised cutting.')
            laser_control_service.cycles().begin(cycle_id, laser_control_service.cycle_session)
            cycle_started = True
        if request.space_cycle_id is not None:
            if request.supervised or request.export.format.lower()!='dxf': raise ValueError('The Space workflow requires standard DXF export.')
            options=load_config().get('lasercad',{}).get('prepare_and_download',{})
            if options.get('enabled') is not True: raise ValueError('Enable automatic LaserCAD preparation/download in Settings before the first Space press.')
            from app.laser_control.machine_workflow import enabled as motion_enabled
            if motion_enabled(load_config()): raise ValueError('Turn off the separate SDK machine workflow before using the operator Space sequence.')
            space_cycle.claim(str(request.space_cycle_id),request.space_revision,'ready_process',cycle_id,workflow_mode=request._assigned_workflow_mode)
        elif space_cycle.status()['stage']!='ready_process':
            raise ValueError('Use the current Space cycle; a second capture is blocked until completion/reset.')
        snapshot = snapshot.model_copy(update={'request_id': UUID(cycle_id)})
        _workflow_update(id=cycle_id, busy=True, stage="Capturing image", error=None, result=None,
                         supervised=request.supervised,
                         cycle_number=WORKFLOW.get("cycle_number", 0) + 1, events=[], artifacts={},
                         sheath_index=None, sheath_count=None, active_sheath=None)
        workflow_thread = Thread(target=_run_workflow, args=(snapshot,), name="capture-to-export")
        response = workflow_status()
        workflow_thread.start()
        return response
    except Exception as exc:
        operation_lock.release()
        if cycle_started:
            try:
                laser_control_service.cycles().failed(cycle_id, str(exc))
            except Exception:
                logger.exception('Could not record preparation startup failure')
        if workflow_thread is not None and workflow_thread.ident is None:
            workflow_thread = None
        if isinstance(exc, HTTPException):
            raise
        _workflow_update(busy=False, stage="Failed", error=str(exc), result=None)
        logger.exception("Unable to start automated workflow")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _save_daily_production(status: dict) -> dict:
    artifacts = status["artifacts"]
    try:
        path = record_daily_production(ROOT / "data" / "production", workflow_id=status["id"],
                                       cycle_number=status.get("cycle_number"), nesting=artifacts["nesting"],
                                       detection=artifacts["detection"], output=artifacts["export"], events=status.get("events", []))
    except Exception as exc:
        raise RuntimeError(f"Exported files are saved at {artifacts['export']['path']}, but the daily production CSV could not be saved: {exc}. Close the daily CSV in Excel, check disk space and permissions, then use Retry daily record in Data.") from exc
    return {"path": str(path), "url": _public_data_path(path)}


@app.post("/api/production/recover")
def recover_production_reports() -> dict:
    # Replay saved export records only. No image capture, nesting or export occurs.
    try:
        checked, paths = recover_daily_production(ROOT / "data" / "production", ROOT / "data" / "projects")
    except Exception as exc:
        logger.exception("Daily production recovery failed")
        raise HTTPException(status_code=400, detail=f"Daily production recovery could not finish: {exc}. Close the CSV in Excel and check storage permissions.") from exc
    return {"checked": checked, "production_reports": [_production_report_info(path) for path in paths]}


@app.post("/api/production/retry")
def retry_daily_production() -> dict:
    # The existing mutation middleware owns operation_lock for this endpoint.
    status = workflow_status()
    artifacts = status.get("artifacts", {})
    if status.get("busy") or status.get("stage") != "Failed" or not all(artifacts.get(key) for key in ("nesting", "detection", "export")):
        raise HTTPException(status_code=400, detail="No exported automatic cycle is waiting for a daily record. Failed captures and exports are not production.")
    try:
        artifacts["export"]["daily_production"] = _save_daily_production(status)
        _handoff_dxf(artifacts["export"])
    except Exception as exc:
        logger.exception("Daily production record retry failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events = status.get("events", [])
    for event in events:
        if event["stage"] == "Exporting" and event["status"] == "failed":
            event["status"] = "completed"
    result = {"detection": artifacts["detection"], "nesting": artifacts["nesting"], "export": artifacts["export"],
              "template_ids": [item["id"] for item in artifacts["nesting"].get("templates", [])]}
    _workflow_update(stage="Completed", busy=False, error=None, events=events, artifacts=artifacts, result=result)
    return {"report": artifacts["export"]["daily_production"], "workflow": workflow_status()}


def _run_workflow(request: WorkflowRequest) -> None:
    try:
        _, image_path = _capture_raw()
        artifacts = {"captured_image": _public_data_path(image_path)}
        _workflow_update(artifacts=deepcopy(artifacts))
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError("Unable to read captured image. Check camera output and storage.")
        if int(image.max()) == int(image.min()):
            raise ValueError("Captured image is blank. Check the USB camera, lens cover, lighting and connection before retrying.")
        _workflow_update(stage="Correcting distortion")
        corrected = _correct_camera_frame(image)
        preview = _save_corrected_preview(image_path, corrected)
        artifacts.update(corrected_image=_public_data_path(preview), preview_crop=STATE.get("preview_crop"))
        _workflow_update(stage="Detecting material and converting coordinates", artifacts=deepcopy(artifacts))
        detection = _detect_corrected(corrected)
        artifacts["detection"] = detection
        _workflow_update(stage="Processing and applying nesting priority", artifacts=deepcopy(artifacts))
        nesting = nest(NestingRequest(**request.model_dump(exclude={"export", "selected_template_id", "request_id"})))
        if not nesting["placements"]:
            raise ValueError("No template fits the detected material. Check dimensions, quantities and clearance.")
        artifacts["nesting"] = nesting
        _workflow_update(stage="Exporting", artifacts=deepcopy(artifacts))
        output = export(request.export)
        if not Path(output["path"]).is_file():
            raise OSError("Export did not create an output file. Check storage permissions.")
        artifacts["export"] = output
        _workflow_update(artifacts=deepcopy(artifacts))
        output["daily_production"] = _save_daily_production(workflow_status())
        _handoff_dxf(output)
        if request.supervised:
            job_id = output.get('laser_control', {}).get('job_id')
            if not job_id:
                raise ValueError('DXF exported, but supervised job registration failed. Review the saved export before retrying.')
            laser_control_service.cycles().prepared(str(request.request_id), laser_control_service.history().get(job_id))
        if request.space_cycle_id is not None:
            import hashlib
            preparation=output.get('lasercad',{}).get('download_automation',{})
            if preparation.get('status')!='download_requested' or not preparation.get('download_requested'):
                raise ValueError('Space cycle stopped before Download Document was requested: '+str(preparation.get('message',output.get('lasercad',{}).get('message','Review LaserCAD.'))))
            if space_cycle.status().get('request_id')!=str(request.request_id):raise ValueError('The processing cycle changed.')
            path=Path(output['path']).resolve()
            window=lasercad_controller._window(job_scoped=True,require_ready=False)
            manual=request._assigned_workflow_mode=='manual'
            space_cycle.finish('processing','awaiting_manual_completion' if manual else 'ready_start', job={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'mapping':output['lasercad'].get('import_mapping'),'window':window,
                'config':config_fingerprint(ROOT,load_config()),'target':preparation['communication_target'],'workflow_mode':request._assigned_workflow_mode},
                message='Download Document requested. Use LaserCAD controls manually for the remaining machine actions. After cutting has finished and the machine is idle, press Space to reset.' if manual else 'Download Document requested. Wait until transfer is finished and the controller has the correct file; then press Space to request Start.')
        _workflow_update(stage="Completed", artifacts=deepcopy(artifacts), result={"detection": detection, "nesting": nesting, "export": output,
                         "template_ids": [item.id for item in request.templates]})
    except Exception as exc:
        if request.space_cycle_id is not None: space_cycle.interrupt(str(exc))
        logger.exception("Automated workflow failed at %s", WORKFLOW["stage"])
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _workflow_update(stage="Failed", error=f"{WORKFLOW['stage']}: {detail}", result=None)
        if request.supervised:
            try:
                laser_control_service.cycles().failed(str(request.request_id), detail)
            except Exception:
                logger.exception('Supervised cycle failure needs operator recovery')
    finally:
        _workflow_update(busy=False)
        operation_lock.release()


@app.post("/api/project/save")
def save_project(request: SaveProjectRequest) -> dict:
    project_dir = Path(STATE.get("project_dir") or timestamped_capture_dir(ROOT / "data" / "projects"))
    metadata = {
        "name": request.name or project_dir.name,
        "saved_at": datetime.now().isoformat(),
        "state": STATE,
        "config": load_config(),
        "laser_alignment": get_laser_alignment(),
    }
    (project_dir / "project.json").write_text(json.dumps(metadata, indent=2))
    STATE["project_dir"] = str(project_dir)
    return {"project_dir": str(project_dir)}


def _production_report_info(path: Path) -> dict:
    return {"name": path.name, "path": str(path), "url": _public_data_path(path),
            "excel_url": f"/api/production/{path.stem.removeprefix('production_')}/excel"}


@app.get("/api/production/{production_date}/excel")
def download_production_excel(production_date: str):
    from app.export.daily_production import _WRITE_LOCK
    from app.export.production_excel import write_daily_workbook
    try:
        day = datetime.strptime(production_date, "%Y-%m-%d").date().isoformat()
        if day != production_date:
            raise ValueError("Use a production date in YYYY-MM-DD format")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Choose a valid production date in Data.") from exc
    source = ROOT / "data" / "production" / f"production_{day}.csv"
    if not source.is_file():
        raise HTTPException(status_code=404, detail="No production records exist for this date.")
    try:
        with _WRITE_LOCK:
            path = write_daily_workbook(source)
    except Exception as exc:
        logger.exception("Production Excel download failed")
        raise HTTPException(status_code=400, detail=f"Could not prepare Excel report: {exc}. Close the workbook in Excel and check disk space and permissions. The original CSV is preserved.") from exc
    return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/projects")
def list_projects() -> dict:
    projects_dir = ROOT / "data" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    reports = [_production_report_info(path)
               for path in sorted((ROOT / "data" / "production").glob("production_????-??-??.csv"), reverse=True) if path.is_file()]
    return {"projects": [str(path) for path in sorted(projects_dir.iterdir()) if path.is_dir()], "production_reports": reports}



class LaserCADCommand(BaseModel):
    action: str
    request_id: UUID
    x: float | None = None
    y: float | None = None
    confirm_start: bool = False
    job_scoped: bool = False
    expected_panel: dict | None = None
    supervised_cycle_id: str | None = None


laser_request_lock = Lock()
laser_request_ids: dict[str, bool] = {}
manual_datum_cancel = Event()


class ManualDatumRequest(BaseModel):
    request_id: UUID
    operator_confirmed_idle: bool = False


@app.post('/api/laser-control/manual-datum')
def manual_datum(payload: ManualDatumRequest):
    # This route is independent of the Space cycle and never advances/resets it.
    if not payload.operator_confirmed_idle:
        raise HTTPException(409, 'Confirm the machine is idle before requesting Datum.')
    def idle_guard():
        if manual_datum_cancel.is_set():
            raise ValueError('Datum was cancelled by Stop. No further request was sent.')
        if WORKFLOW.get('busy') or space_cycle.status()['stage'] not in {'ready_process','interrupted'}:
            raise ValueError('Finish the active workflow before requesting manual Datum.')
        if laser_control_service.cycles().status()['active']:
            raise ValueError('Resolve the supervised cycle before requesting manual Datum.')
        if machine_workflow_enabled(load_config()):
            raise ValueError('Disable the separate automatic machine workflow before using manual Datum.')
    try:
        # A prior Stop must not prevent a later explicit, confirmed manual request.
        manual_datum_cancel.clear()
        idle_guard()
        with laser_request_lock:
            key=str(payload.request_id)
            if key in laser_request_ids:
                raise ValueError('This Datum request was already submitted. It will not be retried.')
            laser_request_ids[key]=True
            if len(laser_request_ids)>512:laser_request_ids.pop(next(iter(laser_request_ids)))
        from app.laser_control.space_native import datum
        result=datum(lasercad_controller,load_config().get('lasercad',{}).get('prepare_and_download',{}),idle_guard,host=laser_control_service.host_callback)
        logger.info('Standalone manual Datum requested: %s', payload.request_id)
        return {'status':'requested','message':'Configured USB selected; Datum requested through background LaserCAD. Wait until the head stops before continuing. The workflow was not advanced or reset.','result':result}
    except (ControlError,OSError,ValueError) as exc:
        raise HTTPException(409,str(exc)) from exc


@app.get("/api/laser/control/status")
def laser_control_status() -> dict:
    return lasercad_controller.status()


@app.post("/api/laser/control/open")
def laser_control_open() -> dict:
    try:
        return lasercad_controller.open()
    except (ControlError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _laser_control_request(payload: LaserCADCommand, *, stop_only=False) -> dict:
    if (payload.action == "stop") != stop_only:
        raise HTTPException(status_code=400, detail="Use the dedicated Stop endpoint for Stop.")
    with laser_request_lock:
        key = str(payload.request_id)
        if key in laser_request_ids:
            raise HTTPException(status_code=409, detail="This request was already submitted. Refresh status; commands are never retried automatically.")
        laser_request_ids[key] = True
        if len(laser_request_ids) > 512:
            laser_request_ids.pop(next(iter(laser_request_ids)))
    try:
        # Deliver a priority native Stop before accessing the optional ledger.
        cycles = laser_control_service.cycles() if payload.action != 'stop' else None
        active = cycles.status()['active'] if cycles else None
        if active:
            if payload.action == 'start':
                cycles.claim_dispatch(payload.supervised_cycle_id, payload.request_id)
            elif payload.action == 'pause' and (active['state'] not in {'start_pending','awaiting_completion','interrupted'} or not payload.confirm_start or not payload.job_scoped):
                raise ValueError('Use the operator-confirmed Pause/Resume controls for the active cycle in Laser Control.')
            elif active['state'] in {'start_pending','awaiting_completion','interrupted'} and payload.action not in {'pause'}:
                raise ValueError('Only Pause/Continue or Stop are available until this supervised cutting cycle is resolved.')
        kwargs = dict(x=payload.x, y=payload.y, confirm_start=payload.confirm_start)
        if payload.job_scoped:
            kwargs['job_scoped'] = True
        if payload.expected_panel is not None:
            kwargs['expected_panel'] = payload.expected_panel
        result = lasercad_controller.request(payload.action, **kwargs)
        if payload.action == 'stop':
            try:
                laser_control_service.cycles().interrupted('Stop requested. Observe the machine and confirm it is stopped before ending this cycle.')
            except Exception:
                logger.exception('Stop was requested, but the supervised ledger could not be updated; inspect machine state')
        logger.info("LaserCAD explicit request %s: %s", payload.request_id, payload.action)
        return result
    except (ControlError, OSError, ValueError) as exc:
        logger.warning("LaserCAD request %s outcome: %s", payload.action, exc)
        if payload.action == 'stop':
            try:
                laser_control_service.cycles().interrupted('Stop delivery is unconfirmed. Inspect the machine and confirm it is stopped before clearing this cycle.')
            except Exception:
                logger.exception('Could not record the uncertain Stop outcome')
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/laser/control/command")
def laser_control_command(payload: LaserCADCommand) -> dict:
    if WORKFLOW.get("busy"):
        raise HTTPException(status_code=409, detail="Wait for DXF export to finish before controlling LaserCAD.")
    return _laser_control_request(payload)


@app.post("/api/laser/control/stop")
def laser_control_stop(payload: LaserCADCommand) -> dict:
    manual_datum_cancel.set()
    machine_workflow.cancel()
    try:space_cycle.interrupt("Stop requested. Confirm machine idle before resetting the Space cycle.")
    except Exception:logger.exception("Could not journal Space interruption; priority Stop will still be delivered")
    # Authenticated Stop bypasses the workflow/mutation lock; it still targets
    # only the single verified LaserCAD Stop button. This is not an emergency stop.
    return _laser_control_request(payload, stop_only=True)


@app.get('/api/laser-control/machine-workflow')
def machine_workflow_status():
    return machine_workflow.preview()


@app.get("/api/laser/status")
def laser_status() -> dict:
    return laser_controller.status()


def _public_data_path(path: Path) -> str:
    path = path.resolve()
    try:
        return "/data/" + path.relative_to((ROOT / "data").resolve()).as_posix()
    except ValueError:
        return str(path)


def _set_active_image(project_dir: Path, image_path: Path) -> None:
    STATE.update(
        {
            "project_dir": str(project_dir),
            "captured_image": str(image_path),
            "sheath_polygon": None,
            "sheaths": [],
            "pixel_polygon": None,
            "nesting_result": None,
            "scale_source": None,
            "scale": None,
        }
    )


def _looks_like_non_machine_camera(name: str) -> bool:
    lowered = name.lower()
    return "macbook" in lowered or "iphone" in lowered or "facetime" in lowered or "continuity" in lowered


def _looks_like_machine_camera(name: str) -> bool:
    lowered = name.lower()
    return ("usb" in lowered or "imx" in lowered or "waveshare" in lowered) and not _looks_like_non_machine_camera(name)


def _is_hidden_camera(name: str) -> bool:
    lowered = name.lower()
    return "iphone" in lowered or "continuity" in lowered


def _visible_cameras(cameras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [camera for camera in cameras if is_usb_camera(camera)]


# Access checks wrap existing routes; workflow functions and worker order are unchanged.
from app.security.access import install_access_control
install_access_control(app, lambda: ROOT, lambda: {"config": load_config(), "camera": camera_manager.status()})


class SpaceAction(BaseModel):
    cycle_id: UUID
    revision: int
    request_id: UUID
    expected_stage: str

class SpaceReset(BaseModel):
    cycle_id: UUID
    revision: int
    completed: bool = False
    operator_confirmed_idle: bool = False

@app.get('/api/laser-control/space-cycle')
def space_cycle_status():
    return space_cycle.status()

@app.post('/api/laser-control/space-cycle/action')
def space_cycle_action(payload: SpaceAction):
    if payload.expected_stage != 'ready_start':
        raise HTTPException(409,'The first Space press uses the capture-to-download workflow.')
    if not operation_lock.acquire(blocking=False):raise HTTPException(409,'An operation is active; this Space press was ignored.')
    claimed=False
    try:
        from app.laser_control.space_native import prepare_job_panel
        space_cycle.claim(str(payload.cycle_id),payload.revision,payload.expected_stage,payload.request_id)
        claimed=True
        def guard():
            current=space_cycle.status()
            if current.get('request_id')!=str(payload.request_id) or current['stage'] != 'start_pending':
                raise ValueError('The cycle was stopped or interrupted; no further action is authorized.')
        job=space_cycle.status()['job']
        if job['config']!=config_fingerprint(ROOT,load_config()):raise ValueError('Calibration or settings changed since download.')
        expected=prepare_job_panel(lasercad_controller,job,job['target'])
        action='start'
        guard()
        result=lasercad_controller.request(action,job_scoped=True,confirm_start=action=='start',expected_panel=expected)
        return space_cycle.finish('start_pending','awaiting_completion',last_request=result,
            message='Start requested. Wait until cutting has finished and the machine is idle, then press Space once to confirm completion and reset for the next sheath. No additional confirmation dialog will open.')
    except Exception as exc:
        if claimed:space_cycle.interrupt(str(exc)+' No automatic retry; inspect the machine before recovery.')
        raise HTTPException(409,str(exc)) from exc
    finally:operation_lock.release()

@app.post('/api/laser-control/space-cycle/reset')
def space_cycle_reset(payload: SpaceReset):
    if not operation_lock.acquire(blocking=False):raise HTTPException(409,'Wait for the current operation.')
    try:
        return space_cycle.reset(str(payload.cycle_id),payload.revision,payload.completed,payload.operator_confirmed_idle)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    finally:operation_lock.release()
