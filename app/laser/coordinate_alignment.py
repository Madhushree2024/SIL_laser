from __future__ import annotations

from math import cos, radians, sin
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CoordinateAlignment(BaseModel):
    """Map one design reference to laser coordinates without changing scale."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    enabled: bool = False
    reference: Literal["design_origin", "min_x_min_y", "min_x_max_y", "max_x_min_y", "max_x_max_y", "center", "custom", "rightmost_template_top_right", "templates_top_right"] = "design_origin"
    design_x_mm: float = 0
    design_y_mm: float = 0
    laser_x_mm: float = 0
    laser_y_mm: float = 0
    rotation_deg: float = 0
    flip_x: bool = False
    flip_y: bool = False


def align_layout(sheath: list[list[float]], placements: list[dict], alignment: CoordinateAlignment, *, sheaths: list[list[list[float]]] | None = None) -> dict:
    boundaries = sheaths if sheaths is not None else [sheath]
    points = [point for polygon in boundaries for point in polygon] + [point for placement in placements for point in placement["polygon"]]
    if not points:
        raise ValueError("No geometry available for alignment")
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    references = {
        "design_origin": [0, 0],
        "min_x_min_y": [min_x, min_y],
        "min_x_max_y": [min_x, max_y],
        "max_x_min_y": [max_x, min_y],
        "max_x_max_y": [max_x, max_y],
        "center": [(min_x + max_x) / 2, (min_y + max_y) / 2],
        "custom": [alignment.design_x_mm, alignment.design_y_mm],
    }
    if alignment.enabled and alignment.reference == "rightmost_template_top_right":
        cut_points = [point for placement in placements for point in placement["polygon"]]
        if not cut_points:
            raise ValueError("Place at least one template before using the automatic top-right DXF origin")
        # Working coordinates use +Y down. Select a real cut vertex on the
        # rightmost edge, choosing its top endpoint; ignore the material outline
        # and higher templates to its left. Tolerate roundoff on vertical edges.
        right_x = max(point[0] for point in cut_points)
        corner = min((point for point in cut_points if abs(point[0] - right_x) <= 1e-7),
                     key=lambda point: (point[1], -point[0]))
        references["rightmost_template_top_right"] = list(corner)
    if alignment.enabled and alignment.reference == "templates_top_right":
        # This frame uses +Y up. Only nested cut vertices define the box;
        # marker centres, sheath margins and historical job extents do not.
        cut_points = [point for placement in placements for point in placement["polygon"]]
        if not cut_points:
            raise ValueError("Place at least one template before using the template bounding-box origin")
        references["templates_top_right"] = [max(p[0] for p in cut_points), max(p[1] for p in cut_points)]
    reference = references[alignment.reference] if alignment.enabled else [0, 0]
    target = [alignment.laser_x_mm, alignment.laser_y_mm] if alignment.enabled else [0, 0]
    angle = radians(alignment.rotation_deg) if alignment.enabled else 0
    sx = -1 if alignment.enabled and alignment.flip_x else 1
    sy = -1 if alignment.enabled and alignment.flip_y else 1
    c, s = cos(angle), sin(angle)
    matrix = [
        [c * sx, -s * sy, target[0] - c * sx * reference[0] + s * sy * reference[1]],
        [s * sx, c * sy, target[1] - s * sx * reference[0] - c * sy * reference[1]],
        [0, 0, 1],
    ]

    def transform(point):
        x, y = point
        return [matrix[0][0] * x + matrix[0][1] * y + matrix[0][2], matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]]

    transformed_sheath = [transform(p) for p in sheath]
    transformed_placements = [{**p, "polygon": [transform(point) for point in p["polygon"]]} for p in placements]
    transformed_points = [transform(p) for p in points]
    return {
        "sheath": transformed_sheath,
        "sheaths": [[transform(point) for point in polygon] for polygon in boundaries],
        "placements": transformed_placements,
        "alignment": alignment.model_dump(),
        "design_reference_mm": reference,
        "laser_reference_mm": target,
        "matrix": matrix,
        "bounds_mm": {
            "min_x": min(p[0] for p in transformed_points), "max_x": max(p[0] for p in transformed_points),
            "min_y": min(p[1] for p in transformed_points), "max_y": max(p[1] for p in transformed_points),
        },
    }
