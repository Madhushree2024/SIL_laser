"""No undocumented network packets or guessed vendor DLL signatures are executed."""
from ipaddress import IPv4Address

BLOCKER = ('AWC7813 Ethernet protocol/SDK contract, network port, job encoder, firmware compatibility '
           'and safety/status semantics have not been verified. Direct controller commands are disabled.')


def controller_configuration(config):
    value = config.get('laser_control', {})
    if not isinstance(value, dict):
        raise ValueError('laser_control settings must be an object.')
    address = str(IPv4Address(value.get('controller_ip', '192.168.8.8')))
    return {'model': 'Trocen AWC7813 CON', 'transport': 'ethernet', 'ip': address,
            'port': None, 'firmware': None, 'protocol_verified': False,
            'capabilities': {name: False for name in ('connect', 'transfer', 'start', 'pause', 'resume', 'stop', 'status', 'interlocks')},
            'blocker': BLOCKER}
