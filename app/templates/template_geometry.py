from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, pi, sin

from shapely.geometry import Point, Polygon, box


@dataclass
class CuttingTemplate:
    id: str
    name: str
    width_mm: float
    height_mm: float
    shape: str = "rectangle"
    quantity: int = 1
    min_spacing_mm: float = 2
    allowed_rotations: list[float] = field(default_factory=lambda: [0])
    enabled: bool = True
    polygon: list[list[float]] | None = None
    priority: int | None = None

    def geometry(self) -> Polygon:
        if self.shape in {"square", "rectangle"}:
            return box(-self.width_mm / 2, -self.height_mm / 2, self.width_mm / 2, self.height_mm / 2)
        if self.shape == "circle":
            return Point(0, 0).buffer(self.width_mm / 2, quad_segs=64)
        if self.shape == "polygon" and self.polygon:
            return Polygon(self.polygon)
        raise ValueError(f"Unsupported template shape: {self.shape}")


def rotate_polygon(poly: Polygon, angle_deg: float) -> Polygon:
    angle = angle_deg * pi / 180
    rotated = []
    for x, y in poly.exterior.coords:
        rotated.append((x * cos(angle) - y * sin(angle), x * sin(angle) + y * cos(angle)))
    return Polygon(rotated)
