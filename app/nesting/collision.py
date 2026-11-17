from __future__ import annotations

from shapely.geometry import Polygon


def is_inside(container: Polygon, candidate: Polygon, edge_clearance: float = 0) -> bool:
    usable = container.buffer(-edge_clearance) if edge_clearance else container
    return usable.contains(candidate)


def collides(candidate: Polygon, placed: list[Polygon], spacing: float = 0) -> bool:
    if not placed:
        return False
    if spacing <= 0:
        return any(candidate.intersects(other) for other in placed)
    return any(candidate.distance(other) < spacing for other in placed)
