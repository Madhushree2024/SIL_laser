from __future__ import annotations

from dataclasses import asdict, dataclass
from math import atan2, degrees, isfinite

from shapely import affinity
from shapely.geometry import Polygon

from app.templates.template_geometry import CuttingTemplate, rotate_polygon

from .collision import collides
from .placement import Placement


@dataclass
class NestingResult:
    placements: list[Placement]
    sheath_area_mm2: float
    used_area_mm2: float
    utilization: float
    waste: float

    def to_dict(self) -> dict:
        return {
            "placements": [asdict(p) for p in self.placements],
            "sheath_area_mm2": self.sheath_area_mm2,
            "used_area_mm2": self.used_area_mm2,
            "utilization": self.utilization,
            "waste": self.waste,
        }


class NestingEngine:
    def nest(
        self,
        sheath_points: list[list[float]],
        templates: list[CuttingTemplate],
        spacing_mm: float = 2,
        edge_clearance_mm: float = 3,
        rotation_step_deg: float = 5,
        strategy: str = "maximum_pieces",
        precision_mm: float = 0.5,
    ) -> NestingResult:
        if not all(isfinite(value) and value >= 0 for value in (spacing_mm, edge_clearance_mm)):
            raise ValueError("Spacing and edge clearance must be finite and non-negative")
        if not isfinite(precision_mm) or not 0.1 <= precision_mm <= 5:
            raise ValueError("Placement precision must be between 0.1 and 5 mm")
        if not isfinite(rotation_step_deg) or not 1 <= rotation_step_deg <= 90:
            raise ValueError("Rotation step must be between 1 and 90 degrees")
        sheath = Polygon(sheath_points)
        if not sheath.is_valid or sheath.area <= 0:
            raise ValueError("Sheath polygon is invalid")
        items: list[CuttingTemplate] = []
        for template in templates:
            if template.enabled:
                items.extend([template] * max(0, int(template.quantity)))
        if strategy == "common_line":
            # True shared cuts touch without an inter-piece gap. Keep material
            # clearance, priorities and each template's allowed world rotation.
            from .precision_nesting import nest_precise
            return nest_precise(sheath, items, 0, edge_clearance_mm, precision_mm, rotation_step_deg, prefer_shared=True)
        # Shared-side row packing rotates the whole layout to the material edge.
        # The existing precise path preserves a rectangle's locked world orientation.
        fixed_rectangle = any(item.shape == "rectangle" and len(item.allowed_rotations) == 1 for item in items)
        if strategy == "accurate" or (strategy == "shared_side" and (fixed_rectangle or any(item.shape not in {"rectangle", "square"} for item in items))):
            from .precision_nesting import nest_precise
            return nest_precise(sheath, items, spacing_mm, edge_clearance_mm, precision_mm, rotation_step_deg)
        if strategy == "shared_side":
            return self._nest_shared_side(sheath, items, edge_clearance_mm, spacing_mm, precision_mm)
        if any(item.priority is not None for item in items):
            items.sort(key=lambda item: (item.priority if item.priority is not None else float("inf"), item.width_mm * item.height_mm if strategy == "maximum_pieces" else -item.width_mm * item.height_mm))
        else:
            items.sort(key=lambda item: item.width_mm * item.height_mm, reverse=strategy != "maximum_pieces")

        usable_sheath = sheath.buffer(-edge_clearance_mm) if edge_clearance_mm else sheath
        if usable_sheath.is_empty:
            return NestingResult([], float(sheath.area), 0.0, 0.0, 1.0)

        minx, miny, maxx, maxy = sheath.bounds
        placed_polygons: list[Polygon] = []
        placements: list[Placement] = []
        grid = max(2.0, min(spacing_mm * 2, 10.0))

        for template in items:
            best: tuple[float, Polygon, float, float, float] | None = None
            rotations = template.allowed_rotations or list(range(0, 360, int(max(1, rotation_step_deg))))
            for rotation in rotations:
                candidate_base = rotate_polygon(template.geometry(), rotation)
                y = miny
                while y <= maxy:
                    x = minx
                    while x <= maxx:
                        candidate = affinity.translate(candidate_base, xoff=x, yoff=y)
                        if usable_sheath.contains(candidate) and not collides(candidate, placed_polygons, spacing_mm):
                            clearance_score = sheath.boundary.distance(candidate)
                            score = clearance_score if strategy == "best_fit" else -y * 0.001 - x * 0.0001
                            if best is None or score > best[0]:
                                best = (score, candidate, x, y, rotation)
                                if strategy in {"first_fit", "maximum_pieces"}:
                                    break
                        x += grid
                    if best is not None and strategy in {"first_fit", "maximum_pieces"}:
                        break
                    y += grid
            if best is None:
                continue
            _, polygon, x, y, rotation = best
            placed_polygons.append(polygon)
            placements.append(
                Placement(
                    template_id=template.id,
                    template_name=template.name,
                    x_mm=x,
                    y_mm=y,
                    rotation_deg=rotation,
                    polygon=[[float(px), float(py)] for px, py in polygon.exterior.coords[:-1]],
                )
            )

        used_area = sum(poly.area for poly in placed_polygons)
        utilization = used_area / sheath.area if sheath.area else 0
        return NestingResult(
            placements=placements,
            sheath_area_mm2=float(sheath.area),
            used_area_mm2=float(used_area),
            utilization=float(utilization),
            waste=float(1 - utilization),
        )

    def _nest_shared_side(
        self,
        sheath: Polygon,
        items: list[CuttingTemplate],
        edge_clearance_mm: float,
        spacing_mm: float = 0,
        precision_mm: float = 0.5,
    ) -> NestingResult:
        respect_priority = any(item.priority is not None for item in items)
        sorted_items = sorted(items, key=lambda item: (item.priority if item.priority is not None else float("inf"), item.width_mm * item.height_mm)) if respect_priority else sorted(items, key=lambda item: item.width_mm * item.height_mm)
        priority_levels = sorted({item.priority if item.priority is not None else float("inf") for item in sorted_items})
        angles = _long_side_angles(sheath)
        best: tuple[tuple[float, ...], list[tuple[CuttingTemplate, Polygon]], float] | None = None

        for angle in angles:
            local_sheath = affinity.rotate(sheath, -angle, origin=(0, 0))
            usable = local_sheath.buffer(-edge_clearance_mm) if edge_clearance_mm else local_sheath
            if usable.is_empty:
                continue
            layout = _find_shared_side_grid(usable, sorted_items, respect_priority=respect_priority, spacing_mm=spacing_mm, precision_mm=precision_mm)
            if not layout:
                continue
            area = sum(poly.area for _, poly in layout)
            priority_counts = tuple(sum(1 for item, _ in layout if (item.priority if item.priority is not None else float("inf")) == level) for level in priority_levels)
            score = (*priority_counts, len(layout), area) if respect_priority else (len(layout), area)
            if best is None or score > best[0]:
                best = (score, layout, angle)

        if best is None:
            return NestingResult([], float(sheath.area), 0.0, 0.0, 1.0)

        best_score, local_row, angle = best
        used_area = best_score[-1]
        placements = []
        for template, local_poly in local_row:
            world_poly = affinity.rotate(local_poly, angle, origin=(0, 0))
            center = world_poly.centroid
            placements.append(
                Placement(
                    template_id=template.id,
                    template_name=template.name,
                    x_mm=float(center.x),
                    y_mm=float(center.y),
                    rotation_deg=float(angle),
                    polygon=[[float(px), float(py)] for px, py in world_poly.exterior.coords[:-1]],
                )
            )

        utilization = used_area / sheath.area if sheath.area else 0
        return NestingResult(
            placements=placements,
            sheath_area_mm2=float(sheath.area),
            used_area_mm2=float(used_area),
            utilization=float(utilization),
            waste=float(1 - utilization),
        )


def _long_side_angles(sheath: Polygon) -> list[float]:
    rect = sheath.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    edges = []
    for start, end in zip(coords, coords[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        edges.append((dx * dx + dy * dy, degrees(atan2(dy, dx))))
    longest = max(edges, key=lambda item: item[0])[1]
    return [longest, longest + 180, longest + 90, longest + 270, 0, 180]


def _find_shared_side_row(usable: Polygon, items: list[CuttingTemplate]) -> list[tuple[CuttingTemplate, Polygon]] | None:
    if not items:
        return None

    minx, miny, maxx, maxy = usable.bounds
    row_width = sum(item.width_mm for item in items)
    row_height = max(item.height_mm for item in items)
    if row_width <= 0 or row_height <= 0:
        return None

    step = 2.0
    y = miny
    while y + row_height <= maxy:
        x = minx
        while x + row_width <= maxx:
            cursor = x
            row: list[tuple[CuttingTemplate, Polygon]] = []
            for item in items:
                poly = Polygon(
                    [
                        (cursor, y),
                        (cursor + item.width_mm, y),
                        (cursor + item.width_mm, y + item.height_mm),
                        (cursor, y + item.height_mm),
                    ]
                )
                row.append((item, poly))
                cursor += item.width_mm
            if all(usable.contains(poly) for _, poly in row):
                return row
            x += step
        y += step
    return None


def _find_shared_side_grid(usable: Polygon, items: list[CuttingTemplate], respect_priority: bool = False, spacing_mm: float = 0, precision_mm: float = 0.5) -> list[tuple[CuttingTemplate, Polygon]]:
    if not items:
        return []

    orders = [items] if respect_priority else [
        sorted(items, key=lambda item: item.width_mm * item.height_mm),
        sorted(items, key=lambda item: item.width_mm * item.height_mm, reverse=True),
        sorted(items, key=lambda item: (item.height_mm, item.width_mm)),
        sorted(items, key=lambda item: (item.height_mm, item.width_mm), reverse=True),
    ]
    best: list[tuple[CuttingTemplate, Polygon]] = []
    for ordered_items in orders:
        layout = _pack_shared_edge_order(usable, ordered_items, spacing_mm) if precision_mm >= 0.5 else _pack_shared_edge_precise(usable, ordered_items, spacing_mm, precision_mm)
        layout_area = sum(poly.area for _, poly in layout)
        best_area = sum(poly.area for _, poly in best)
        if (len(layout), layout_area) > (len(best), best_area):
            best = layout
    return best



def _pack_shared_edge_precise(usable: Polygon, items: list[CuttingTemplate], spacing_mm: float, precision_mm: float) -> list[tuple[CuttingTemplate, Polygon]]:
    import shapely
    from .precision_nesting import _find_position
    shapely.prepare(usable)
    placed = []
    failed = set()
    for item in items:
        key = (item.id, item.width_mm, item.height_mm, len(placed))
        if key in failed:
            continue
        base = Polygon([(0, 0), (item.width_mm, 0), (item.width_mm, item.height_mm), (0, item.height_mm)])
        position = _find_position(base, usable, [poly for _, poly in placed], spacing_mm if spacing_mm > 1e-7 else 0,
                                  precision_mm, containment="contains")
        if position is not None:
            placed.append((item, position[0]))
        else:
            failed.add(key)
    return placed


def _pack_shared_edge_order(usable: Polygon, items: list[CuttingTemplate], spacing_mm: float = 0) -> list[tuple[CuttingTemplate, Polygon]]:
    minx, miny, maxx, maxy = usable.bounds
    placed: list[tuple[CuttingTemplate, Polygon]] = []
    step = 2.0

    import numpy as np
    import shapely
    from .precision_nesting import _valid_candidates
    shapely.prepare(usable)
    for item in items:
        candidates = _shared_edge_candidates(minx, miny, maxx, maxy, placed, item, step, spacing_mm)
        # The existing score orders by (y, x); its final term only breaks ties
        # at identical coordinates. Stable sorting keeps that exact choice.
        candidates.sort(key=lambda point: (point[1], point[0]))
        base = Polygon([(0, 0), (item.width_mm, 0), (item.width_mm, item.height_mm), (0, item.height_mm)])
        for start in range(0, len(candidates), 256):
            centers = np.asarray(candidates[start:start + 256], dtype=float)
            geometries, valid = _valid_candidates(base, centers, usable, [poly for _, poly in placed], spacing_mm if spacing_mm > 1e-7 else 0, containment="contains")
            indices = np.flatnonzero(valid)
            if len(indices):
                placed.append((item, geometries[indices[0]]))
                break
    return placed


def _shared_edge_candidates(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    placed: list[tuple[CuttingTemplate, Polygon]],
    item: CuttingTemplate,
    step: float,
    spacing_mm: float = 0,
) -> list[tuple[float, float]]:
    xs = {minx}
    ys = {miny}
    for _, poly in placed:
        bx0, by0, bx1, by1 = poly.bounds
        xs.update({bx0, bx1 + spacing_mm, bx0 - item.width_mm - spacing_mm, bx1 - item.width_mm})
        ys.update({by0, by1 + spacing_mm, by0 - item.height_mm - spacing_mm, by1 - item.height_mm})

    candidates: list[tuple[float, float]] = []
    for y in sorted(ys):
        for x in sorted(xs):
            if minx <= x and miny <= y and x + item.width_mm <= maxx and y + item.height_mm <= maxy:
                candidates.append((x, y))

    if not placed:
        y = miny
        while y + item.height_mm <= maxy:
            x = minx
            while x + item.width_mm <= maxx:
                candidates.append((x, y))
                x += step
            y += step

    return candidates
