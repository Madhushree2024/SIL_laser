from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
import desktop_runtime as desktop


def test_non_windows_leaves_files_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, 'platform', 'linux')
    assert desktop.unblock_bundled_runtime(tmp_path) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows download-zone streams')
def test_removes_runtime_download_blocks_preserving_dll_bytes_and_calibration(tmp_path):
    runtime = tmp_path / 'pythonnet/runtime/Python.Runtime.dll'
    calibration = tmp_path / 'seed/camera_calibration.npz'
    for path in (runtime, calibration):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original bytes')
        Path(str(path) + ':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3')
    repaired = desktop.unblock_bundled_runtime(tmp_path)
    assert repaired == [str(runtime.relative_to(tmp_path))]
    assert runtime.read_bytes() == b'original bytes'
    assert not Path(str(runtime) + ':Zone.Identifier').exists()
    assert calibration.read_bytes() == b'original bytes'
    assert Path(str(calibration) + ':Zone.Identifier').exists()
    assert desktop.unblock_bundled_runtime(tmp_path) == []


def test_runtime_repair_precedes_explicit_windows_clr_initialization(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, 'unblock_bundled_runtime', lambda: calls.append('repair') or [])
    monkeypatch.setitem(sys.modules, 'pythonnet', SimpleNamespace(load=lambda runtime: calls.append(runtime)))
    desktop.initialize_desktop_runtime()
    assert calls == ['repair', 'netfx']
