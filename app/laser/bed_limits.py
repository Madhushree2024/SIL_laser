"""Validate cut geometry and describe its coordinates without imposing travel bounds."""
from math import isfinite
from shapely.geometry import Polygon


def bed_dimensions(config: dict) -> tuple[float, float]:
    workspace = config.get('workspace', {})
    try:
        raw = (workspace.get('width_mm', 915), workspace.get('height_mm', 630))
        if any(isinstance(value, bool) for value in raw):
            raise ValueError
        width, height = map(float, raw)
        if not all(isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('Laser bed width and height must be positive finite millimetre values.') from None
    return width, height


def validate_bed_settings(config: dict) -> None:
    for key in ('workspace', 'automation'):
        if not isinstance(config.get(key, {}), dict):
            raise ValueError(f'{key} settings must be an object.')
    bed_dimensions(config)
    for section, name in (('workspace', 'enforce_bed_bounds'), ('automation', 'refresh_workspace_calibration')):
        if name in config.get(section, {}) and not isinstance(config[section][name], bool):
            raise ValueError(f'{section}.{name} must be true or false.')


def validate_cut_bounds(placements: list[dict], config: dict) -> dict | None:
    """Keep geometry checks, but never reject coordinates against a nominal bed size."""
    validate_bed_settings(config)
    width, height = bed_dimensions(config)
    if not placements:
        raise ValueError('No cut templates fit this sheath. No cutting DXF was exported.')
    bounds = []
    for index, placement in enumerate(placements, 1):
        polygon = Polygon(placement['polygon'])
        if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
            raise ValueError(f'Cut {index} has invalid geometry. Review the template before export.')
        x0, y0, x1, y1 = polygon.bounds
        if not all(isfinite(value) for value in polygon.bounds):
            raise ValueError(f'Cut {index} has non-finite coordinates.')
        bounds.append(polygon.bounds)
    return {'width_mm': width, 'height_mm': height, 'coordinate_system': 'laser_mm',
            'checked_cuts': len(placements), 'within_bed': None, 'bounds_enforced': False,
            'export_bounds_mm': {'min_x': min(b[0] for b in bounds), 'min_y': min(b[1] for b in bounds),
                                 'max_x': max(b[2] for b in bounds), 'max_y': max(b[3] for b in bounds)}}
