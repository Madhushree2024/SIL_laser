from __future__ import annotations

from pathlib import Path

from .common_line import common_cut_paths


def _path(points: list[list[float]]) -> str:
    if not points:
        return ""
    commands = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    commands.extend(f"L {x:.3f} {y:.3f}" for x, y in points[1:])
    commands.append("Z")
    return " ".join(commands)


def export_svg(path: Path, sheath: list[list[float]], placements: list[dict], include_sheath: bool = True, *, sheaths: list[list[list[float]]] | None = None, shared_cut: bool = False, y_up: bool = False) -> Path:
    boundaries = sheaths if sheaths is not None else [sheath]
    all_points = [point for polygon in boundaries for point in polygon] + [pt for placement in placements for pt in placement["polygon"]]
    min_x=min((p[0] for p in all_points),default=0)
    max_x=max((p[0] for p in all_points),default=600)
    min_y=min((p[1] for p in all_points),default=0)
    max_y=max((p[1] for p in all_points),default=450)
    width=max(max_x-min_x,0.001);height=max(max_y-min_y,0.001)
    view_y=-max_y if y_up else min_y
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}mm" height="{height:.3f}mm" viewBox="{min_x:.3f} {view_y:.3f} {width:.3f} {height:.3f}">',
        '<g transform="scale(1,-1)">' if y_up else '<g>',
        '<g id="REFERENCE" fill="none" stroke="#999" stroke-width="0.2">',
    ]
    if include_sheath:
        for i, polygon in enumerate(boundaries, start=1):
            identifier = "SHEATH" if len(boundaries) == 1 else f"SHEATH_{i}"
            parts.append(f'<path id="{identifier}" d="{_path(polygon)}" />')
    parts.append("</g>")
    parts.append('<g id="CUT" fill="none" stroke="#ff0000" stroke-width="0.1">')
    if shared_cut:
        for i, points in enumerate(common_cut_paths(placements), start=1):
            commands = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
            commands.extend(f"L {x:.3f} {y:.3f}" for x, y in points[1:])
            parts.append(f'<path id="COMMON_CUT_{i}" d="{" ".join(commands)}" />')
    else:
        for i, placement in enumerate(placements, start=1):
            parts.append(f'<path id="CUT_{i}_{placement["template_id"]}" d="{_path(placement["polygon"])}" />')
    parts.append("</g></g></svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts))
    return path
