import csv
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Event

import pytest
import app.main as main
import app.export.daily_production as daily
from app.export.daily_production import FIELDS, record_daily_production
from test_multi_sheath import workspace, wait


def rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def fixture_record(root, key="one", timestamp="2026-10-15T03:30:00+00:00"):
    folder = root / key / "exports"
    folder.mkdir(parents=True)
    vector = folder / "layout.svg"
    vector.write_text("<svg/>", encoding="utf-8")
    report = vector.with_suffix(".csv")
    with report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["exported_at_utc", "source_image"])
        writer.writeheader()
        writer.writerow({"exported_at_utc": timestamp, "source_image": str(folder.parent / "original.jpg")})
    return dict(workflow_id=key, cycle_number=1, output={"path": str(vector), "csv": {"path": str(report)}},
                nesting={"placements": [{"template_id": "t", "template_name": "7 inch, café"}] * 2,
                         "sheaths": [{"id": "s1", "measurements": {"aligned_width_mm": 263.123456789, "aligned_height_mm": 262}}],
                         "requested_pieces": 3, "sheath_area_mm2": 10000, "used_area_mm2": 7500,
                         "utilization": .75, "waste": .25, "settings": {"strategy": "shared_side"}},
                detection={}, events=[{"stage": "Capturing image", "elapsed_seconds": 1.5}, {"stage": "Exporting", "elapsed_seconds": .25}])


def test_daily_csv_preserves_counts_precision_unicode_and_measured_times(tmp_path):
    record = fixture_record(tmp_path)
    path = record_daily_production(tmp_path / "production", **record)
    assert path.name == "production_2026-10-15.csv"
    data = rows(path)
    assert len(data) == 1 and list(data[0]) == FIELDS
    row = data[0]
    assert row["timezone"] == "Asia/Kolkata" and row["exported_at_local"].endswith("+05:30")
    assert row["exported_piece_count"] == "2" and row["requested_piece_count"] == "3"
    assert row["utilization_percent"] == "75.0" and row["waste_percent"] == "25.0"
    assert row["capture_seconds"] == "1.5" and row["export_seconds"] == "0.25" and row["cycle_seconds"] == "1.75"
    assert row["detection_seconds"] == "" and row["status"] == "exported"
    assert json.loads(row["template_counts_json"]) == [{"template_id": "t", "name": "7 inch, café", "count": 2}]
    assert json.loads(row["sheath_measurements_json"])[0]["measurements"]["aligned_width_mm"] == 263.123456789
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_two_cycles_append_and_retry_is_duplicate_safe_using_persistent_ids(tmp_path):
    directory = tmp_path / "production"
    first = fixture_record(tmp_path, "first")
    path = record_daily_production(directory, **first)
    original = path.read_bytes()
    assert record_daily_production(directory, **first) == path
    assert path.read_bytes() == original
    record_daily_production(directory, **fixture_record(tmp_path, "second"))
    assert [row["workflow_id"] for row in rows(path)] == ["first", "second"]


def test_date_rollover_uses_india_export_date_and_retries_keep_original_date(tmp_path):
    directory = tmp_path / "production"
    first = fixture_record(tmp_path, "before", "2026-10-14T18:29:59+00:00")
    second = fixture_record(tmp_path, "after", "2026-10-14T18:30:00+00:00")
    before = record_daily_production(directory, **first)
    after = record_daily_production(directory, **second)
    assert before.name == "production_2026-10-14.csv"
    assert after.name == "production_2026-10-15.csv"
    assert record_daily_production(directory, **first) == before
    assert len(rows(before)) == len(rows(after)) == 1


def test_concurrent_records_do_not_lose_rows_or_repeat_header(tmp_path):
    records = [fixture_record(tmp_path, f"cycle-{index}") for index in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda value: record_daily_production(tmp_path / "production", **value), records))
    assert len(set(paths)) == 1
    data = rows(paths[0])
    assert len(data) == 12 and len({row["workflow_id"] for row in data}) == 12
    assert sum(int(row["exported_piece_count"]) for row in data) == 24


def test_atomic_failure_preserves_prior_day_rows_and_retry_saves_once(tmp_path, monkeypatch):
    directory = tmp_path / "production"
    path = record_daily_production(directory, **fixture_record(tmp_path, "first"))
    original = path.read_bytes()
    second = fixture_record(tmp_path, "second")
    real_replace = daily.os.replace
    monkeypatch.setattr(daily.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("CSV open in Excel")))
    with pytest.raises(PermissionError, match="Excel"):
        record_daily_production(directory, **second)
    assert path.read_bytes() == original
    assert not list(directory.glob("*.tmp"))
    monkeypatch.setattr(daily.os, "replace", real_replace)
    record_daily_production(directory, **second)
    record_daily_production(directory, **second)
    assert len(rows(path)) == 2


def test_invalid_header_and_conflicting_id_preserve_existing_data(tmp_path):
    directory = tmp_path / "production"
    record = fixture_record(tmp_path, "first")
    path = record_daily_production(directory, **record)
    original = path.read_bytes()
    conflict = fixture_record(tmp_path, "different")
    conflict["workflow_id"] = "first"
    with pytest.raises(ValueError, match="another export"):
        record_daily_production(directory, **conflict)
    assert path.read_bytes() == original
    path.write_text("old,unrelated\nkeep,data\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        record_daily_production(directory, **record)
    assert path.read_text(encoding="utf-8") == "old,unrelated\nkeep,data\n"


def test_missing_export_does_not_create_production_file(tmp_path):
    record = fixture_record(tmp_path)
    Path(record["output"]["path"]).unlink()
    with pytest.raises(ValueError, match="verified"):
        record_daily_production(tmp_path / "production", **record)
    assert not (tmp_path / "production").exists()


def request(manager):
    return {"templates": [asdict(t) for t in manager.list()], "strategy": "shared_side", "export": {"format": "svg"}}


def test_real_multi_sheath_cycle_records_totals_and_data_download_then_manual_reexport_does_not_count_again(workspace):
    client, manager, root, _ = workspace
    assert client.post("/api/workflow", json=request(manager)).status_code == 202
    status = wait(client)
    assert status["stage"] == "Completed", status
    production = status["result"]["export"]["daily_production"]
    row = rows(production["path"])[0]
    assert row["workflow_id"] == status["id"] and row["sheath_count"] == "2" and row["exported_piece_count"] == "4"
    assert json.loads(row["template_counts_json"])[0]["count"] == 4
    assert len(json.loads(row["sheath_measurements_json"])) == 2
    assert float(row["material_area_mm2"]) == status["result"]["nesting"]["sheath_area_mm2"]
    assert float(row["utilization_percent"]) == pytest.approx(status["result"]["nesting"]["utilization"] * 100)
    assert float(row["cycle_seconds"]) >= 0
    listing = client.get("/api/projects").json()["production_reports"]
    assert listing[0]["url"].startswith("/data/production/production_")
    assert client.post("/api/export", json={"format": "svg"}).status_code == 200
    assert len(rows(production["path"])) == 1
    assert client.post("/api/workflow", json=request(manager)).status_code == 202
    second = wait(client)
    assert second["stage"] == "Completed"
    assert sum(len(rows(path)) for path in (root / "data/production").glob("*.csv")) == 2


def test_daily_storage_failure_preserves_export_and_retry_does_not_recapture_or_reexport(workspace, monkeypatch):
    client, manager, root, _ = workspace
    original_record, original_capture, original_export = main.record_daily_production, main._capture_raw, main.export
    calls = {"capture": 0, "export": 0}
    def capture():
        calls["capture"] += 1
        return original_capture()
    def export(value):
        calls["export"] += 1
        return original_export(value)
    monkeypatch.setattr(main, "_capture_raw", capture)
    monkeypatch.setattr(main, "export", export)
    monkeypatch.setattr(main, "record_daily_production", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("open in Excel")))
    assert client.post("/api/workflow", json=request(manager)).status_code == 202
    status = wait(client)
    assert status["stage"] == "Failed" and "daily production CSV" in status["error"]
    assert not main.operation_lock.locked()
    assert Path(status["artifacts"]["export"]["path"]).is_file()
    assert Path(status["artifacts"]["export"]["csv"]["path"]).is_file()
    assert client.post("/api/production/retry", json={}).status_code == 400
    monkeypatch.setattr(main, "record_daily_production", original_record)
    response = client.post("/api/production/retry", json={})
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["stage"] == "Completed"
    assert all(event["status"] == "completed" for event in response.json()["workflow"]["events"])
    assert len(rows(response.json()["report"]["path"])) == 1
    assert client.post("/api/production/retry", json={}).status_code == 400
    assert calls == {"capture": 1, "export": 1}


def test_worker_stays_busy_until_daily_record_is_durable(workspace, monkeypatch):
    client, manager, root, _ = workspace
    entered, release = Event(), Event()
    original = main.record_daily_production
    def waiting(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(main, "record_daily_production", waiting)
    try:
        assert client.post("/api/workflow", json=request(manager)).status_code == 202
        assert entered.wait(5)
        status = client.get("/api/workflow").json()
        assert status["busy"] and status["stage"] == "Exporting" and status["result"] is None
        assert client.post("/api/production/retry", json={}).status_code == 409
        assert client.post("/api/workflow", json=request(manager)).status_code == 409
    finally:
        release.set()
        status = wait(client)
    assert status["stage"] == "Completed"


def test_failed_export_cannot_be_counted_or_retried_as_production(workspace, monkeypatch):
    client, manager, root, _ = workspace
    monkeypatch.setattr(main, "export", lambda value: (_ for _ in ()).throw(OSError("failed export")))
    client.post("/api/workflow", json=request(manager))
    assert wait(client)["stage"] == "Failed"
    assert not list((root / "data/production").glob("*.csv"))
    assert client.post("/api/production/retry", json={}).status_code == 400


def test_saved_record_recovers_after_write_failure_and_keeps_first_measured_times(tmp_path, monkeypatch):
    directory = tmp_path / "production"
    record = fixture_record(tmp_path, "pending")
    real_replace = daily.os.replace
    def replace(source, target):
        if Path(target).parent == directory:
            raise PermissionError("daily file locked")
        return real_replace(source, target)
    monkeypatch.setattr(daily.os, "replace", replace)
    with pytest.raises(PermissionError):
        record_daily_production(directory, **record)
    assert Path(record["output"]["path"]).with_suffix(".production.json").is_file()
    monkeypatch.setattr(daily.os, "replace", real_replace)
    checked, paths = daily.recover_daily_production(directory, tmp_path)
    assert checked == 1 and len(rows(paths[0])) == 1
    assert rows(paths[0])[0]["cycle_seconds"] == "1.75"
    record["events"] = [{"stage": "Exporting", "elapsed_seconds": 999}]
    record_daily_production(directory, **record)
    assert rows(paths[0])[0]["cycle_seconds"] == "1.75"
    assert daily.recover_daily_production(directory, tmp_path)[0] == 1
    assert len(rows(paths[0])) == 1


def test_recovery_after_restart_rebuilds_daily_file_without_any_hardware_or_export(workspace, monkeypatch):
    client, manager, root, _ = workspace
    client.post("/api/workflow", json=request(manager))
    status = wait(client)
    path = Path(status["result"]["export"]["daily_production"]["path"])
    path.unlink()
    monkeypatch.setattr(main, "WORKFLOW", {"id": None, "stage": "Ready", "busy": False, "error": None, "result": None})
    monkeypatch.setattr(main, "_capture_raw", lambda: pytest.fail("Recovery must not capture"))
    monkeypatch.setattr(main, "nest", lambda value: pytest.fail("Recovery must not nest"))
    monkeypatch.setattr(main, "export", lambda value: pytest.fail("Recovery must not export"))
    response = client.post("/api/production/recover", json={})
    assert response.status_code == 200 and response.json()["checked"] == 1
    assert len(rows(path)) == 1 and rows(path)[0]["workflow_id"] == status["id"]
    assert client.post("/api/production/recover", json={}).status_code == 200
    assert len(rows(path)) == 1
    assert client.get("/api/workflow").json()["stage"] == "Ready"


def test_invalid_saved_record_date_cannot_write_outside_production_folder(tmp_path):
    record = fixture_record(tmp_path)
    directory = tmp_path / "production"
    path = record_daily_production(directory, **record)
    original = path.read_bytes()
    saved = Path(record["output"]["path"]).with_suffix(".production.json")
    value = json.loads(saved.read_text(encoding="utf-8"))
    value["record"]["production_date"] = "../../outside"
    saved.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        daily.recover_daily_production(directory, tmp_path)
    assert path.read_bytes() == original and not (tmp_path / "outside").exists()
