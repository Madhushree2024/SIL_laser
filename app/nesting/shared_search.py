"""Conservative rectangle filters for the zero-spacing common-cut search."""
from functools import lru_cache
import numpy as np


def rectangle_projection(polygon, axes):
    coords = np.asarray(polygon.exterior.coords)[:-1]
    if len(coords) != 4 or len(polygon.interiors):
        return None
    projected = coords @ axes
    low, high = projected.min(axis=0), projected.max(axis=0)
    expected = np.prod(high - low)
    if abs(expected - polygon.area) > max(1e-10, polygon.area * 1e-10):
        return None
    # Every vertex must be a corner in this frame. The area check alone could
    # accept a nearly rectangular custom quadrilateral.
    if not np.all(np.minimum(abs(projected - low), abs(projected - high)) <= 1e-8):
        return None
    return low, high


def rectangle_context(base, placed):
    coords = np.asarray(base.exterior.coords)
    if len(coords) != 5:
        return None
    edge = coords[1] - coords[0]
    length = np.linalg.norm(edge)
    if length <= 0:
        return None
    u = edge / length
    axes = np.column_stack((u, [-u[1], u[0]]))
    projection = rectangle_projection(base, axes)
    if projection is None:
        return None
    others = [rectangle_projection(other, axes) for other in placed]
    if any(other is None for other in others):
        return None
    return axes, projection, others


def possible_contacts(context, centers):
    axes, (low, high), others = context
    offset = centers @ axes
    lower, upper = offset + low, offset + high
    possible = np.zeros(len(centers), dtype=bool)
    tolerance = 4e-6  # Conservative relative to the existing 1e-6 cut-line grid.
    for other_low, other_high in others:
        overlap = np.minimum(upper, other_high) - np.maximum(lower, other_low)
        touch = (abs(upper - other_low) <= tolerance) | (abs(lower - other_high) <= tolerance)
        possible |= (touch[:, 0] & (overlap[:, 1] >= -tolerance)) | (touch[:, 1] & (overlap[:, 0] >= -tolerance))
    return possible


def overlap_areas(context, centers, index):
    axes, (low, high), others = context
    other_low, other_high = others[index]
    offset = centers @ axes
    overlap = np.maximum(0, np.minimum(offset + high, other_high) - np.maximum(offset + low, other_low))
    areas = overlap[:, 0] * overlap[:, 1]
    scale = max(1.0, float(np.max(abs(offset))) if len(offset) else 1, float(np.max(abs(other_high))), float(np.max(abs(other_low))))
    extent = max(1.0, float(np.max(high - low)), float(np.max(other_high - other_low)))
    # Near the area tolerance use GEOS rather than relying on floating-point
    # projection arithmetic. Away from it the answer is unambiguous.
    error = np.finfo(float).eps * 128 * scale * extent
    return areas, error



@lru_cache(maxsize=16)
def _expanded_material(usable):
    return usable.buffer(.001, join_style=2)


@lru_cache(maxsize=128)
def _corner_region(base, usable):
    import shapely
    from shapely import affinity
    expanded = _expanded_material(usable)
    corners = np.asarray(base.exterior.coords)[:-1]
    return shapely.intersection_all([affinity.translate(expanded, -x, -y) for x, y in corners])


def feasible_center_region(base, usable, context):
    import shapely
    from shapely import affinity
    from shapely.geometry import Polygon
    axes, (low, high), others = context
    if min(high - low) < .01 or any(min(upper - lower) < .01 for lower, upper in others):
        return None
    # A conservative necessary condition, never a substitute for containment.
    # Expand by 0.001 mm so overlay rounding and exact-contact lines are retained.
    region = _corner_region(base, usable)
    if region.is_empty:
        return region
    forbidden = []
    for other_low, other_high in others:
        lower, upper = other_low - high + .001, other_high - low - .001
        if np.any(upper <= lower):
            continue
        points = np.array([[lower[0],lower[1]],[upper[0],lower[1]],[upper[0],upper[1]],[lower[0],upper[1]]]) @ axes.T
        forbidden.append(Polygon(points))
    # Contracting each forbidden box permits all overlap-tolerance boundary
    # cases. Exact overlap-area validation still rejects the extra positions.
    if forbidden:
        region = region.difference(shapely.union_all(forbidden))
    shapely.prepare(region)
    return region


def edge_frame_contacts(context, vertices):
    """Exact rectangle contacts in the pieces' axes, even in an angled frame.

    A packing frame need not be parallel to the allowed template direction.
    Aligning bounding boxes in that frame leaves small gaps between real edges.
    These candidates align actual edges; ordinary geometry validation still
    checks material containment and overlap before accepting any position.
    """
    axes, (low, high), others = context
    if not others or np.min(np.abs(axes)) < 1e-12:
        return np.empty((0, 2))
    vertices = np.asarray(vertices) @ axes
    xs = set(vertices[:, 0] - low[0]) | set(vertices[:, 0] - high[0])
    ys = set(vertices[:, 1] - low[1]) | set(vertices[:, 1] - high[1])
    for lower, upper in others:
        xs.update((lower[0] - low[0], upper[0] - high[0], upper[0] - low[0], lower[0] - high[0]))
        ys.update((lower[1] - low[1], upper[1] - high[1], upper[1] - low[1], lower[1] - high[1]))
    grid_x, grid_y = np.meshgrid(sorted(xs), sorted(ys))
    centers = np.column_stack((grid_x.ravel(), grid_y.ravel())) @ axes.T
    return centers[possible_contacts(context, centers)]
