def monitoring_status():
    return {'source': 'unavailable', 'current_job': None, 'running': None, 'progress_percent': None,
            'elapsed_seconds': None, 'estimated_remaining_seconds': None,
            'alarms': None, 'faults': None, 'completed': None,
            'message': 'No verified controller telemetry. Job preparation is separate from cutting completion.'}
