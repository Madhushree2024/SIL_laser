import hashlib
from pathlib import Path
import re
from uuid import uuid4
from .cutting_parameters import CuttingParameters, LayerParameters
from .dxf_validator import MAX_FILE_BYTES, VALIDATION_POLICY, validate_dxf
from .job_history import JobHistory, now


class DxfJobManager:
    def __init__(self, history: JobHistory, config: dict):
        self.history, self.config = history, config

    def receive(self, content: bytes, filename: str, source: str, source_path=''):
        if len(content) > MAX_FILE_BYTES:
            raise ValueError('DXF exceeds the 20 MB size limit.')
        filename = Path(filename.replace('\\', '/')).name
        if not filename.lower().endswith('.dxf') or re.search(r'[\x00-\x1f]', filename) or len(filename) > 200:
            raise ValueError('Select a DXF filename of at most 200 characters.')
        digest = hashlib.sha256(content).hexdigest()
        identity = hashlib.sha256((source + '\0' + source_path + '\0' + digest).encode()).hexdigest()
        previous = self.history.find_identity(identity)
        if previous:
            return self.refresh_validation(previous)
        job_id = uuid4().hex
        folder = self.history.directory / 'jobs' / job_id
        folder.mkdir(parents=True)
        # Unique titles preserve the existing LaserCAD bridge's document matching.
        path = folder / f'{job_id}.dxf'
        path.write_bytes(content)
        validation = validate_dxf(path, self.config)
        parameters = CuttingParameters(layers={layer['name']: LayerParameters(enabled=layer['default_enabled']) for layer in validation['layers']})
        job = {'id': job_id, 'identity': identity, 'filename': filename, 'sha256': digest,
               'source': source, 'source_path': source_path, 'created_at': now(),
               'status': 'validated' if validation['valid'] else 'invalid', 'revision': 1,
               'validation': validation, 'parameters': parameters.model_dump(), 'prepared': None,
               'started_at': None, 'completed_at': None, 'execution_seconds': None,
               'controller_job': None, 'last_error': None}
        return self.history.save(job, 'received', {'source': source, 'validation_valid': validation['valid']})

    def path(self, job):
        path = self.history.directory / 'jobs' / job['id'] / f"{job['id']}.dxf"
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != job['sha256']:
            raise ValueError('Saved DXF is missing or has changed. Import the job again.')
        return path

    def refresh_validation(self, job):
        if job['validation'].get('policy') == VALIDATION_POLICY:
            return job
        job['validation'] = validate_dxf(self.path(job), self.config)
        job.update(status='validated' if job['validation']['valid'] else 'invalid',
                   prepared=None, revision=job['revision']+1, last_error=None)
        return self.history.save(job, 'validation_policy_updated', {'policy': VALIDATION_POLICY})
