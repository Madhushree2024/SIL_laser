from uuid import uuid4
import pytest
import app.main as main
from test_laser_control_api import laser_workspace, upload


def ready_panel(monkeypatch, job):
    path=main.laser_control_service.manager().path(job)
    panel={'available':True,'job_matches':True,'job':str(path),
           'window_title':'LaserCAD V8.28.3 - job.dxf','window_id':123,'communication_selection':['MachName:COM4'],
           'actions':{name:True for name in ['download','frame','stop','start','pause']}}
    monkeypatch.setattr(main.lasercad_controller,'status',lambda **kwargs:panel)
    calls=[]
    monkeypatch.setattr(main.lasercad_controller,'request',lambda action,**kwargs:calls.append(action) or {'requested':True,'message':'Panel click delivered; execution unverified.'})
    return panel,calls


@pytest.mark.parametrize('action',['download','frame'])
def test_matching_job_enables_one_guarded_lasercad_request(laser_workspace,monkeypatch,action):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    controls=client.get('/api/laser-control/job-controls').json()
    assert controls['matching_job'] and controls['actions'][action]
    assert controls['actions']['start'] and controls['actions']['resume']
    request={'action':action,'job_id':job['id'],'request_id':str(uuid4())}
    response=client.post('/api/laser-control/fallback-command',json=request)
    assert response.status_code==200,response.text
    assert response.json()['controller_acknowledged'] is False
    assert calls==[action]
    assert client.post('/api/laser-control/fallback-command',json=request).status_code==409
    assert calls==[action]
    stored=client.get('/api/laser-control/jobs/'+job['id']).json()
    assert stored['started_at'] is None and stored['completed_at'] is None


def test_another_job_or_changed_file_cannot_receive_download(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    panel['job']=str(root/'different.dxf')
    controls=client.get('/api/laser-control/job-controls').json()
    assert not controls['actions']['download'] and controls['actions']['stop']
    result=client.post('/api/laser-control/fallback-command',json={'action':'download','job_id':job['id'],'request_id':str(uuid4())})
    assert result.status_code==409 and not calls


def test_stop_bypasses_workflow_and_preparation_lock(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    monkeypatch.setattr(main,'WORKFLOW',{'busy':True})
    assert main.operation_lock.acquire(blocking=False)
    try:
        result=client.post('/api/laser-control/fallback-stop',json={'action':'stop','job_id':job['id'],'request_id':str(uuid4())})
        assert result.status_code==200,result.text
    finally:
        main.operation_lock.release()
    assert calls==['stop']


@pytest.mark.parametrize('action',['start','pause','resume'])
def test_unverified_cutting_actions_cannot_use_fallback_route(laser_workspace,monkeypatch,action):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    response=client.post('/api/laser-control/fallback-command',json={'action':action,'job_id':job['id'],'request_id':str(uuid4())})
    assert response.status_code==409 and not calls


def test_job_revalidation_keeps_immutable_file_and_records_event(laser_workspace):
    client,root,config=laser_workspace;job=upload(client)
    response=client.post('/api/laser-control/jobs/'+job['id']+'/validate',json={})
    assert response.status_code==200 and response.json()['validation']['valid']
    assert response.json()['sha256']==job['sha256']
    events=client.get('/api/laser-control/jobs/'+job['id']+'/events').json()['events']
    assert events[-1]['kind']=='revalidated'


def test_export_notification_status_does_not_wait_for_lasercad_panel(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    upload(client)
    monkeypatch.setattr(main.lasercad_controller,'status',lambda:pytest.fail('Fast job status must not inspect the LaserCAD window'))
    assert client.get('/api/laser-control/status').status_code==200


def test_history_failure_after_panel_request_does_not_claim_no_request_was_sent(laser_workspace,monkeypatch):
    import sqlite3
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    def fail_save(*args,**kwargs): raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(main.laser_control_service.history(),'save',fail_save)
    response=client.post('/api/laser-control/fallback-command',json={'action':'download','job_id':job['id'],'request_id':str(uuid4())})
    assert response.status_code==503 and 'unconfirmed' in response.json()['detail']
    assert calls==['download']


@pytest.mark.parametrize('action,native,observed',[('start','start',None),('pause','pause','running'),('resume','pause','paused')])
def test_confirmed_actions_click_once_without_claiming_execution(laser_workspace,monkeypatch,action,native,observed):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    payload={'action':action,'job_id':job['id'],'request_id':str(uuid4()),'operator_confirmed':True,
             'revision':job['revision'],'dxf_sha256':job['sha256'],'window_title':panel['window_title'],
             'communication_selection':panel['communication_selection'],'observed_state':observed}
    response=client.post('/api/laser-control/fallback-command',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['controller_acknowledged'] is False
    assert calls==[native]
    assert client.post('/api/laser-control/fallback-command',json=payload).status_code==409
    assert calls==[native]
    stored=client.get('/api/laser-control/jobs/'+job['id']).json()
    assert stored['started_at'] is None and stored['completed_at'] is None
    events=client.get('/api/laser-control/jobs/'+job['id']+'/events').json()['events']
    event=next(e for e in events if e['kind']=='lasercad_job_request')
    assert event['detail']['action']==action
    assert event['detail']['operator_observed_state']==observed


@pytest.mark.parametrize('change',[
    {'operator_confirmed':False},{'revision':999},{'dxf_sha256':'wrong'},
    {'window_title':'Another drawing'},{'communication_selection':['different machine']},
    {'observed_state':'paused'}])
def test_changed_confirmation_rejects_before_click(laser_workspace,monkeypatch,change):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    payload={'action':'pause','job_id':job['id'],'request_id':str(uuid4()),'operator_confirmed':True,
             'revision':job['revision'],'dxf_sha256':job['sha256'],'window_title':panel['window_title'],
             'communication_selection':panel['communication_selection'],'observed_state':'running',**change}
    response=client.post('/api/laser-control/fallback-command',json=payload)
    assert response.status_code==409,response.text
    assert calls==[]


def test_desktop_layout_only_when_supported_and_matching(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job=upload(client)
    panel,calls=ready_panel(monkeypatch,job)
    monkeypatch.setattr(main.laser_control_service,'layout_callback',None)
    assert client.post('/api/laser-control/layout',json={'action':'split'}).status_code==409
    layouts=[]
    monkeypatch.setattr(main.laser_control_service,'layout_callback',lambda action: layouts.append(action) or {'status':action})
    assert client.get('/api/laser-control/status').json()['desktop_layout_supported']
    assert client.post('/api/laser-control/layout',json={'action':'split'}).status_code==200
    panel['job_matches']=False
    assert client.post('/api/laser-control/layout',json={'action':'split'}).status_code==409
    assert client.post('/api/laser-control/layout',json={'action':'restore'}).status_code==200
    assert layouts==['split','restore'] and not calls


@pytest.mark.parametrize('saved,expected',[({},True),({'controller_ip':'192.168.8.8'},True),({'split_after_dxf':False},False)])
def test_upgrade_split_default_preserves_explicit_opt_out(laser_workspace,monkeypatch,saved,expected):
    from test_laser_control_api import drawing
    client,root,config=laser_workspace
    config['laser_control']=saved
    path=root/'data/exports/upgrade.dxf';path.parent.mkdir(parents=True);path.write_bytes(drawing())
    layouts=[]
    monkeypatch.setattr(main.laser_control_service,'layout',lambda action: layouts.append(action) or {'status':'split'})
    result={'path':str(path)}
    main._handoff_dxf(result)
    assert layouts==(['split'] if expected else [])
    assert client.get('/api/laser-control/status').json()['split_after_dxf'] is expected


def test_layout_failure_is_visible_in_dashboard_status(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    monkeypatch.setattr(main.laser_control_service,'layout_callback',None)
    assert client.post('/api/laser-control/layout',json={'action':'split'}).status_code==409
    status=client.get('/api/laser-control/status').json()['desktop_layout_status']
    assert status['status']=='unavailable' and 'desktop' in status['message']
