from .trocen_protocol import BLOCKER, controller_configuration


class TrocenConnection:
    def status(self, config):
        return {**controller_configuration(config), 'connected': None,
                'readiness': 'unverified', 'last_controller_response_at': None}

    def command(self, action):
        # An IP or a user-editable enabled flag never grants verified capabilities.
        raise ValueError(f'{action}: {BLOCKER}')
