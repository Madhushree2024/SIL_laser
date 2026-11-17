from uuid import uuid4
import pytest
import app.main as main
from app.laser_control.supervised_cycle import SupervisedCycles
from app.laser_control.cutting_parameters import CuttingParameters
from test_automated_workflow import workspace, payload, finish
from test_laser_control_api import laser_workspace, upload
from test_laser_job_controls import ready_panel


def prepared(client):
    job=upload(client)
    cycles=main.laser_control_service.cycles();cycle_id=str(uuid4())
    cycles.begin(cycle_id, main.laser_control_service.cycle_session)
    cycles.prepared(cycle_id,job)
    return job,cycle_id


def review_payload(job,cycle_id,panel):
    return {'cycle_id':cycle_id,'operator_confirmed':True,'revision':job['revision'],
            'dxf_sha256':job['sha256'],'window_title':panel['window_title'],
            'communication_selection':panel['communication_selection']}


def start_payload(job,panel):
    return {'action':'start','job_id':job['id'],'request_id':str(uuid4()),'operator_confirmed':True,
            'revision':job['revision'],'dxf_sha256':job['sha256'],'window_title':panel['window_title'],
            'communication_selection':panel['communication_selection']}


def test_loaded_sheath_runs_real_preparation_and_waits_without_firing(workspace):
    client,manager,root=workspace
    body=payload(manager,supervised=True,operator_loaded=True,request_id=str(uuid4()))
    assert client.post('/api/workflow',json=body).status_code==202
    result=finish(client)
    assert result['stage']=='Completed',result
    cycle=client.get('/api/laser-control/status').json()['supervised']['active']
    assert cycle['state']=='awaiting_review'
    assert cycle['job_id']==result['result']['export']['laser_control']['job_id']
    assert cycle['start_requested_at'] is None
    assert client.post('/api/workflow',json=body).status_code in {400,409}
    assert client.post('/api/capture',json={}).status_code==409


@pytest.mark.parametrize('changes',[{'operator_loaded':False},{'export':{'format':'svg'}}])
def test_load_confirmation_and_dxf_are_required(workspace,changes):
    client,manager,root=workspace
    body=payload(manager,**{'supervised':True,'operator_loaded':True,**changes})
    result=client.post('/api/workflow',json=body)
    assert result.status_code==400
    assert main.laser_control_service.cycles().status()['active'] is None


def test_full_supervised_cut_needs_review_start_and_observed_completion(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    start=start_payload(job,panel)
    assert client.post('/api/laser-control/fallback-command',json=start).status_code==409
    assert not calls
    review=review_payload(job,cycle_id,panel)
    assert client.post('/api/laser-control/cycle/review',json={**review,'operator_confirmed':False}).status_code==409
    assert client.post('/api/laser-control/cycle/review',json=review).status_code==200
    start['request_id']=str(uuid4())
    response=client.post('/api/laser-control/fallback-command',json=start)
    assert response.status_code==200,response.text
    assert calls==['start']
    active=main.laser_control_service.cycles().status()['active']
    assert active['state']=='awaiting_completion' and active['controller_acknowledged'] is False
    assert client.post('/api/laser-control/fallback-command',json={**start,'request_id':str(uuid4())}).status_code==409
    assert client.post('/api/capture',json={}).status_code==409
    assert client.post('/api/config',json=config).status_code==409
    complete={'cycle_id':cycle_id,'outcome':'completed','operator_confirmed':True,'machine_stopped':True}
    assert client.post('/api/laser-control/cycle/confirm',json={**complete,'machine_stopped':False}).status_code==409
    assert client.post('/api/laser-control/cycle/confirm',json={**complete,'cycle_id':str(uuid4())}).status_code==409
    result=client.post('/api/laser-control/cycle/confirm',json=complete)
    assert result.status_code==200,result.text
    assert result.json()['completion_basis']=='operator_reported'
    assert main.laser_control_service.cycles().status()['active'] is None
    stored=main.laser_control_service.history().get(job['id'])
    assert stored['started_at'] is None and stored['completed_at'] is None
    assert calls==['start']
    assert client.post('/api/laser-control/cycle/confirm',json=complete).status_code==409


def test_changed_revision_invalidates_review_and_legacy_start_cannot_bypass(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    assert client.post('/api/laser-control/cycle/review',json=review_payload(job,cycle_id,panel)).status_code==200
    native={'action':'start','request_id':str(uuid4()),'confirm_start':True}
    assert client.post('/api/laser/control/command',json=native).status_code==409
    job=main.laser_control_service.update_parameters(job['id'],CuttingParameters(**job['parameters']),job['revision'])
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==409
    assert not calls


def test_restart_and_unknown_start_never_resend_and_require_resolution(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    assert client.post('/api/laser-control/cycle/review',json=review_payload(job,cycle_id,panel)).status_code==200
    def uncertain(*args,**kwargs):
        calls.append('attempted')
        raise OSError('Native delivery timeout; outcome unknown')
    monkeypatch.setattr(main.lasercad_controller,'request',uncertain)
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==409
    reopened=SupervisedCycles(main.laser_control_service.history(),'new-process-session')
    assert reopened.status()['active']['state']=='start_pending'
    with pytest.raises(ValueError):reopened.require_idle()
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==409
    assert calls==['attempted']
    assert client.post('/api/laser-control/cycle/confirm',json={'cycle_id':cycle_id,'outcome':'aborted','operator_confirmed':True,'machine_stopped':True}).status_code==200
    assert calls==['attempted']


def test_stop_interrupts_and_requires_observed_stop_before_next_cycle(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    assert client.post('/api/laser-control/cycle/review',json=review_payload(job,cycle_id,panel)).status_code==200
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==200
    assert client.post('/api/laser-control/fallback-stop',json={'action':'stop','job_id':job['id'],'request_id':str(uuid4())}).status_code==200
    assert calls==['start','stop']
    assert main.laser_control_service.cycles().status()['active']['state']=='interrupted'
    body={'cycle_id':cycle_id,'outcome':'completed','operator_confirmed':True,'machine_stopped':True}
    assert client.post('/api/laser-control/cycle/confirm',json=body).status_code==409
    assert client.post('/api/laser-control/cycle/confirm',json={**body,'outcome':'aborted'}).status_code==200


def test_restarting_during_preparation_requires_operator_recovery(laser_workspace):
    client,root,config=laser_workspace
    cycles=main.laser_control_service.cycles();cycle_id=str(uuid4())
    cycles.begin(cycle_id,main.laser_control_service.cycle_session)
    recovered=SupervisedCycles(main.laser_control_service.history(),'another-session')
    assert recovered.status()['active']['state']=='interrupted'
    with pytest.raises(ValueError):recovered.begin(str(uuid4()),'another-session')
    recovered.finish(cycle_id,'aborted','operator')
    assert recovered.status()['active'] is None


def test_priority_stop_dispatches_before_unavailable_cycle_storage(laser_workspace,monkeypatch):
    import sqlite3
    client,root,config=laser_workspace
    calls=[]
    monkeypatch.setattr(main.lasercad_controller,'request',lambda action,**kw:calls.append(action) or {'requested':True,'message':'Stop click delivered; physical result unverified.'})
    def unavailable():raise sqlite3.OperationalError('test unavailable ledger')
    monkeypatch.setattr(main.laser_control_service,'cycles',unavailable)
    response=client.post('/api/laser-control/fallback-stop',json={'action':'stop','request_id':str(uuid4())})
    assert response.status_code==200,response.text
    assert calls==['stop'] and response.json()['controller_acknowledged'] is False


def test_download_invalidates_cycle_review(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    assert client.post('/api/laser-control/cycle/review',json=review_payload(job,cycle_id,panel)).status_code==200
    assert client.post('/api/laser-control/fallback-command',json={'action':'download','job_id':job['id'],'request_id':str(uuid4())}).status_code==200
    assert main.laser_control_service.cycles().status()['active']['state']=='awaiting_review'
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==409
    assert calls==['download']


def test_uncertain_stop_requires_aborted_resolution(laser_workspace,monkeypatch):
    client,root,config=laser_workspace;job,cycle_id=prepared(client)
    panel,calls=ready_panel(monkeypatch,job)
    assert client.post('/api/laser-control/cycle/review',json=review_payload(job,cycle_id,panel)).status_code==200
    assert client.post('/api/laser-control/fallback-command',json=start_payload(job,panel)).status_code==200
    def uncertain(*args,**kwargs):raise OSError('Stop timeout; delivery unconfirmed')
    monkeypatch.setattr(main.lasercad_controller,'request',uncertain)
    response=client.post('/api/laser-control/fallback-stop',json={'action':'stop','job_id':job['id'],'request_id':str(uuid4())})
    assert response.status_code==409
    active=main.laser_control_service.cycles().status()['active']
    assert active['state']=='interrupted' and 'unconfirmed' in active['error']
