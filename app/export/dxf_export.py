from __future__ import annotations

from pathlib import Path

from .common_line import common_cut_paths

import ezdxf


def _add_polyline(modelspace, points: list[list[float]], layer: str) -> None:
    if len(points) >= 3:
        modelspace.add_lwpolyline([(x, y) for x, y in points], close=True, dxfattribs={"layer": layer})


def export_dxf(path: Path, sheath: list[list[float]], placements: list[dict], include_sheath: bool = True, *, sheaths: list[list[list[float]]] | None = None, shared_cut: bool = False) -> Path:
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    doc.layers.add("SHEATH", color=8)
    doc.layers.add("CUT", color=1)
    msp = doc.modelspace()
    if include_sheath:
        for polygon in (sheaths if sheaths is not None else [sheath]):
            _add_polyline(msp, polygon, "SHEATH")
    if shared_cut:
        for points in common_cut_paths(placements):
            msp.add_lwpolyline(points, close=False, dxfattribs={"layer": "CUT"})
    else:
        for placement in placements:
            _add_polyline(msp, placement["polygon"], "CUT")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path
