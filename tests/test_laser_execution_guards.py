import pytest
import app.main as main
from app.security.accounts import AccountStore
from test_laser_control_api import laser_workspace, drawing

pytestmark=pytest.mark.access_control
PASSWORD='Laser dashboard password 2026'


@pytest.mark.parametrize('role',['viewer','editor'])
def test_dashboard_uses_existing_role_and_csrf_boundary(laser_workspace,monkeypatch,role):
    client,root,config=laser_workspace
    store=AccountStore(root/'security/accounts.sqlite3')
    store.create('owner',PASSWORD,'admin',initial=True)
    store.create('operator',PASSWORD,role)
    monkeypatch.setitem(main.app.state._state,'account_store',lambda:store)
    assert client.get('/api/laser-control/status').status_code==401
    result=client.post('/api/auth/login',json={'username':'operator','password':PASSWORD})
    assert result.status_code==200,result.text
    assert client.post('/api/laser-control/command',json={'action':'start'}).status_code==403
    client.headers['X-CSRF-Token']=result.json()['csrf_token']
    assert client.get('/api/laser-control/status').status_code==200
    expected=403 if role=='viewer' else 200
    assert client.post('/api/laser-control/jobs/upload',files={'file':('job.dxf',drawing())}).status_code==expected
    assert client.post('/api/laser-control/profiles',json={'name':'unauthorized','parameters':{}}).status_code==403
    assert client.post('/api/laser-control/command',json={'action':'start'}).status_code in {403,409}
    body={'action':'download','job_id':None,'request_id':'11111111-1111-4111-8111-111111111111'}
    assert client.post('/api/laser-control/fallback-command',json=body).status_code==(403 if role=='viewer' else 409)
    finish={'cycle_id':'11111111-1111-4111-8111-111111111111','outcome':'completed','operator_confirmed':True,'machine_stopped':True}
    assert client.post('/api/laser-control/cycle/confirm',json=finish).status_code==(403 if role=='viewer' else 409)
