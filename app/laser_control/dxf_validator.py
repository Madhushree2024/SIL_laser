"""Read-only DXF inspection. No geometry repair, translation or job encoding."""
from itertools import islice
from math import isfinite
from pathlib import Path
import ezdxf
from ezdxf.path import make_path
from shapely.geometry import Polygon

MAX_FILE_BYTES = 20 * 1024 * 1024
VALIDATION_POLICY = 'geometry-only-v2'
SUPPORTED = {'LINE', 'ARC', 'CIRCLE', 'LWPOLYLINE', 'POLYLINE', 'SPLINE'}


def validate_dxf(path: Path, config: dict) -> dict:
    result = {'policy': VALIDATION_POLICY, 'valid': False, 'errors': [], 'warnings': [], 'units': None, 'layers': [],
              'entity_count': 0, 'dimensions_mm': None, 'bounds_mm': None, 'preview': []}
    errors = result['errors']
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('DXF exceeds the 20 MB size limit.')
        doc = ezdxf.readfile(path)
        result['units'] = int(doc.units)
        if doc.units != ezdxf.units.MM:
            errors.append('DXF units must be explicitly millimetres. Re-export with millimetre units; no scale is assumed.')
        audit = doc.audit()
        if audit.errors or audit.fixes:
            errors.append('DXF requires structural repairs. Repair it in the source CAD application and re-export.')
        layers = {}
        all_points = []
        count = 0
        points_budget = 0
        for entity in doc.modelspace():
            count += 1
            if count > 20000:
                raise ValueError('DXF exceeds the 20,000 entity limit.')
            name = str(entity.dxf.layer)
            layer = layers.setdefault(name, {'name': name, 'entities': 0, 'open_paths': 0, 'default_enabled': name.upper() != 'SHEATH'})
            layer['entities'] += 1
            kind = entity.dxftype()
            if kind not in SUPPORTED:
                errors.append(f'Unsupported entity {kind} on layer {name}. Convert it to planar cutting curves before import.')
                continue
            curve = make_path(entity)
            vertices = list(islice(curve.flattening(distance=0.1), 5001))
            if len(vertices) > 5000:
                raise ValueError('A curve exceeds the preview complexity limit.')
            points_budget += len(vertices)
            if points_budget > 100000:
                raise ValueError('DXF exceeds the 100,000 preview point limit.')
            if len(vertices) < 2:
                errors.append(f'Degenerate cutting curve on layer {name}.')
                continue
            if any(not all(isfinite(float(v)) for v in point) or abs(point.z) > 1e-6 for point in vertices):
                errors.append(f'Non-planar or non-finite geometry on layer {name}.')
                continue
            points = [[float(p.x), float(p.y)] for p in vertices]
            if all(abs(p[0]-points[0][0]) + abs(p[1]-points[0][1]) < 1e-9 for p in points):
                errors.append(f'Zero-length curve on layer {name}.')
                continue
            closed = bool(curve.is_closed)
            if closed:
                polygon = Polygon(points)
                if not polygon.is_valid or polygon.area <= 1e-9:
                    errors.append(f'Self-intersecting or zero-area closed contour on layer {name}.')
            if not closed:
                layer['open_paths'] += 1
            result['preview'].append({'layer': name, 'closed': closed, 'points': points})
            all_points.extend(points)
        result['entity_count'] = count
        result['layers'] = list(layers.values())
        if not all_points:
            errors.append('No supported cutting geometry was found.')
        else:
            x0, x1 = min(p[0] for p in all_points), max(p[0] for p in all_points)
            y0, y1 = min(p[1] for p in all_points), max(p[1] for p in all_points)
            result['bounds_mm'] = {'min_x': x0, 'min_y': y0, 'max_x': x1, 'max_y': y1}
            result['dimensions_mm'] = {'width': x1-x0, 'height': y1-y0}
            workspace = config.get('workspace', {})
            width, height = float(workspace.get('width_mm', 915)), float(workspace.get('height_mm', 630))
            if not all(isfinite(v) and v > 0 for v in (width, height)):
                errors.append('The configured bed dimensions are invalid.')
            result['configured_bed_mm'] = {'width': width, 'height': height}
            result['bounds_enforced'] = False
        if any(layer['open_paths'] for layer in layers.values()):
            result['warnings'].append('Open paths are present. These can be intentional shared cuts; inspect the preview.')
        if 'SHEATH' in layers:
            result['warnings'].append('SHEATH is a reference outline and is disabled by default. Layer settings here are not applied to the LaserCAD fallback automatically.')
        result['errors'] = list(dict.fromkeys(errors))[:100]
        result['valid'] = not result['errors']
    except Exception as exc:
        errors.append(f'Unable to validate DXF: {exc}')
        result['valid'] = False
    return result
