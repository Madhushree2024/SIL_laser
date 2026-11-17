import pytest
import app.main as main
from test_laser_control_api import laser_workspace


@pytest.mark.parametrize('path', [
    '/api/laser-control/command', '/api/laser-control/fallback-command',
    '/api/laser/control/command', '/api/laser-control/layout', '/api/laser-control/cycle/review',
])
def test_disabled_dashboard_blocks_dispatch_before_bridge(laser_workspace,monkeypatch,path):
    client,root,config=laser_workspace
    config['laser_control']['dashboard_enabled']=False
    monkeypatch.setattr(main.lasercad_controller,'request',lambda *a,**k:pytest.fail('Disabled dashboard dispatched a command'))
    result=client.post(path,json={})
    assert result.status_code==409
    assert 'temporarily disabled' in result.json()['detail']


def test_disabled_dashboard_keeps_native_host_and_job_inspection(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    config['laser_control']['dashboard_enabled']=False
    calls=[]
    monkeypatch.setattr(main.laser_control_service,'host_callback',lambda payload:calls.append(payload) or {'status':'hosted'})
    result=client.post('/api/laser-control/lasercad-host',json={'action':'show','width':1920,'height':938,'viewport_width':1920,'viewport_height':974,'y':36})
    assert result.status_code==200
    assert calls[0]['height']==938
    assert client.get('/api/laser-control/status').status_code==200


def test_disabled_dashboard_prevents_direct_layout_callback(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    config['laser_control']['dashboard_enabled']=False
    monkeypatch.setattr(main.laser_control_service,'layout_callback',lambda *a:pytest.fail('Split callback must not run'))
    with pytest.raises(ValueError,match='temporarily disabled'):
        main.laser_control_service.layout('split')
