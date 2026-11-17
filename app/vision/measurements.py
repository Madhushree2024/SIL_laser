from __future__ import annotations

from math import hypot
from typing import Any
from shapely.geometry import Polygon


def polygon_measurements(points: list[list[float]]) -> dict[str, Any]:
    polygon = Polygon(points)
    minx, miny, maxx, maxy = polygon.bounds
    measurements = {
        "width_mm": maxx - minx,
        "height_mm": maxy - miny,
        "area_mm2": polygon.area,
        "area_m2": polygon.area / 1_000_000,
    }
    # Additional display measurements; keep legacy spans and nesting geometry unchanged.
    rectangle = polygon.minimum_rotated_rectangle
    if rectangle.geom_type == "Polygon" and polygon.area > 0:
        corners = list(rectangle.exterior.coords)[:4]
        edges = [(corners[i], corners[i + 1]) for i in range(2)]
        lengths = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges]
        if all(length > 0 for length in lengths):
            width_index = max(range(2), key=lambda i: abs(edges[i][1][0] - edges[i][0][0]) / lengths[i])
            height_index = 1 - width_index
            measurements.update(
                aligned_width_mm=lengths[width_index], aligned_height_mm=lengths[height_index],
                width_edge_mm=[list(point) for point in edges[width_index]],
                height_edge_mm=[list(point) for point in edges[height_index]],
            )
    return measurements
