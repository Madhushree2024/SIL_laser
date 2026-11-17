import pytest
from app.laser_control.trocen_protocol import controller_configuration
from app.laser_control.trocen_connection import TrocenConnection
from app.laser_control.machine_status import monitoring_status
from app.laser_control.safety_interlocks import safety_status


def test_configuration_cannot_enable_unverified_commands():
    config={'laser_control':{'controller_ip':'192.168.8.8','enabled':True,'protocol_verified':True,'port':502}}
    result=controller_configuration(config)
    assert result['port'] is None and not result['protocol_verified']
    assert not any(result['capabilities'].values())
    assert monitoring_status()['completed'] is None
    assert safety_status()['water_flow'] is None
    assert not safety_status()['start_allowed']


@pytest.mark.parametrize('action',['connect','transfer','start','pause','resume','stop'])
def test_no_socket_or_dll_is_opened(action,monkeypatch):
    import socket,ctypes
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('No undocumented socket traffic'))
    monkeypatch.setattr(ctypes,'CDLL',lambda *a,**k:pytest.fail('No undocumented DLL calls'))
    with pytest.raises(ValueError,match='not been verified'): TrocenConnection().command(action)
