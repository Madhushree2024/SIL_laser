"""Durable, duplicate-safe daily ledger of successfully exported automatic cycles."""
from __future__ import annotations

import csv
import math
import json
import logging
import os
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from .csv_report import _cell, _json

PRODUCTION_TIMEZONE = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
_WRITE_LOCK = RLock()
STAGE_FIELDS = {
    "Capturing image": "capture_seconds",
    "Correcting distortion": "correction_seconds",
    "Detecting material and converting coordinates": "detection_seconds",
    "Processing and applying nesting priority": "nesting_seconds",
    "Exporting": "export_seconds",
}
FIELDS = [
    "production_date", "exported_at_local", "timezone", "workflow_id", "cycle_number", "status",
    "project_dir", "source_image", "export_format", "vector_export", "cycle_csv",
    "sheath_count", "exported_piece_count", "requested_piece_count", "material_area_mm2", "used_area_mm2",
    "utilization_percent", "waste_percent", "capture_seconds", "correction_seconds", "detection_seconds",
    "nesting_seconds", "export_seconds", "cycle_seconds", "strategy", "template_counts_json", "sheath_measurements_json",
]


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def record_daily_production(directory: Path, *, workflow_id: str, cycle_number, nesting: dict,
                            detection: dict, output: dict, events: list[dict]) -> Path:
    """One row per workflow ID; date comes from the original immutable per-cycle CSV."""
    vector = Path(output["path"])
    report = Path(output["csv"]["path"])
    if not workflow_id or not nesting.get("placements") or not vector.is_file() or not report.is_file():
        raise ValueError("A verified vector export and per-cycle CSV are required before recording production")
    with report.open(encoding="utf-8-sig", newline="") as stream:
        source = next(csv.DictReader(stream), None)
    if not source or not source.get("exported_at_utc"):
        raise ValueError("Per-cycle CSV has no export timestamp")
    exported_at = datetime.fromisoformat(source["exported_at_utc"])
    if exported_at.tzinfo is None:
        raise ValueError("Per-cycle export timestamp must include its timezone")
    local_time = exported_at.astimezone(PRODUCTION_TIMEZONE)
    templates = {item["id"]: item for item in nesting.get("templates", [])}
    counts = Counter(item["template_id"] for item in nesting["placements"])
    names = {item["template_id"]: item.get("template_name", templates.get(item["template_id"], {}).get("name", "")) for item in nesting["placements"]}
    sheaths = nesting.get("sheaths") or detection.get("sheaths") or [{"id": "sheath-1", "measurements": detection.get("measurements", {})}]
    row = {
        "production_date": local_time.date().isoformat(), "exported_at_local": local_time.isoformat(), "timezone": "Asia/Kolkata",
        "workflow_id": workflow_id, "cycle_number": cycle_number, "status": "exported",
        "project_dir": str(vector.parent.parent), "source_image": source.get("source_image", ""),
        "export_format": vector.suffix.lstrip(".").lower(), "vector_export": str(vector), "cycle_csv": str(report),
        "sheath_count": len(sheaths), "exported_piece_count": len(nesting["placements"]),
        "requested_piece_count": nesting.get("requested_pieces"), "material_area_mm2": nesting.get("sheath_area_mm2"),
        "used_area_mm2": nesting.get("used_area_mm2"), "strategy": nesting.get("settings", {}).get("strategy", ""),
        "template_counts_json": _json([{"template_id": key, "name": names[key], "count": count} for key, count in counts.items()]),
        "sheath_measurements_json": _json([{"sheath_id": piece["id"], "measurements": piece.get("measurements", {})} for piece in sheaths]),
    }
    for field in ("utilization", "waste"):
        value = nesting.get(field)
        row[field + "_percent"] = value * 100 if _finite(value) and 0 <= value <= 1 else ""
    times = {}
    for event in events:
        if event.get("stage") in STAGE_FIELDS and _finite(event.get("elapsed_seconds")) and event["elapsed_seconds"] >= 0:
            times[STAGE_FIELDS[event["stage"]]] = event["elapsed_seconds"]
    row.update(times)
    row["cycle_seconds"] = sum(times.values()) if times else ""
    row = {key: _cell(value) for key, value in row.items()}
    # Persist the exact record beside the successful export before updating the daily file.
    # Recovery can then finish the append after a restart or after another cycle starts.
    intent = vector.with_suffix(".production.json")
    with _WRITE_LOCK:
        if intent.exists():
            saved = _read_saved_record(intent)
            if saved["workflow_id"] != str(row["workflow_id"]):
                raise ValueError("Saved production record belongs to a different workflow")
            row = saved
        else:
            _save_record(intent, row)
        return _append_daily_row(directory, row)


def _save_record(path: Path, row: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "record": row}, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_saved_record(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data.get("record")
    if data.get("version") != 1 or not isinstance(row, dict) or not set(row).issubset(FIELDS):
        raise ValueError(f"Invalid saved production record: {path}")
    if not row.get("workflow_id") or row.get("status") != "exported" or row.get("timezone") != "Asia/Kolkata":
        raise ValueError(f"Incomplete saved production record: {path}")
    if date.fromisoformat(row["production_date"]).isoformat() != row["production_date"]:
        raise ValueError("Invalid production date")
    if any(not isinstance(value, (str, int, float, type(None))) for value in row.values()):
        raise ValueError("Invalid production record value")
    for value in row.values():
        _cell(value)
    vector = Path(row["vector_export"])
    if (vector.parent.resolve() != path.parent.resolve() or vector.stem != path.name.removesuffix(".production.json")
            or vector.suffix not in {".svg", ".dxf"} or Path(row["cycle_csv"]).resolve() != vector.with_suffix(".csv").resolve()):
        raise ValueError(f"Saved production record points outside its export: {path}")
    if isinstance(row.get("exported_piece_count"), bool) or not isinstance(row.get("exported_piece_count"), int) or row["exported_piece_count"] < 1:
        raise ValueError("Saved production record has no exported pieces")
    return {key: _cell(value) for key, value in row.items()}


def recover_daily_production(directory: Path, projects: Path) -> tuple[int, list[Path]]:
    files = set()
    records = sorted(projects.glob("*/exports/*.production.json"))
    for saved in records:
        with _WRITE_LOCK:
            row = _read_saved_record(saved)
            files.add(_append_daily_row(directory, row))
    return len(records), sorted(files, reverse=True)


def _append_daily_row(directory: Path, row: dict) -> Path:
    path = directory / f"production_{date.fromisoformat(row['production_date']).isoformat()}.csv"
    directory.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        previous = []
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != FIELDS:
                    raise ValueError("Existing daily production CSV has an unexpected header; preserve it and check the file")
                previous = list(reader)
            if any(set(item) != set(FIELDS) or any(value is None for value in item.values()) for item in previous):
                raise ValueError("Existing daily production CSV contains a damaged row; preserve it and check the file")
            for existing in previous:
                if existing["workflow_id"] == str(row["workflow_id"]):
                    if existing["vector_export"] != str(row["vector_export"]):
                        raise ValueError("Workflow ID is already recorded for another export")
                    refresh_daily_excel(path)
                    return path
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=directory,
                                             prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(previous)
                writer.writerow(row)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    refresh_daily_excel(path)
    return path


def refresh_daily_excel(path: Path) -> Path | None:
    """Excel is a derived view; its failure must not lose a verified CSV record."""
    from .production_excel import write_daily_workbook
    try:
        with _WRITE_LOCK:
            return write_daily_workbook(path)
    except Exception:
        logging.getLogger(__name__).warning("Production CSV is saved, but Excel could not be refreshed: %s. Close the workbook and download Excel again from Data.", path, exc_info=True)
        return None
