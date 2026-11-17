def safety_status():
    return {'verified': False, 'start_allowed': False,
            'emergency_stop': None, 'water_flow': None, 'door': None, 'exhaust': None,
            'laser_protection': None, 'limit_switches': None,
            'message': 'Machine protections remain hardware-controlled. Their live state is unavailable; operator checkboxes cannot substitute for telemetry.'}
