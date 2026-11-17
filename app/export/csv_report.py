"""Atomic CSV sidecar with source measurements and actual exported cut geometry."""
from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Polygon
from app.vision.measurements import polygon_measurements

FIELDS = [
    "record_type", "exported_at_utc", "workflow_id", "cycle_number", "project_id", "source_image", "vector_export",
    "export_format", "coordinate_system", "sheath_id", "sheath_width_mm", "sheath_height_mm", "sheath_area_mm2",
    "sheath_used_area_mm2", "sheath_utilization", "sheath_cut_count", "requested_pieces", "quantity_scope",
    "cut_number", "template_id", "template_name", "template_shape", "template_width_mm", "template_height_mm",
    "priority", "requested_quantity", "placed_quantity", "unplaced_quantity", "allowed_rotations_deg",
    "design_x_mm", "design_y_mm", "design_rotation_deg", "export_x_mm", "export_y_mm", "export_rotation_deg",
    "design_polygon_mm", "export_polygon_mm", "strategy", "refinement_mm", "spacing_mm", "edge_clearance_mm",
    "scale_source", "calibration_image_width", "calibration_image_height", "nominal_mm_per_pixel", "measurement_calibration", "alignment_matrix",
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _cell(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("CSV contains a non-finite measurement")
    # Preserve ordinary names; prevent spreadsheet interpretation of user text.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv_report(path: Path, result: dict, sheath: list, *, vector_path: Path, export_format: str,
                     exported_placements: list[dict], calibration: dict | None = None, alignment: dict | None = None,
                     workflow_id: str = "", cycle_number=None, project_id: str = "", source_image: str = "") -> Path:
    calibration = calibration or {}
    matrix = (alignment or {}).get("matrix", [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    templates = {item["id"]: item for item in result.get("templates", [])}
    settings = result.get("settings", {})
    common = {"exported_at_utc": datetime.now(timezone.utc).isoformat(), "workflow_id": workflow_id,
              "cycle_number": cycle_number, "project_id": project_id, "source_image": source_image,
              "vector_export": str(vector_path), "export_format": export_format,
              "coordinate_system": "aligned_export_mm" if (alignment or {}).get("alignment", {}).get("enabled") else "design_mm",
              "quantity_scope": result.get("quantity_scope", "per_sheath"), "strategy": settings.get("strategy"),
              "refinement_mm": settings.get("precision_mm"), "spacing_mm": settings.get("spacing_mm"),
              "edge_clearance_mm": settings.get("edge_clearance_mm"), "scale_source": calibration.get("source"),
              "calibration_image_width": calibration.get("image_width"), "calibration_image_height": calibration.get("image_height"),
              "nominal_mm_per_pixel": calibration.get("mm_per_pixel"), "measurement_calibration": _json(calibration), "alignment_matrix": _json(matrix)}
    pieces = result.get("sheaths") or [{"id": "sheath-1", "mm_polygon": sheath, "placements": result.get("placements", []),
                                       "requested_pieces": result.get("requested_pieces"), "unplaced_templates": result.get("unplaced_templates", [])}]
    rows = []
    cuts = result.get("placements", [])
    for piece in pieces:
        placements = piece.get("placements", [])
        counts = Counter(item["template_id"] for item in placements)
        m = piece.get("measurements") or polygon_measurements(piece["mm_polygon"])
        base = {**common, "sheath_id": piece["id"], "sheath_width_mm": m.get("aligned_width_mm", m["width_mm"]),
                "sheath_height_mm": m.get("aligned_height_mm", m["height_mm"]), "sheath_area_mm2": m["area_mm2"],
                "sheath_used_area_mm2": piece.get("used_area_mm2"), "sheath_utilization": piece.get("utilization"),
                "sheath_cut_count": len(placements), "requested_pieces": piece.get("requested_pieces")}
        rows.append({**base, "record_type": "sheath"})
        for index, original in enumerate(cuts):
            if original.get("sheath_id", pieces[0]["id"]) != piece["id"]:
                continue
            actual = exported_placements[index]
            template = templates.get(original["template_id"], {})
            center = Polygon(original["polygon"]).centroid
            exported_center = Polygon(actual["polygon"]).centroid
            angle = original.get("rotation_deg")
            export_angle = None
            if angle is not None:
                radians = math.radians(angle)
                dx = matrix[0][0] * math.cos(radians) + matrix[0][1] * math.sin(radians)
                dy = matrix[1][0] * math.cos(radians) + matrix[1][1] * math.sin(radians)
                export_angle = math.degrees(math.atan2(dy, dx)) % 360
            quantity = template.get("quantity")
            rows.append({**base, "record_type": "cut", "cut_number": index + 1,
                         "template_id": original["template_id"], "template_name": original.get("template_name", template.get("name")),
                         "template_shape": template.get("shape"), "template_width_mm": template.get("width_mm"),
                         "template_height_mm": template.get("height_mm"), "priority": template.get("priority"),
                         "requested_quantity": quantity, "placed_quantity": counts[original["template_id"]],
                         "unplaced_quantity": max(0, quantity - counts[original["template_id"]]) if quantity is not None else None,
                         "allowed_rotations_deg": _json(template.get("allowed_rotations", [])),
                         "design_x_mm": center.x, "design_y_mm": center.y, "design_rotation_deg": angle,
                         "export_x_mm": exported_center.x, "export_y_mm": exported_center.y, "export_rotation_deg": export_angle,
                         "design_polygon_mm": _json(original["polygon"]), "export_polygon_mm": _json(actual["polygon"])})
        for missing in piece.get("unplaced_templates", []):
            template = templates.get(missing["template_id"], {})
            rows.append({**base, "record_type": "unplaced_template", "template_id": missing["template_id"],
                         "template_name": missing.get("name", template.get("name")), "priority": template.get("priority"),
                         "template_shape": template.get("shape"), "template_width_mm": template.get("width_mm"),
                         "template_height_mm": template.get("height_mm"), "requested_quantity": missing.get("requested"),
                         "placed_quantity": missing.get("placed"), "unplaced_quantity": missing.get("unplaced")})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _cell(value) for key, value in row.items()})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
