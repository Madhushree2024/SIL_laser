from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from dataclasses import asdict, dataclass
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.calibration.aruco_scale import detect_aruco_scale_profile, draw_detected_markers, write_marker_png
from app.calibration.scale_calibration import load_profile as load_scale_profile, profile_from_reference_square, save_profile as save_scale_profile
from app.camera.camera_manager import CameraManager
from app.camera.frame_capture import timestamped_capture_dir
from app.config.settings import ROOT, data_path, load_config, save_config
from app.export.dxf_export import export_dxf
from app.export.svg_export import export_svg
from app.laser.laser_controller import LaserController
from app.nesting.nesting_engine import NestingEngine
from app.templates.template_geometry import CuttingTemplate
from app.templates.template_manager import TemplateManager
from app.vision.measurements import polygon_measurements
from app.vision.sheath_detection import ClassicalSheathDetector


# ---------------------------------------------------------------------------
# Camera calibration
# ---------------------------------------------------------------------------
# This application uses the existing camera_calibration.npz directly.
# No checkerboard calibration or camera_profile.json is used here.

CAMERA_CALIBRATION_FILE = ROOT / "camera_calibration.npz"


@dataclass
class CameraCalibrationProfile:
    camera_matrix: list[list[float]]
    distortion_coefficients: list[float]
    image_width: int
    image_height: int
    rms_error: float
    mean_reprojection_error: float
    new_camera_matrix: list[list[float]]


class CameraCalibrator:
    """Load the existing NPZ calibration and undistort captured images."""

    def __init__(self, calibration_file: Path = CAMERA_CALIBRATION_FILE):
        self.enabled = False
        self.camera_matrix = None
        self.distortion = None
        self.new_camera_matrix = None
        self.calibration_width = None
        self.calibration_height = None
        self.rms_error = None
        self.mean_reprojection_error = None
        self._map_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

        if not calibration_file.exists():
            raise RuntimeError(
                f"Camera calibration file not found: {calibration_file}"
            )

        try:
            with np.load(calibration_file) as data:
                self.camera_matrix = data["camera_matrix"].astype(np.float64)
                self.distortion = data["distortion"].astype(np.float64)
                self.new_camera_matrix = data["new_camera_matrix"].astype(np.float64)
                self.calibration_width = int(data["image_width"])
                self.calibration_height = int(data["image_height"])
                self.rms_error = float(data["rms_error"])
                self.mean_reprojection_error = float(
                    data["mean_reprojection_error"]
                )

            self.enabled = True

        except KeyError as exc:
            raise RuntimeError(
                f"Required calibration field {exc!s} is missing from "
                f"{calibration_file}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load camera calibration: {calibration_file}: {exc}"
            ) from exc

    def _scaled_matrices(
        self, width: int, height: int
    ) -> tuple[np.ndarray, np.ndarray]:
        if (
            width == self.calibration_width
            and height == self.calibration_height
        ):
            return self.camera_matrix, self.new_camera_matrix

        sx = width / float(self.calibration_width)
        sy = height / float(self.calibration_height)

        camera_matrix = self.camera_matrix.copy()
        new_camera_matrix = self.new_camera_matrix.copy()

        camera_matrix[0, 0] *= sx
        camera_matrix[1, 1] *= sy
        camera_matrix[0, 2] *= sx
        camera_matrix[1, 2] *= sy

        new_camera_matrix[0, 0] *= sx
        new_camera_matrix[1, 1] *= sy
        new_camera_matrix[0, 2] *= sx
        new_camera_matrix[1, 2] *= sy

        return camera_matrix, new_camera_matrix

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled or frame is None:
            return frame

        height, width = frame.shape[:2]
        key = (width, height)

        maps = self._map_cache.get(key)
        if maps is None:
            camera_matrix, new_camera_matrix = self._scaled_matrices(
                width, height
            )

            maps = cv2.initUndistortRectifyMap(
                camera_matrix,
                self.distortion,
                None,
                new_camera_matrix,
                key,
                cv2.CV_16SC2,
            )
            self._map_cache[key] = maps

        return cv2.remap(
            frame,
            maps[0],
            maps[1],
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )

    def profile(self) -> CameraCalibrationProfile:
        return CameraCalibrationProfile(
            camera_matrix=self.camera_matrix.tolist(),
            distortion_coefficients=self.distortion.ravel().tolist(),
            image_width=self.calibration_width,
            image_height=self.calibration_height,
            rms_error=self.rms_error,
            mean_reprojection_error=self.mean_reprojection_error,
            new_camera_matrix=self.new_camera_matrix.tolist(),
        )


CAMERA_CALIBRATOR = CameraCalibrator()


def undistort_image(image: np.ndarray) -> np.ndarray:
    """Apply the loaded NPZ calibration to an image."""
    return CAMERA_CALIBRATOR.undistort(image)


app = FastAPI(title="Areca Cutter", version="0.1.0")
camera_manager = CameraManager()
template_manager = TemplateManager(ROOT / "data" / "templates" / "templates.json")
detector = ClassicalSheathDetector()
nesting_engine = NestingEngine()
laser_controller = LaserController()

STATE: dict[str, Any] = {
    "project_dir": None,
    "captured_image": None,
    "sheath_polygon": None,
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
    templates: list[CuttingTemplate]
    spacing_mm: float | None = None
    edge_clearance_mm: float | None = None
    rotation_step_deg: float | None = None
    strategy: str | None = None


class ExportRequest(BaseModel):
    format: str
    include_sheath: bool = True


class SaveProjectRequest(BaseModel):
    name: str | None = None


class ArucoMarkerRequest(BaseModel):
    marker_id: int = 0
    side_pixels: int = 1000
    dictionary: str = "DICT_4X4_50"


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
app.mount("/data", StaticFiles(directory=ROOT / "data"), name="data")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    return load_config()


@app.post("/api/config")
def update_config(config: dict[str, Any]) -> dict:
    save_config(config)
    return config


@app.get("/api/state")
def current_state() -> dict:
    image_path = Path(STATE["captured_image"]) if STATE.get("captured_image") else None
    return {
        "project_dir": STATE.get("project_dir"),
        "image": _public_data_path(image_path) if image_path and image_path.exists() else None,
        "mm_polygon": STATE.get("sheath_polygon"),
        "pixel_polygon": STATE.get("pixel_polygon"),
        "measurements": polygon_measurements(STATE["sheath_polygon"]) if STATE.get("sheath_polygon") else None,
        "scale": STATE.get("scale"),
        "scale_source": STATE.get("scale_source"),
        "nesting_result": STATE.get("nesting_result"),
    }


@app.get("/api/cameras")
def cameras() -> dict:
    return {"cameras": _visible_cameras(camera_manager.enumerate_cameras())}


@app.post("/api/camera/open")
def open_camera(request: CameraOpenRequest) -> dict:
    try:
        cameras = _visible_cameras(camera_manager.enumerate_cameras())
        selected = next((camera for camera in cameras if camera["index"] == request.index), None)
        if selected is None:
            raise RuntimeError(f"Camera index {request.index} is not available for this machine")
        if selected and not request.allow_non_external and _looks_like_non_machine_camera(selected["name"]):
            raise RuntimeError(f"Refusing to open non-machine camera: {selected['name']}")
        return camera_manager.open(request.index, request.width, request.height, request.fps)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/camera/open-machine")
def open_machine_camera() -> dict:
    cameras = _visible_cameras(camera_manager.enumerate_cameras())
    preferred = next((camera for camera in cameras if _looks_like_machine_camera(camera["name"]) and camera["status"] == "CONNECTED"), None)
    if preferred is None:
        preferred = next((camera for camera in cameras if not _looks_like_non_machine_camera(camera["name"]) and camera["status"] == "CONNECTED"), None)
    if preferred is None:
        raise HTTPException(status_code=400, detail="No connected USB/IMX machine camera found")
    try:
        return camera_manager.open(preferred["index"], 1920, 1080, 30)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/camera/close")
def close_camera() -> dict:
    camera_manager.close()
    return {"connected": False}


@app.get("/api/camera/status")
def camera_status() -> dict:
    return camera_manager.status()


@app.post("/api/camera/control")
def camera_control(request: CameraControlRequest) -> dict:
    try:
        return {"ok": camera_manager.set_control(request.name, request.value)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/camera/stream")
def camera_stream() -> StreamingResponse:
    try:
        return StreamingResponse(camera_manager.mjpeg_stream(), media_type="multipart/x-mixed-replace; boundary=frame")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/capture")
def capture() -> dict:
    try:
        project_dir = timestamped_capture_dir(ROOT / "data" / "projects")
        image_path = camera_manager.save_frame(project_dir / "original.jpg")
        _set_active_image(project_dir, image_path)
        return {"project_dir": str(project_dir), "image": _public_data_path(image_path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)) -> dict:
    project_dir = timestamped_capture_dir(ROOT / "data" / "projects")
    image_path = project_dir / "original.jpg"
    image_path.write_bytes(await file.read())
    _set_active_image(project_dir, image_path)
    return {"project_dir": str(project_dir), "image": _public_data_path(image_path)}


@app.post("/api/detect")
def detect() -> dict:
    image_path = STATE.get("captured_image")
    if not image_path:
        raise HTTPException(status_code=400, detail="Capture or upload an image first")
    image = cv2.imread(image_path)
    if image is None:
        raise HTTPException(status_code=400, detail="Unable to read captured image")
    config = load_config()
    camera_profile = CAMERA_CALIBRATOR.profile()
    scale_profile = load_scale_profile(ROOT / "data" / "calibration" / "scale_profile.json")

    # Apply the supplied camera_calibration.npz before any vision processing.
    calibration_image = undistort_image(image)

    aruco_config = config.get("calibration", {}).get("aruco", {})
    aruco_profile = None
    if aruco_config.get("enabled", True):
        aruco_profile = detect_aruco_scale_profile(
            calibration_image,
            marker_size_mm=float(aruco_config.get("marker_size_mm", 100)),
            dictionary_name=str(aruco_config.get("dictionary", "DICT_4X4_50")),
            marker_id=aruco_config.get("marker_id"),
        )
        if aruco_profile is not None and STATE.get("project_dir"):
            save_scale_profile(aruco_profile, Path(STATE["project_dir"]) / "aruco_scale_profile.json")
            marker_overlay = draw_detected_markers(
                calibration_image,
                str(aruco_config.get("dictionary", "DICT_4X4_50")),
            )
            cv2.imwrite(
                str(Path(STATE["project_dir"]) / "aruco_overlay.jpg"),
                marker_overlay,
            )

    active_scale_profile = aruco_profile or scale_profile

    try:
        result = detector.detect(
            calibration_image,
            Path(STATE["project_dir"]),
            camera_profile=camera_profile,
            scale_profile=active_scale_profile,
            min_area_px=float(config.get("vision", {}).get("min_area_px", 5000)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    STATE["sheath_polygon"] = result.mm_polygon
    STATE["pixel_polygon"] = result.pixel_polygon
    STATE["scale_source"] = active_scale_profile.source if active_scale_profile else "pixels"
    STATE["scale"] = asdict(active_scale_profile) if active_scale_profile else None
    contour_path = Path(STATE["project_dir"]) / "contour.json"
    contour_path.write_text(json.dumps(asdict(result), indent=2))
    return {
        "pixel_polygon": result.pixel_polygon,
        "mm_polygon": result.mm_polygon,
        "measurements": polygon_measurements(result.mm_polygon),
        "scale": STATE["scale"],
        "scale_source": STATE["scale_source"],
        "mask": _public_data_path(Path(result.mask_path)) if result.mask_path else None,
        "overlay": _public_data_path(Path(result.overlay_path)) if result.overlay_path else None,
        "aruco_overlay": _public_data_path(Path(STATE["project_dir"]) / "aruco_overlay.jpg")
        if aruco_profile is not None and STATE.get("project_dir")
        else None,
    }


@app.get("/api/calibration/status")
def calibration_status() -> dict:
    scale_profile = load_scale_profile(
        ROOT / "data" / "calibration" / "scale_profile.json"
    )
    return {
        "camera_calibrated": CAMERA_CALIBRATOR.enabled,
        "camera": asdict(CAMERA_CALIBRATOR.profile()),
        "camera_calibration_file": str(CAMERA_CALIBRATION_FILE),
        "scale_calibrated": scale_profile is not None,
        "scale": asdict(scale_profile) if scale_profile else None,
    }


@app.post("/api/calibration/scale")
def scale_calibration(request: ScaleCalibrationRequest) -> dict:
    profile = profile_from_reference_square(
        request.image_points,
        request.square_size_mm,
        (request.image_width, request.image_height),
    )
    save_scale_profile(profile, ROOT / "data" / "calibration" / "scale_profile.json")
    return asdict(profile)


@app.post("/api/calibration/aruco-marker")
def aruco_marker(request: ArucoMarkerRequest) -> dict:
    path = write_marker_png(
        ROOT / "data" / "calibration" / f"aruco_{request.dictionary}_id{request.marker_id}_{request.side_pixels}px.png",
        marker_id=request.marker_id,
        side_pixels=request.side_pixels,
        dictionary_name=request.dictionary,
    )
    return {
        "path": str(path),
        "url": _public_data_path(path),
        "print_size_mm": 100,
        "note": "Print this square at exactly 100 mm x 100 mm. Do not scale-to-fit.",
    }


@app.get("/api/templates")
def list_templates() -> dict:
    return {"templates": [asdict(template) for template in template_manager.list()]}


@app.post("/api/templates")
def save_templates(templates: list[CuttingTemplate]) -> dict:
    template_manager.save(templates)
    return {"templates": [asdict(template) for template in templates]}


@app.post("/api/nest")
def nest(request: NestingRequest) -> dict:
    config = load_config().get("nesting", {})
    sheath = request.sheath_polygon or STATE.get("sheath_polygon")
    if not sheath:
        raise HTTPException(status_code=400, detail="No sheath boundary available")
    try:
        result = nesting_engine.nest(
            sheath,
            request.templates,
            spacing_mm=float(request.spacing_mm if request.spacing_mm is not None else config.get("spacing_mm", 2)),
            edge_clearance_mm=float(request.edge_clearance_mm if request.edge_clearance_mm is not None else config.get("edge_clearance_mm", 3)),
            rotation_step_deg=float(request.rotation_step_deg if request.rotation_step_deg is not None else config.get("rotation_step_deg", 5)),
            strategy=request.strategy or config.get("strategy", "maximum_pieces"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = result.to_dict()
    data["sheath_polygon"] = sheath
    data["scale"] = STATE.get("scale")
    STATE["nesting_result"] = data
    if STATE.get("project_dir"):
        (Path(STATE["project_dir"]) / "nesting.json").write_text(json.dumps(data, indent=2))
    return data


@app.post("/api/export")
def export(request: ExportRequest) -> dict:
    sheath = STATE.get("sheath_polygon")
    result = STATE.get("nesting_result")
    if not sheath or not result:
        raise HTTPException(status_code=400, detail="Run detection and nesting before export")
    export_dir = Path(STATE.get("project_dir") or data_path("exports")).joinpath("exports")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if request.format.lower() == "svg":
        path = export_svg(export_dir / f"areca_layout_{stamp}.svg", sheath, result["placements"], request.include_sheath)
    elif request.format.lower() == "dxf":
        path = export_dxf(export_dir / f"areca_layout_{stamp}.dxf", sheath, result["placements"], request.include_sheath)
    else:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    return {"path": str(path), "url": _public_data_path(path)}


@app.post("/api/project/save")
def save_project(request: SaveProjectRequest) -> dict:
    project_dir = Path(STATE.get("project_dir") or timestamped_capture_dir(ROOT / "data" / "projects"))
    metadata = {
        "name": request.name or project_dir.name,
        "saved_at": datetime.now().isoformat(),
        "state": STATE,
        "config": load_config(),
    }
    (project_dir / "project.json").write_text(json.dumps(metadata, indent=2))
    STATE["project_dir"] = str(project_dir)
    return {"project_dir": str(project_dir)}


@app.get("/api/projects")
def list_projects() -> dict:
    projects_dir = ROOT / "data" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return {"projects": [str(path) for path in sorted(projects_dir.iterdir()) if path.is_dir()]}


@app.get("/api/laser/status")
def laser_status() -> dict:
    return laser_controller.status()


def _public_data_path(path: Path) -> str:
    path = path.resolve()
    try:
        return "/data/" + str(path.relative_to((ROOT / "data").resolve()))
    except ValueError:
        return str(path)


def _set_active_image(project_dir: Path, image_path: Path) -> None:
    STATE.update(
        {
            "project_dir": str(project_dir),
            "captured_image": str(image_path),
            "sheath_polygon": None,
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
    return [camera for camera in cameras if not _is_hidden_camera(camera["name"])]
