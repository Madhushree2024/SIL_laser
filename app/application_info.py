
"""Read-only deployment identity; never launches LaserCAD or sends commands."""
from pathlib import Path
import os
import sys
from app.export.lasercad import find_lasercad

APP_VERSION = '2026.11.18.2'
UPDATE_NAME = 'Remote-injected completion Space consumed safely; physical and first/second workflow unchanged'

def application_info(config, windows_getter):
    desktop = bool(getattr(sys, 'frozen', False))
    app_path = Path(sys.executable).resolve() if desktop else Path(__file__).resolve().parents[1] / 'desktop.py'
    configured = str(config.get('lasercad', {}).get('executable', '') or '')
    resolved = find_lasercad(configured)
    expected = str(Path(resolved).resolve()) if resolved else None
    running, error, window_count = [], None, 0
    try:
        for window in windows_getter():
            if str(window.get('title', '')).lower().startswith('lasercad'):
                window_count += 1
                path = window.get('executable')
                if path:
                    path = str(Path(path).resolve())
                    if path not in running:
                        running.append(path)
    except Exception as exc:
        error = str(exc)
    matching = bool(expected and any(os.path.normcase(p) == os.path.normcase(expected) for p in running))
    state = 'unavailable' if error else 'not_running' if not running else 'multiple' if window_count>1 else 'matching' if matching else 'different'
    return {'version': APP_VERSION, 'update': UPDATE_NAME, 'desktop': desktop,
            'app_path': str(app_path), 'configured_lasercad_path': configured,
            'resolved_lasercad_path': expected, 'running_lasercad_paths': running,
            'lasercad_state': state, 'inspection_error': error}
