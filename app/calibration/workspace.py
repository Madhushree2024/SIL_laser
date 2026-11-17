"""Working-area calibration from surveyed marker centres, independent of laser alignment."""
from pathlib import Path
import json
import cv2
import numpy as np
from app.calibration.aruco_bed import BedCalibrationRequest, profile_from_bed_markers
from app.calibration.scale_calibration import ScaleCalibrationProfile


def read_workspace_layout(path: Path) -> BedCalibrationRequest:
    if not path.is_file():
        raise ValueError('Surveyed working-area layout is missing')
    return BedCalibrationRequest.model_validate(json.loads(path.read_text(encoding='utf-8')))


def workspace_profile(image: np.ndarray, request: BedCalibrationRequest) -> ScaleCalibrationProfile:
    profile = profile_from_bed_markers(image, request)
    points = np.array([[m.x_mm, m.y_mm] for m in request.markers])
    profile.source = 'aruco_workspace'
    profile.working_width_mm = float(np.ptp(points[:, 0]))
    profile.working_height_mm = float(np.ptp(points[:, 1]))
    return profile


def workspace_mask(image_size, profile: ScaleCalibrationProfile, exclude_markers=True):
    width, height = image_size
    if not profile.homography or not profile.bed_markers:
        raise ValueError('Working-area profile needs marker coordinates and a homography')
    if (width, height) != (profile.image_width, profile.image_height):
        raise ValueError('Working-area photo resolution changed; recalibrate at this camera resolution')
    centres = np.array([[m['x_mm'], m['y_mm']] for m in profile.bed_markers], np.float32)
    world_hull = cv2.convexHull(centres).reshape(-1, 2)
    inverse = np.linalg.inv(np.array(profile.homography, np.float64))
    def pixels(points):
        result = cv2.perspectiveTransform(np.array(points, np.float64).reshape(-1, 1, 2), inverse).reshape(-1, 2)
        if not np.isfinite(result).all():
            raise ValueError('Invalid working-area coordinates')
        return np.round(result).astype(np.int32)
    mask = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(mask, pixels(world_hull), 255)
    if exclude_markers:
        # Keep a clear reference zone around each centre, including white margins.
        half_side = 70.
        for x, y in centres:
            square = [[x-half_side,y-half_side], [x+half_side,y-half_side],
                      [x+half_side,y+half_side], [x-half_side,y+half_side]]
            cv2.fillConvexPoly(mask, pixels(square), 0)
    return mask


def material_workspace_mask(image: np.ndarray, profile: ScaleCalibrationProfile):
    """Allow complete material on the board beyond the surveyed marker centres.

    The homography remains the measurement calibration; its centre rectangle is
    not the physical board edge. Exclude actual marker margins in image pixels.
    """
    from app.vision.green_surface import green_surface_workspace
    height, width = image.shape[:2]
    if (width, height) != (profile.image_width, profile.image_height):
        raise ValueError('Working-area photo resolution changed; recalibrate at this camera resolution')
    if not profile.homography or not profile.bed_markers:
        raise ValueError('Working-area profile needs marker coordinates and a homography')
    surface = green_surface_workspace(image)
    if surface is None:
        raise ValueError('Green board not found. Show the full green board and improve lighting before detecting sheaths.')
    mask = surface[0].copy()
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    corners, _, _ = detector.detectMarkers(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
    for marker in corners:
        points = marker.reshape(4, 2)
        centre = points.mean(axis=0)
        border = np.rint(centre + (points - centre) * 1.5).astype(np.int32)
        cv2.fillConvexPoly(mask, border, 0)
    return mask
