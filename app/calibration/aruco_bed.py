from __future__ import annotations

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .aruco_scale import ARUCO_DICTIONARIES, aruco_dictionary, robust_detector_parameters
from .scale_calibration import ScaleCalibrationProfile


class BedMarker(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    marker_id: int = Field(ge=0)
    x_mm: float
    y_mm: float


class BedCalibrationRequest(BaseModel):
    markers: list[BedMarker] = Field(min_length=4, max_length=4)
    dictionary: str = "DICT_4X4_50"

    @model_validator(mode="after")
    def validate_markers(self):
        if self.dictionary not in ARUCO_DICTIONARIES:
            raise ValueError("Unsupported ArUco dictionary")
        ids = [m.marker_id for m in self.markers]
        if len(set(ids)) != 4:
            raise ValueError("Use four different marker IDs")
        if max(ids) >= len(aruco_dictionary(self.dictionary).bytesList):
            raise ValueError("Marker ID is outside the selected dictionary")
        points = np.array([[m.x_mm, m.y_mm] for m in self.markers], np.float32)
        hull = cv2.convexHull(points)
        if len(hull) != 4 or cv2.contourArea(hull) < 1:
            raise ValueError("Marker centers must form four corners of a nondegenerate work area")
        return self


def profile_from_bed_markers(image: np.ndarray, request: BedCalibrationRequest) -> ScaleCalibrationProfile:
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(request.dictionary), robust_detector_parameters())
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, _ = detector.detectMarkers(gray)
    found = ids.flatten().tolist() if ids is not None else []
    source = []
    for marker in request.markers:
        if found.count(marker.marker_id) != 1:
            raise ValueError(f"Show exactly one full marker with ID {marker.marker_id} in the calibration photo")
        quad = corners[found.index(marker.marker_id)].reshape(4, 2).astype(np.float64)
        # The diagonal intersection is the projective image of the physical center.
        homogeneous = np.column_stack([quad, np.ones(4)])
        center = np.cross(np.cross(homogeneous[0], homogeneous[2]), np.cross(homogeneous[1], homogeneous[3]))
        if abs(center[2]) < 1e-9:
            raise ValueError("Unable to resolve marker center")
        source.append((center[:2] / center[2]).tolist())
    src = np.array(source, np.float32)
    dst = np.array([[m.x_mm, m.y_mm] for m in request.markers], np.float32)
    order = cv2.convexHull(src, returnPoints=False).flatten()
    if len(order) != 4 or cv2.contourArea(src[order]) < 1 or not cv2.isContourConvex(dst[order]):
        raise ValueError("Marker positions do not correspond to four work-area corners; check IDs and laser coordinates")
    homography, _ = cv2.findHomography(src, dst, method=0)
    if homography is None or not np.isfinite(homography).all() or abs(np.linalg.det(homography)) < 1e-12:
        raise ValueError("Unable to calculate a valid bed alignment")
    height, width = image.shape[:2]
    image_corners = np.array([[0, 0], [width, 0], [width, height], [0, height]], np.float64)
    denominators = np.column_stack([image_corners, np.ones(4)]) @ homography[2]
    if np.min(denominators) <= 0 <= np.max(denominators) or np.min(np.abs(denominators)) < 1e-9:
        raise ValueError("Bed alignment crosses a perspective horizon; check marker coordinates")
    bounds = cv2.perspectiveTransform(image_corners.reshape(-1, 1, 2), homography).reshape(-1, 2)
    ratios = [np.linalg.norm(dst[i] - dst[j]) / np.linalg.norm(src[i] - src[j]) for i in range(4) for j in range(i)]
    return ScaleCalibrationProfile(
        mm_per_pixel=float(np.median(ratios)),
        working_width_mm=float(np.ptp(bounds[:, 0])), working_height_mm=float(np.ptp(bounds[:, 1])),
        homography=homography.tolist(), image_width=width, image_height=height,
        source="aruco_bed", bed_markers=[m.model_dump() for m in request.markers], dictionary=request.dictionary,
    )
