from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Placement:
    template_id: str
    template_name: str
    x_mm: float
    y_mm: float
    rotation_deg: float
    polygon: list[list[float]]
    valid: bool = True
