"""Job operations through the existing LaserCAD bridge, never native IP commands."""
import hashlib
from pathlib import Path


def job_controls(job, manager, panel):
    matching = False
    if job and panel.get('available') and panel.get('job_matches') and panel.get('job'):
        try:
            actual = Path(panel['job']).resolve()
            expected = {manager.path(job).resolve()}
            if job.get('source_path'):
                expected.add(Path(job['source_path']).resolve())
            matching = actual in expected and actual.is_file() and hashlib.sha256(actual.read_bytes()).hexdigest() == job['sha256']
        except (OSError, ValueError):
            matching = False
    actions = panel.get('actions', {})
    valid = bool(job and job['validation']['valid'])
    return {'mode': 'lasercad', 'available': bool(panel.get('available')), 'matching_job': matching,
            'panel_message': panel.get('message', ''),
            'window_title': panel.get('window_title'), 'window_id': panel.get('window_id'),
            'process_id': panel.get('process_id'),
            'communication_selection': panel.get('communication_selection', []),
            'pause_resume_mode': 'operator_confirmed_toggle',
            'actions': {'download': bool(valid and matching and actions.get('download')),
                        'frame': bool(valid and matching and actions.get('frame')),
                        'stop': bool(panel.get('available') and actions.get('stop')),
                        'start': bool(valid and matching and actions.get('start')),
                        'pause': bool(valid and matching and actions.get('pause')),
                        'resume': bool(valid and matching and actions.get('pause'))},
            'message': 'Operator-confirmed LaserCAD panel requests only. Pause/Resume share its toggle; current machine state and interlocks are not telemetry-verified.'}
