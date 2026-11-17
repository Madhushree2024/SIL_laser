from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np


@dataclass
class ScaleCalibrationProfile:
    mm_per_pixel: float
    working_width_mm: float
    working_height_mm: float
    homography: list[list[float]] | None
    image_width: int
    image_height: int
    source: str = "manual"
    marker_id: int | None = None
    marker_size_mm: float | None = None
    bed_markers: list[dict] | None = None
    dictionary: str | None = None
    coordinate_frame: str | None = None
    origin_marker_id: int | None = None


def profile_from_reference_square(image_points: list[list[float]], square_size_mm: float, image_size: tuple[int, int]) -> ScaleCalibrationProfile:
    if len(image_points) != 4:
        raise ValueError("Four corner points are required")
    src = np.array(image_points, dtype=np.float32)
    dst = np.array([[0, 0], [square_size_mm, 0], [square_size_mm, square_size_mm], [0, square_size_mm]], dtype=np.float32)
    homography, _ = cv2.findHomography(src, dst)
    side_a = np.linalg.norm(src[0] - src[1])
    side_b = np.linalg.norm(src[1] - src[2])
    mm_per_pixel = square_size_mm / float((side_a + side_b) / 2.0)
    return ScaleCalibrationProfile(
        mm_per_pixel=mm_per_pixel,
        working_width_mm=image_size[0] * mm_per_pixel,
        working_height_mm=image_size[1] * mm_per_pixel,
        homography=homography.tolist() if homography is not None else None,
        image_width=image_size[0],
        image_height=image_size[1],
        source="manual",
    )


def pixel_points_to_mm(points: list[list[float]], profile: ScaleCalibrationProfile | None) -> list[list[float]]:
    if profile is None:
        return points
    pts = np.array(points, dtype=np.float64).reshape(-1, 1, 2)
    if profile.homography:
        transformed = cv2.perspectiveTransform(pts, np.array(profile.homography, dtype=np.float64)).reshape(-1, 2)
        return transformed.tolist()
    return (pts.reshape(-1, 2) * profile.mm_per_pixel).tolist()


def save_profile(profile: ScaleCalibrationProfile, path: Path) -> None:
    path=Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(asdict(profile), indent=2),encoding='utf-8')
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()


def load_profile(path: Path) -> ScaleCalibrationProfile | None:
    if not path.exists():
        return None
    return ScaleCalibrationProfile(**json.loads(path.read_text()))
