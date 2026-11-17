from __future__ import annotations

from math import ceil, sqrt

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon

from app.templates.template_geometry import rotate_polygon
from .placement import Placement

TOLERANCE = 1e-7


def _valid_candidates(base, centers, usable, placed, spacing, *, containment="covers", fast_rectangles=False, rectangle_cache=None, search_region=None):
    coords = np.asarray(base.exterior.coords)
    if fast_rectangles and spacing <= 0:
        from .shared_search import rectangle_context, overlap_areas
        context = rectangle_cache if rectangle_cache is not None else rectangle_context(base, placed)
        if context is not None and context is not False:
            valid = np.ones(len(centers), dtype=bool)
            # Polygon containment requires every corner to be in the material.
            # Prepared point predicates reject misses before constructing tens
            # of thousands of polygons. Exact polygon containment still follows.
            if search_region is not None:
                valid &= shapely.intersects_xy(search_region, centers[:, 0], centers[:, 1])
            else:
                for x, y in coords[:-1]:
                    indices = np.flatnonzero(valid)
                    if not len(indices):
                        break
                    valid[indices] &= shapely.intersects_xy(usable, centers[indices, 0] + x, centers[indices, 1] + y)
            for index, other in enumerate(placed):
                indices = np.flatnonzero(valid)
                if not len(indices):
                    break
                areas, error = overlap_areas(context, centers[indices], index)
                uncertain = abs(areas - TOLERANCE) <= error
                valid[indices[~uncertain]] &= areas[~uncertain] <= TOLERANCE
                if uncertain.any():
                    uncertain_indices = indices[uncertain]
                    candidates = shapely.polygons(coords[None, :, :] + centers[uncertain_indices, None, :])
                    valid[uncertain_indices] &= shapely.area(shapely.intersection(candidates, other)) <= TOLERANCE
            # Callers use only valid positions. Invalid slots deliberately stay
            # empty on this private fast path to avoid needless allocations.
            geometries = np.empty(len(centers), dtype=object)
            geometries.fill(None)
            indices = np.flatnonzero(valid)
            if len(indices):
                geometries[indices] = shapely.polygons(coords[None, :, :] + centers[indices, None, :])
                valid[indices] &= (shapely.contains if containment == "contains" else shapely.covers)(usable, geometries[indices])
            return geometries, valid
    geometries = shapely.polygons(coords[None, :, :] + centers[:, None, :])
    valid = (shapely.contains if containment == "contains" else shapely.covers)(usable, geometries)
    if len(coords) <= 16 and len(placed) <= 2:
        # For a few simple outlines, the original distance check is cheaper than
        # setting up a broad phase. Keep that fast path for small camera pieces.
        for other in placed:
            indices = np.flatnonzero(valid)
            if not len(indices):
                break
            if spacing > 0:
                valid[indices] &= shapely.distance(geometries[indices], other) >= spacing - TOLERANCE
            else:
                valid[indices] &= shapely.area(shapely.intersection(geometries[indices], other)) <= TOLERANCE
        return geometries, valid
    bounds = shapely.bounds(geometries) if placed else None
    for other in placed:
        ox0, oy0, ox1, oy1 = other.bounds
        # Disjoint expanded bounds cannot violate spacing or overlap constraints.
        nearby = ((bounds[:, 2] >= ox0 - spacing) & (bounds[:, 0] <= ox1 + spacing)
                  & (bounds[:, 3] >= oy0 - spacing) & (bounds[:, 1] <= oy1 + spacing))
        indices = np.flatnonzero(valid & nearby)
        if not len(indices):
            if not valid.any():
                break
            continue
        if spacing > 0:
            if spacing > TOLERANCE and len(coords) > 16:
                # Intersecting polygons have zero distance. Avoid the expensive
                # distance calculation for overlapping detailed circle outlines.
                shapely.prepare(other)
                overlaps = shapely.intersects(other, geometries[indices])
                valid[indices[overlaps]] = False
                indices = indices[~overlaps]
            if len(indices):
                valid[indices] &= shapely.distance(geometries[indices], other) >= spacing - TOLERANCE
        else:
            # Shared edges are allowed; interiors must never overlap.
            valid[indices] &= shapely.area(shapely.intersection(geometries[indices], other)) <= TOLERANCE
    return geometries, valid


def _shared_boundary_length(polygon, placed):
    if not placed:
        return 0.0
    boundary = shapely.union_all([other.boundary for other in placed], grid_size=1e-6)
    return float(shapely.intersection(polygon.boundary, boundary, grid_size=1e-6).length)


def _find_position(base, usable, placed, spacing, precision, *, containment="covers", prefer_shared=False, fast_rectangles=False, search_region=None, edge_contacts=True):
    fast_rectangles = fast_rectangles or prefer_shared
    candidate_options = {}
    if fast_rectangles:
        from .shared_search import rectangle_context
        context = rectangle_context(base, placed)
        candidate_options = {"fast_rectangles": True, "rectangle_cache": context if context is not None else False}
        if context is not None:
            from .shared_search import feasible_center_region
            if search_region is None:
                search_region = feasible_center_region(base, usable, context)
            if search_region is not None:
                if search_region.is_empty:
                    return None
                candidate_options["search_region"] = search_region
    bx0, by0, bx1, by1 = base.bounds
    ux0, uy0, ux1, uy1 = usable.bounds
    x0, x1 = ux0 - bx0, ux1 - bx1
    y0, y1 = uy0 - by0, uy1 - by1
    if x1 < x0 - TOLERANCE or y1 < y0 - TOLERANCE or base.area > usable.area + TOLERANCE:
        return None
    xs, ys = {x0, x1}, {y0, y1}
    # Exact boundary and neighbouring-piece contacts avoid grid rounding waste.
    vertices = shapely.get_coordinates(usable)
    if len(vertices) > 64:
        vertices = vertices[np.linspace(0, len(vertices) - 1, 64, dtype=int)]
    for x, y in vertices:
        xs.update((x - bx0, x - bx1))
        ys.update((y - by0, y - by1))
    for other in placed:
        ox0, oy0, ox1, oy1 = other.bounds
        xs.update((ox0 - bx0, ox1 - bx1, ox1 + spacing - bx0, ox0 - spacing - bx1))
        ys.update((oy0 - by0, oy1 - by1, oy1 + spacing - by0, oy0 - spacing - by1))
    # Shared-edge positions must retain the neighbour's exact coordinate.
    # Rounding a centre by even 1e-8 mm can create a thin overlap whose area
    # exceeds the collision tolerance, incorrectly rejecting a valid contact.
    xs = sorted({x if prefer_shared else round(x, 7) for x in xs if x0 - TOLERANCE <= x <= x1 + TOLERANCE})
    ys = sorted({y if prefer_shared else round(y, 7) for y in ys if y0 - TOLERANCE <= y <= y1 + TOLERANCE})
    best = None
    best_contact = -1.0
    occupied_boundary = shapely.union_all([other.boundary for other in placed], grid_size=1e-6) if prefer_shared and placed else None
    if occupied_boundary is not None:
        from .shared_search import rectangle_context, possible_contacts, edge_frame_contacts
        context = rectangle_context(base, placed)
        if context is not None:
            # Only edge-contact candidates can improve the common-cut score.
            # Preserve row-major tie order and exact GEOS scoring after filtering.
            grid_x, grid_y = np.meshgrid(xs, ys)
            centers = np.column_stack((grid_x.ravel(), grid_y.ravel()))
            centers = centers[possible_contacts(context, centers)]
            edge_centers = edge_frame_contacts(context, vertices) if edge_contacts else np.empty((0, 2))
            if len(edge_centers):
                centers = np.vstack((centers, edge_centers))
                centers = centers[np.lexsort((centers[:, 0], centers[:, 1]))]
            for start in range(0, len(centers), 512):
                batch = centers[start:start + 512]
                geometries, valid = _valid_candidates(base, batch, usable, placed, spacing, containment=containment, **candidate_options)
                indices = np.flatnonzero(valid)
                if len(indices):
                    contacts = shapely.length(shapely.intersection(shapely.boundary(geometries[indices]), occupied_boundary, grid_size=1e-6))
                    offset = int(np.argmax(contacts)); i = indices[offset]; contact = float(contacts[offset])
                    if best is None or (-contact, batch[i, 1], batch[i, 0]) < (-best_contact, best[2], best[1]):
                        best = (geometries[i], float(batch[i, 0]), float(batch[i, 1])); best_contact = contact
            if best is not None and best_contact > TOLERANCE:
                return best
            return _find_position(base, usable, placed, spacing, precision, containment=containment, fast_rectangles=True, search_region=search_region)
    for y in ys:
        centers = np.array([(x, y) for x in xs])
        if not len(centers):
            continue
        geometries, valid = _valid_candidates(base, centers, usable, placed, spacing, containment=containment, **candidate_options)
        indices = np.flatnonzero(valid)
        if len(indices):
            if occupied_boundary is not None:
                contacts = shapely.length(shapely.intersection(shapely.boundary(geometries[indices]), occupied_boundary, grid_size=1e-6))
                offset = int(np.argmax(contacts))
                i, contact = indices[offset], float(contacts[offset])
                if best is None or (-contact, y, centers[i, 0]) < (-best_contact, best[2], best[1]):
                    best = (geometries[i], float(centers[i, 0]), float(y))
                    best_contact = contact
            else:
                i = indices[0]
                best = (geometries[i], float(centers[i, 0]), float(y))
                break
    if occupied_boundary is not None and best_contact > TOLERANCE:
        return best  # Moving this contact to a grid point would reopen the cut.
    coarse_step = max(2.0, precision * 4)
    scanned_steps = set()
    for step in (coarse_step, precision):
        # Bound the fallback search; exact contacts and local refinement remain precise.
        step = max(step, sqrt(max(0, (x1 - x0) * (y1 - y0)) / 20000))
        if round(step, 8) in scanned_steps:
            continue
        scanned_steps.add(round(step, 8))
        scan_xs = np.linspace(x0, max(x0, x1), max(1, ceil(max(0, x1 - x0) / step) + 1))
        scan_ys = np.linspace(y0, max(y0, y1), max(1, ceil(max(0, y1 - y0) / step) + 1))
        if best is not None:
            scan_ys = scan_ys[scan_ys <= best[2] + TOLERANCE]
        # Keep exactly the same row-major search order, but batch across rows.
        # Failed fits otherwise make thousands of tiny geometry-library calls.
        columns = len(scan_xs)
        for start in range(0, columns * len(scan_ys), 512):
            indices = np.arange(start, min(start + 512, columns * len(scan_ys)))
            centers = np.column_stack((scan_xs[indices % columns], scan_ys[indices // columns]))
            geometries, valid = _valid_candidates(base, centers, usable, placed, spacing, containment=containment, **candidate_options)
            indices = np.flatnonzero(valid)
            if len(indices):
                i = indices[0]
                candidate = (geometries[i], float(centers[i, 0]), float(centers[i, 1]))
                if best is None or (candidate[2], candidate[1]) < (best[2], best[1]):
                    best = candidate
                break
        if best is not None:
            break
    if best is None:
        return None
    # Tighten the selected position without changing template size or orientation.
    step = coarse_step
    while step >= precision - TOLERANCE:
        for _ in range(12):
            _, x, y = best
            centers = np.array([(x - step, y - step), (x, y - step), (x - step, y)])
            geometries, valid = _valid_candidates(base, centers, usable, placed, spacing, containment=containment, **candidate_options)
            indices = np.flatnonzero(valid)
            if not len(indices):
                break
            i = indices[0]
            best = (geometries[i], float(centers[i, 0]), float(centers[i, 1]))
        if step <= precision + TOLERANCE:
            break
        step = max(precision, step / 2)
    return best


def nest_precise(sheath, items, spacing, clearance, precision, rotation_step, *, prefer_shared=False):
    from .nesting_engine import NestingResult, _long_side_angles
    ranks = sorted({item.priority if item.priority is not None else float("inf") for item in items})
    ordered = sorted(items, key=lambda item: (item.priority if item.priority is not None else float("inf"), -item.geometry().area))
    usable = sheath.buffer(-clearance) if clearance else sheath
    if usable.is_empty:
        return NestingResult([], float(sheath.area), 0, 0, 1)
    frames = list(dict.fromkeys(round(angle % 360, 6) for angle in [0, 90, 180, 270, *_long_side_angles(sheath)]))
    best_layout, best_score = [], None
    # Keep the existing search as a competing layout: a greedily longer contact
    # for one piece can block later requested pieces. Compare complete layouts
    # by the same priority/usage score before using shared length as the tie-break.
    searches = [(frame, edges) for frame in frames for edges in ([False, True] if prefer_shared else [False])]
    for frame_angle, use_edge_contacts in searches:
        local_usable = affinity.rotate(usable, -frame_angle, origin=(0, 0)).buffer(TOLERANCE, quad_segs=1)
        shapely.prepare(local_usable)
        local_polys, layout = [], []
        failed = set()
        occupied_area = 0.0
        for template in ordered:
            key = (template.id, len(local_polys))
            if key in failed or template.geometry().area > usable.area - occupied_area + TOLERANCE:
                continue
            rotations = [0] if template.shape == "circle" else template.allowed_rotations or list(np.arange(0, 360, rotation_step))
            best = None
            for rotation in rotations:
                # Shapely canonicalizes quarter-turn sine/cosine values, so a
                # locked 90-degree rectangle keeps truly parallel shared edges.
                base = affinity.rotate(template.geometry(), float(rotation) - frame_angle, origin=(0, 0)) if prefer_shared else rotate_polygon(template.geometry(), float(rotation) - frame_angle)
                position = _find_position(base, local_usable, local_polys, spacing, precision, **({"prefer_shared": True, "edge_contacts": use_edge_contacts} if prefer_shared else {}))
                if position is None:
                    continue
                polygon, x, y = position
                score = (-_shared_boundary_length(polygon, local_polys), polygon.bounds[1], polygon.bounds[0]) if prefer_shared else (polygon.bounds[1], polygon.bounds[0])
                if best is None or score < best[0]:
                    best = (score, polygon, float(rotation))
            if best is None:
                failed.add(key)
                continue
            _, local_poly, rotation = best
            local_polys.append(local_poly)
            occupied_area += local_poly.area
            world_poly = affinity.rotate(local_poly, frame_angle, origin=(0, 0))
            layout.append((template, world_poly, rotation))
        if prefer_shared and len(local_polys) > 1:
            # Revisit isolated pieces after all neighbours exist. Moving only
            # pieces with no shared edge cannot break an existing shared cut,
            # and preserves the selected quantities, priorities and rotations.
            for index, polygon in enumerate(local_polys):
                others = local_polys[:index] + local_polys[index + 1:]
                if _shared_boundary_length(polygon, others) > 1e-6:
                    continue
                center = polygon.centroid
                base = affinity.translate(polygon, -center.x, -center.y)
                position = _find_position(base, local_usable, others, spacing, precision, prefer_shared=True)
                if position is not None and _shared_boundary_length(position[0], others) > 1e-6:
                    local_polys[index] = position[0]
                    template, _, rotation = layout[index]
                    layout[index] = (template, affinity.rotate(position[0], frame_angle, origin=(0, 0)), rotation)
        counts = tuple(sum(template.priority == rank if rank != float("inf") else template.priority is None for template, _, _ in layout) for rank in ranks)
        used_area = sum(poly.area for _, poly, _ in layout)
        score = (*counts, used_area, len(layout)) if any(item.priority is not None for item in items) else (used_area, len(layout))
        if prefer_shared:
            # Keep quantity/priority and material usage ahead of cut-length ties.
            contact = sum(_shared_boundary_length(poly, [previous for _, previous, _ in layout[:i]]) for i, (_, poly, _) in enumerate(layout))
            score = (*counts, round(used_area, 6), len(layout), round(contact, 6)) if any(item.priority is not None for item in items) else (round(used_area, 6), len(layout), round(contact, 6))
        if best_score is None or score > best_score:
            best_score, best_layout = score, layout
    placed = []
    placements = []
    for template, polygon, rotation in best_layout:
        if not usable.buffer(TOLERANCE * 2).covers(polygon):
            raise RuntimeError("Nesting validation failed: placement outside usable sheath")
        for other in placed:
            if polygon.intersection(other).area > TOLERANCE or polygon.distance(other) < spacing - TOLERANCE * 2:
                raise RuntimeError("Nesting validation failed: overlap or insufficient spacing")
        placed.append(polygon)
        center = polygon.centroid
        placements.append(Placement(template.id, template.name, float(center.x), float(center.y), rotation, [[float(x), float(y)] for x, y in polygon.exterior.coords[:-1]]))
    used_area = sum(poly.area for poly in placed)
    utilization = used_area / sheath.area
    return NestingResult(placements, float(sheath.area), float(used_area), float(utilization), float(1 - utilization))
