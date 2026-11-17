from .trocen_protocol import BLOCKER


def prepare_snapshot(job, parameters):
    if not job['validation']['valid']:
        raise ValueError('Resolve DXF validation errors before preparing a job.')
    missing = parameters.missing_settings([layer['name'] for layer in job['validation']['layers']])
    if missing:
        raise ValueError(' '.join(missing))
    return {'revision': job['revision'], 'dxf_sha256': job['sha256'], 'parameters': parameters.model_dump(),
            'controller_format': None, 'transfer_ready': False, 'status': 'conversion_required', 'blocker': BLOCKER}
