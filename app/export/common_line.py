"""Unique cut paths, preserving internal dividers and separate sheath identities."""
from collections import defaultdict
import math
import shapely
from shapely.geometry import Polygon


def common_cut_paths(placements: list[dict]) -> list[list[list[float]]]:
    groups = defaultdict(list)
    for placement in placements:
        points = placement["polygon"]
        if not all(math.isfinite(value) for point in points for value in point):
            raise ValueError("Common-cut polygon contains non-finite coordinates")
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("Common-cut polygon is invalid")
        groups[placement.get("sheath_id")].append(polygon.boundary)
    paths = []
    for boundaries in groups.values():
        # Union the lines, not the filled polygons: filled union loses the cuts
        # dividing adjacent pieces. Snap-rounding handles rotated shared edges.
        network = shapely.union_all(boundaries, grid_size=1e-6)
        merged = shapely.line_merge(network)
        lines = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
        for line in lines:
            if line.length > 1e-6:
                paths.append([[float(x), float(y)] for x, y in line.coords])
    return paths
