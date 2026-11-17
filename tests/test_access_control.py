from pathlib import Path
from types import SimpleNamespace
import json
import time
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.security.accounts import AccountStore, AccessError, password_matches
from app.security.access import COOKIE, required_permission
from test_multi_sheath import workspace

pytestmark=pytest.mark.access_control
PASSWORD='Test password for roles 2026'

@pytest.fixture
def access(tmp_path,monkeypatch):
    store=AccountStore(tmp_path/'security/accounts.sqlite3')
    monkeypatch.setitem(main.app.state._state,'account_store',lambda:store)
    monkeypatch.setattr(main,'ROOT',tmp_path)
    monkeypatch.setattr(main,'WORKFLOW',{'id':None,'busy':False,'stage':'Ready'})
    client=TestClient(main.app,base_url='http://127.0.0.1')
    return client,store,tmp_path

def setup(client):
    response=client.post('/api/auth/setup',json={'username':'owner','password':PASSWORD})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response.json()

def login(client,name,password=PASSWORD):
    client.cookies.clear();client.headers.pop('X-CSRF-Token',None)
    response=client.post('/api/auth/login',json={'username':name,'password':password})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=response.json()['csrf_token']
    return response.json()

def test_first_run_requires_admin_setup_and_blocks_data_and_processing(access,monkeypatch):
    client,store,root=access
    monkeypatch.setattr(main,'_capture_raw',lambda:pytest.fail('Unauthenticated capture'))
    assert client.get('/api/auth/me').json()['setup_required']
    for path in ['/api/templates','/api/state','/api/workflow','/api/projects','/data/production/missing.csv']:
        assert client.get(path).status_code==401
    assert client.post('/api/workflow',json={}).status_code==401
    assert client.get('/api/health').status_code==200
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert not store.configured()

def test_setup_hashes_password_and_sets_private_expiring_cookie(access):
    client,store,_=access
    response=client.post('/api/auth/setup',json={'username':'owner','password':PASSWORD})
    assert response.status_code==200
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=43200' in cookie
    with store.connect() as db:
        row=db.execute('SELECT password_hash FROM users').fetchone()
        token=db.execute('SELECT token_hash FROM sessions').fetchone()[0]
    assert PASSWORD not in row[0] and password_matches(PASSWORD,row[0])
    assert token!=client.cookies.get(COOKIE)
    assert client.get('/api/auth/me').json()['user']['role']=='admin'
    assert client.post('/api/auth/setup',json={'username':'second','password':PASSWORD}).status_code==409

def test_setup_rejects_weak_password_and_cross_origin_requests(access):
    client,store,_=access
    assert client.post('/api/auth/setup',json={'username':'owner','password':'short'}).status_code==400
    assert client.post('/api/auth/setup',json={'username':'owner','password':PASSWORD},headers={'Origin':'https://unrelated.example'}).status_code==403
    assert not store.configured()
    assert client.get('/api/auth/me',headers={'Host':'unrelated.example'}).status_code==400

def test_admin_can_manage_accounts_but_editor_cannot_escalate(access):
    client,store,_=access;setup(client)
    added=client.post('/api/auth/users',json={'username':'operator','password':PASSWORD,'role':'editor'})
    assert added.status_code==200
    assert client.post('/api/auth/users',json={'username':'OPERATOR','password':PASSWORD,'role':'viewer'}).status_code==409
    assert 'password_hash' not in client.get('/api/auth/users').text
    login(client,'operator')
    for path,method in [('/api/auth/users','get'),('/api/auth/audit','get'),('/api/config','post'),('/api/calibration/scale','post'),('/api/laser/alignment','post'),('/api/camera/control','post')]:
        assert getattr(client,method)(path).status_code==403,path
    assert client.post('/api/auth/users',json={'username':'hacker','password':PASSWORD,'role':'admin'}).status_code==403

@pytest.mark.parametrize('path', ['/api/workflow','/api/capture','/api/upload','/api/detect','/api/nest','/api/export','/api/templates','/api/config','/api/camera/open','/api/camera/control','/api/production/retry','/api/production/recover','/api/project/save','/api/calibration/camera','/api/laser/alignment'])
def test_readonly_cannot_mutate_any_workflow_or_settings(access,path):
    client,store,_=access;store.create('reader',PASSWORD,'viewer')
    login(client,'reader')
    response=client.post(path,json={})
    assert response.status_code==403,(path,response.text)
    assert client.delete('/api/templates/example').status_code==403


def test_readonly_can_view_and_download_saved_production(access,monkeypatch):
    from test_daily_production import fixture_record
    from app.export.daily_production import record_daily_production
    client,store,root=access
    record_daily_production(root/'data/production',**fixture_record(root/'fixture'))
    store.create('reader',PASSWORD,'viewer');login(client,'reader')
    reports=client.get('/api/projects')
    assert reports.status_code==200
    assert client.get(reports.json()['production_reports'][0]['excel_url']).status_code==200
    assert client.get('/api/workflow').status_code==200


def test_editor_can_execute_existing_operation_and_template_save(access,monkeypatch):
    client,store,root=access
    store.create('operator',PASSWORD,'editor');login(client,'operator')
    calls=[]
    monkeypatch.setattr(main,'_capture_raw',lambda: calls.append('capture') or (root,root/'image.jpg'))
    monkeypatch.setattr(main,'_write_corrected_preview',lambda p:p)
    assert client.post('/api/capture').status_code==200
    assert calls==['capture']
    with store.connect() as db:
        event=db.execute("SELECT * FROM audit WHERE action='POST' AND resource='/api/capture'").fetchone()
    assert event['username']=='operator' and event['outcome']=='200'


def test_csrf_missing_wrong_and_cross_origin_requests_are_rejected(access):
    client,_,_=access;setup(client)
    token=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/capture').status_code==403
    assert client.post('/api/capture',headers={'X-CSRF-Token':'wrong'}).status_code==403
    client.headers['X-CSRF-Token']=token
    assert client.post('/api/capture',headers={'Origin':'http://127.0.0.1:9999'}).status_code==403
    assert client.post('/api/auth/logout',headers={'Origin':'http://127.0.0.1'}).status_code==200
    assert client.get('/api/projects').status_code==401


def test_sessions_expire_and_logout_revokes_access(access):
    client,store,_=access;setup(client)
    with store.connect(write=True) as db:db.execute('UPDATE sessions SET expires_at=?',(time.time()-1,))
    assert client.get('/api/projects').status_code==401
    login(client,'owner');assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/auth/me').json()['user'] is None


def test_last_admin_cannot_be_disabled_or_demoted(access):
    client,store,_=access;account=setup(client)['user']
    for role,enabled in [('viewer',True),('admin',False)]:
        response=client.patch('/api/auth/users/'+account['id'],json={'role':role,'enabled':enabled})
        assert response.status_code==409
    assert store.users()[0]['enabled']


def test_role_changes_and_password_resets_revoke_existing_sessions(access):
    client,store,_=access;owner=setup(client)['user']
    operator=store.create('operator',PASSWORD,'editor')
    token,_=store.login('operator',PASSWORD)
    assert store.session(token)
    assert client.patch('/api/auth/users/'+operator['id'],json={'role':'viewer','enabled':True}).status_code==200
    assert store.session(token) is None
    token,_=store.login('operator',PASSWORD)
    assert client.post('/api/auth/users/'+operator['id']+'/password',json={'password':'Another test password 2026'}).status_code==200
    assert store.session(token) is None
    with pytest.raises(AccessError):store.login('operator',PASSWORD)


def test_password_change_requires_current_password_and_revokes_all_sessions(access):
    client,store,_=access;setup(client)
    assert client.post('/api/auth/password',json={'current_password':'incorrect','password':'Another test password 2026'}).status_code==403
    assert client.post('/api/auth/password',json={'current_password':PASSWORD,'password':'Another test password 2026'}).status_code==200
    assert client.get('/api/projects').status_code==401
    login(client,'owner','Another test password 2026')


def test_login_rate_limit_persists_and_does_not_disclose_usernames(access):
    client,store,_=access;setup(client);client.cookies.clear()
    messages=[]
    for _ in range(10):
        response=client.post('/api/auth/login',json={'username':'missing-user','password':'incorrect'})
        assert response.status_code==401
        messages.append(response.json()['detail'])
    assert len(set(messages))==1
    assert client.post('/api/auth/login',json={'username':'missing-user','password':'incorrect'}).status_code==429
    assert client.post('/api/auth/login',json={'username':'owner','password':PASSWORD}).status_code==200


def test_unknown_future_write_route_requires_admin():
    assert required_permission('POST','/api/future-dangerous-operation')=='users'


def test_account_database_is_outside_downloadable_data(access):
    client,store,root=access;setup(client)
    assert store.path==root/'security/accounts.sqlite3'
    assert client.get('/data/../security/accounts.sqlite3').status_code in {401,404}
    assert client.get('/data/%2e%2e/security/accounts.sqlite3').status_code==404

def test_editor_runs_the_original_capture_to_export_workflow(workspace,monkeypatch):
    from test_daily_production import request
    from test_multi_sheath import wait
    client,manager,root,corrections=workspace
    client.base_url='http://127.0.0.1'
    store=AccountStore(root/'security/accounts.sqlite3')
    monkeypatch.setitem(main.app.state._state,'account_store',lambda:store)
    store.create('operator',PASSWORD,'editor')
    login(client,'operator')
    response=client.post('/api/workflow',json=request(manager))
    assert response.status_code==202,response.text
    finished=wait(client)
    assert finished['stage']=='Completed',finished
    assert len(corrections)==1
    assert Path(finished['result']['export']['path']).is_file()
    events=store.events()
    assert any(event['username']=='operator' and event['resource']=='/api/workflow' and event['workflow_id']==finished['id'] for event in events)

def test_sign_in_is_available_while_processing_lock_is_held(access):
    client,store,_=access;store.create('operator',PASSWORD,'editor')
    assert main.operation_lock.acquire(blocking=False)
    try:
        response=client.post('/api/auth/login',json={'username':'operator','password':PASSWORD})
        assert response.status_code==200,response.text
    finally:main.operation_lock.release()


def test_camera_stream_stops_when_its_session_is_revoked(access,monkeypatch):
    client,store,_=access;store.create('reader',PASSWORD,'viewer');login(client,'reader')
    def stream(**kw):
        yield b'first-frame'
        with store.connect(write=True) as db:db.execute('DELETE FROM sessions')
        time.sleep(1.1)
        yield b'frame-after-revocation'
    monkeypatch.setattr(main,'camera_manager',SimpleNamespace(mjpeg_stream=stream))
    response=client.get('/api/camera/stream')
    assert response.status_code==200
    assert b'frame-after-revocation' not in response.content

def test_non_ascii_forged_csrf_header_is_rejected_without_crashing(access):
    client,_,_=access;setup(client)
    response=client.post('/api/capture',headers=[(b'X-CSRF-Token',b'\xff')])
    assert response.status_code==403

def test_editor_cannot_change_camera_parameters_through_direct_api(access,monkeypatch):
    client,store,_=access;store.create('operator',PASSWORD,'editor');login(client,'operator')
    monkeypatch.setattr(main,'camera_manager',SimpleNamespace(status=lambda:{'connected':True,'width':1280,'height':720,'fps':30}))
    monkeypatch.setattr(main,'load_config',lambda:{'camera':{'resolution':{'width':1280,'height':720},'fps':30}})
    assert client.post('/api/camera/open',json={'index':0,'width':640,'height':480,'fps':15}).status_code==403
    assert client.post('/api/camera/open',json={'index':0}).status_code==403
    monkeypatch.setattr(main,'_visible_cameras',lambda values:[{'index':0,'name':'USB Camera'}])
    main.camera_manager.enumerate_cameras=lambda **kw:[]
    calls=[]
    main.camera_manager.open=lambda *args:calls.append(args) or {'connected':True}
    assert client.post('/api/camera/open',json={'index':0,'width':1280,'height':720,'fps':30}).status_code==200
    assert calls==[(0,1280,720,30)]


def test_admin_deletes_user_revokes_sessions_and_keeps_history_and_production(access):
    client,store,root=access;setup(client)
    user=store.create('operator',PASSWORD,'editor')
    token,_=store.login('operator',PASSWORD)
    store.audit('operator','export','preserved.svg')
    production=root/'data/production/example.csv';production.parent.mkdir(parents=True)
    production.write_text('saved production data')
    response=client.delete('/api/auth/users/'+user['id'])
    assert response.status_code==200,response.text
    assert response.json()=={'ok':True}
    assert user['id'] not in {u['id'] for u in client.get('/api/auth/users').json()['users']}
    assert store.session(token) is None
    with pytest.raises(AccessError):store.login('operator',PASSWORD)
    assert production.read_text()=='saved production data'
    events=store.events()
    assert any(e['username']=='operator' and e['action']=='export' for e in events)
    assert any(e['username']=='owner' and e['action']=='delete user' and e['resource']=='operator' for e in events)
    assert client.delete('/api/auth/users/'+user['id']).status_code==404
    assert client.get('/api/auth/me').json()['user']['username']=='owner'


@pytest.mark.parametrize('role',['editor','viewer'])
def test_non_admin_cannot_delete_accounts(access,role):
    client,store,_=access;owner=setup(client)['user']
    store.create('operator',PASSWORD,role);login(client,'operator')
    assert client.delete('/api/auth/users/'+owner['id']).status_code==403
    assert len(store.users())==2


def test_delete_requires_login_and_csrf(access):
    client,store,_=access;setup(client)
    user=store.create('reader',PASSWORD,'viewer')
    client.headers.pop('X-CSRF-Token')
    assert client.delete('/api/auth/users/'+user['id']).status_code==403
    client.cookies.clear()
    assert client.delete('/api/auth/users/'+user['id']).status_code==401
    assert len(store.users())==2


def test_cannot_delete_self_or_last_active_admin(access):
    client,store,_=access;owner=setup(client)['user']
    response=client.delete('/api/auth/users/'+owner['id'])
    assert response.status_code==409 and 'active administrator' in response.json()['detail']
    second=store.create('second-admin',PASSWORD,'admin')
    response=client.delete('/api/auth/users/'+owner['id'])
    assert response.status_code==409 and 'signed-in account' in response.json()['detail']
    # The store also protects the invariant, independent of route authorization.
    store.update(second['id'],'admin',False,'owner')
    with pytest.raises(AccessError,match='active administrator'):
        store.delete(owner['id'],second['id'],'second-admin')
    assert len(store.users())==2


def test_admin_can_delete_another_admin_without_reopening_setup(access):
    client,store,_=access;setup(client)
    other=store.create('second-admin',PASSWORD,'admin')
    assert client.delete('/api/auth/users/'+other['id']).status_code==200
    assert not client.get('/api/auth/me').json()['setup_required']
    assert client.delete('/api/auth/users/not-a-valid-user').status_code==404


def test_login_handles_account_deleted_during_password_verification(access,monkeypatch):
    import app.security.accounts as accounts
    client,store,_=access;owner=setup(client)['user']
    user=store.create('operator',PASSWORD,'editor')
    original=accounts.password_matches
    def verify_then_delete(password,saved):
        result=original(password,saved)
        store.delete(user['id'],owner['id'],'owner')
        return result
    monkeypatch.setattr(accounts,'password_matches',verify_then_delete)
    response=client.post('/api/auth/login',json={'username':'operator','password':PASSWORD})
    assert response.status_code==401
    assert 'Account changed' in response.json()['detail']
