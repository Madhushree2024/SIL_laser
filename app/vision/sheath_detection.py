from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.calibration.camera_calibration import CameraCalibrationProfile
from app.calibration.distortion import undistort
from app.calibration.scale_calibration import ScaleCalibrationProfile, pixel_points_to_mm

from .contour import draw_contour_overlay, contour_polygons
from .preprocessing import segment_sheath


@dataclass
class DetectionResult:
    pixel_polygon: list[list[float]]
    mm_polygon: list[list[float]]
    mask_path: str | None = None
    overlay_path: str | None = None
    sheaths: list[dict] = field(default_factory=list)


class ClassicalSheathDetector:
    def detect(
        self,
        image: np.ndarray,
        output_dir: Path | None = None,
        camera_profile: CameraCalibrationProfile | None = None,
        scale_profile: ScaleCalibrationProfile | None = None,
        min_area_px: float = 5000,
        workspace_mask: np.ndarray | None = None,
        reject_incomplete: bool = False,
    ) -> DetectionResult:
        corrected = undistort(image, camera_profile)
        mask = segment_sheath(corrected, workspace_mask=workspace_mask, min_area_px=min_area_px)
        if workspace_mask is not None:
            if workspace_mask.shape != mask.shape:
                raise ValueError("Working-area mask differs from image resolution")
            mask = cv2.bitwise_and(mask, workspace_mask)
        polygons = contour_polygons(mask, min_area_px=min_area_px)
        if reject_incomplete:
            height, width = mask.shape
            if workspace_mask is not None:
                interior = cv2.erode(workspace_mask, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
                boundary = cv2.subtract(workspace_mask, interior)
            else:
                boundary = np.zeros_like(mask)
            for points in polygons:
                pts = np.array(points, dtype=np.int32)
                if (pts[:, 0].min() <= 1 or pts[:, 1].min() <= 1 or
                    pts[:, 0].max() >= width - 2 or pts[:, 1].max() >= height - 2):
                    raise ValueError('A sheath reaches the camera image edge. Move the whole sheath into view and capture again; incomplete outlines cannot be used for nesting.')
                piece = np.zeros_like(mask)
                cv2.fillPoly(piece, [pts], 255)
                if cv2.countNonZero(cv2.bitwise_and(piece, boundary)) >= 3:
                    raise ValueError('A sheath touches the green-board or marker boundary and may be clipped. Move it fully onto the green board, clear of markers, and capture again.')
        sheaths = [{"id": f"sheath-{index + 1}", "pixel_polygon": polygon,
                    "mm_polygon": pixel_points_to_mm(polygon, scale_profile)}
                   for index, polygon in enumerate(polygons)]
        polygon, mm_polygon = sheaths[0]["pixel_polygon"], sheaths[0]["mm_polygon"]
        mask_path = overlay_path = None
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            mask_file = output_dir / "mask.png"
            overlay_file = output_dir / "contour_overlay.jpg"
            cv2.imwrite(str(mask_file), mask)
            overlay = corrected.copy()
            for index, piece in enumerate(sheaths):
                overlay = draw_contour_overlay(overlay, piece["pixel_polygon"])
                if len(sheaths) > 1:
                    x, y = np.mean(piece["pixel_polygon"], axis=0).astype(int)
                    cv2.putText(overlay, f"S{index + 1}", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 80), 2)
            cv2.imwrite(str(overlay_file), overlay)
            mask_path = str(mask_file)
            overlay_path = str(overlay_file)
        return DetectionResult(pixel_polygon=polygon, mm_polygon=mm_polygon, mask_path=mask_path, overlay_path=overlay_path, sheaths=sheaths)
