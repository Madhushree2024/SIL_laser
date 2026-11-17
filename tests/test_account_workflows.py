from pathlib import Path
from types import SimpleNamespace
import json,sqlite3
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.security.accounts import AccountStore,password_hash,PERMISSIONS,AccessError
from test_access_control import access,setup,login,PASSWORD
from test_automated_workflow import workspace,payload,finish
pytestmark=pytest.mark.access_control

@pytest.mark.parametrize('role',['viewer','editor'])
@pytest.mark.parametrize('mode',['automated','manual'])
def test_admin_assigns_workflow_without_granting_settings_or_account_management(access,role,mode):
 client,store,root=access;setup(client)
 result=client.post('/api/auth/users',json={'username':'operator','password':PASSWORD,'role':role,'workflow_mode':mode})
 assert result.status_code==200,result.text
 user=result.json()['user'];assert user['workflow_mode']==mode and 'operate' in user['permissions']
 identity=login(client,'operator');assert identity['user']['workflow_mode']==mode
 for path in ['/api/config','/api/calibration/camera','/api/laser/alignment','/api/camera/control','/api/auth/users']:
  assert client.post(path,json={}).status_code==403,path
 assert client.patch('/api/auth/users/'+user['id'],json={'role':role,'enabled':True,'workflow_mode':'automated'}).status_code==403
 if mode=='manual':assert client.post('/api/laser-control/space-cycle/action',json={}).status_code==403


def test_mode_change_revokes_existing_session_and_omitted_mode_preserves_assignment(access):
 client,store,root=access;setup(client)
 user=store.create('operator',PASSWORD,'viewer',workflow_mode='manual')
 token,session=store.login('operator',PASSWORD)
 response=client.patch('/api/auth/users/'+user['id'],json={'role':'viewer','enabled':True,'workflow_mode':'automated'})
 assert response.status_code==200 and response.json()['user']['workflow_mode']=='automated'
 assert store.session(token) is None
 assert client.patch('/api/auth/users/'+user['id'],json={'role':'viewer','enabled':True}).json()['user']['workflow_mode']=='automated'
 assert client.patch('/api/auth/users/'+user['id'],json={'role':'viewer','enabled':True,'workflow_mode':None}).json()['user']['permissions']==PERMISSIONS['viewer']


def test_legacy_accounts_migrate_without_changing_password_role_or_status(tmp_path):
 path=tmp_path/'accounts.sqlite3';hashed=password_hash(PASSWORD)
 with sqlite3.connect(path) as db:
  db.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT,enabled INTEGER,created_at REAL)')
  db.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',('one','reader',hashed,'viewer',1,123.0))
 store=AccountStore(path)
 with store.connect() as db:
  row=db.execute('SELECT * FROM users').fetchone();assert row['password_hash']==hashed and row['role']=='viewer' and row['enabled']==1 and row['created_at']==123.0 and row['workflow_mode'] is None
 assert store.users()[0]['permissions']==PERMISSIONS['viewer']
 assert AccountStore(path).users()==store.users()


def test_admin_remains_unrestricted_and_cannot_be_given_limited_mode(access):
 client,store,root=access;identity=setup(client);owner=identity['user']
 assert owner['permissions']==PERMISSIONS['admin'] and owner['workflow_mode']=='automated'
 result=client.patch('/api/auth/users/'+owner['id'],json={'role':'admin','enabled':True,'workflow_mode':'manual'})
 assert result.status_code==400
 assert client.get('/api/auth/me').json()['user']['permissions']==PERMISSIONS['admin']
 assert client.post('/api/auth/users',json={'username':'badmode','password':PASSWORD,'role':'editor','workflow_mode':'unknown'}).status_code==422

@pytest.mark.parametrize('role',['viewer','editor'])
@pytest.mark.parametrize('mode',['manual','automated'])
def test_assigned_first_space_runs_real_processing_and_uses_server_mode_not_payload(workspace,monkeypatch,role,mode):
 client,manager,root=workspace;client=TestClient(main.app,base_url='http://127.0.0.1')
 store=AccountStore(root/'security/accounts.sqlite3');store.create('operator',PASSWORD,role,workflow_mode=mode)
 monkeypatch.setitem(main.app.state._state,'account_store',lambda:store);login(client,'operator')
 config=main.load_config();config['lasercad']={'prepare_and_download':{'enabled':True}};monkeypatch.setattr(main,'load_config',lambda:config)
 def handoff(output):output['lasercad']={'import_mapping':None,'download_automation':{'status':'download_requested','download_requested':True,'communication_target':'MachName:_AWC_D2713611'}}
 monkeypatch.setattr(main,'_handoff_dxf',handoff)
 monkeypatch.setattr(main.lasercad_controller,'_window',lambda **kw:{'hwnd':1,'pid':2,'title':'LaserCAD - fresh.dxf'})
 calls=[];monkeypatch.setattr(main.lasercad_controller,'request',lambda action,**kw:calls.append(action) or {'requested':True})
 state=main.space_cycle.status()
 body=payload(manager,space_cycle_id=state['cycle_id'],space_revision=state['revision'],request_id=str(uuid4()),workflow_mode='automated',_assigned_workflow_mode='automated')
 response=client.post('/api/workflow',json=body);assert response.status_code==202,response.text
 result=finish(client);assert result['stage']=='Completed',result
 current=main.space_cycle.status();assert current['job']['workflow_mode']==mode
 if mode=='manual':
  assert current['stage']=='awaiting_manual_completion' and current['next_action'] is None
  response=client.post('/api/laser-control/space-cycle/action',json={'cycle_id':current['cycle_id'],'revision':current['revision'],'expected_stage':'ready_start','request_id':str(uuid4())})
  assert response.status_code==403 and not calls
  response=client.post('/api/laser-control/space-cycle/reset',json={'cycle_id':current['cycle_id'],'revision':current['revision'],'completed':True,'operator_confirmed_idle':True})
  assert response.status_code==200 and response.json()['stage']=='ready_process'
 else:
  import app.laser_control.space_native as native
  monkeypatch.setattr(native,'prepare_job_panel',lambda *a:{})
  response=client.post('/api/laser-control/space-cycle/action',json={'cycle_id':current['cycle_id'],'revision':current['revision'],'expected_stage':'ready_start','request_id':str(uuid4())})
  assert response.status_code==200,response.text
  assert calls==['start'] and response.json()['stage']=='awaiting_completion'
  current=response.json()
  response=client.post('/api/laser-control/space-cycle/reset',json={'cycle_id':current['cycle_id'],'revision':current['revision'],'completed':True,'operator_confirmed_idle':True})
  assert response.status_code==200 and response.json()['stage']=='ready_process'
  assert calls==['start'],'Reset must not send another machine command'
