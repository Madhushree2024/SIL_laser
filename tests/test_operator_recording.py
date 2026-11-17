from pathlib import Path
import hashlib
import io
import json
import zipfile
from threading import Event
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.laser_control.operator_recording import OperatorRecorder
from app.laser.lasercad_control import LaserCADController
import app.main as main

@pytest.fixture
def recording(tmp_path):
    source=tmp_path/'data/projects/test/exports/job.dxf'
    source.parent.mkdir(parents=True);source.write_bytes(b'original DXF')
    profile=tmp_path/'data/calibration/machine_placement.json'
    profile.parent.mkdir(parents=True);profile.write_text('{"survey":"actual"}')
    camera=tmp_path/'camera_calibration.npz';camera.write_bytes(b'camera profile')
    observed=Event()
    def observer():
        observed.set()
        return {'available':True,'displayed_position_unverified':{'x':'12.3','y':'45.6'},
                'controller_telemetry_verified':False}
    context=lambda:{'settings':{'workspace':{'width_mm':950}},'job':{'path':str(source)}}
    recorder=OperatorRecorder(lambda:tmp_path,context,observer,interval=60)
    yield recorder,source,profile,camera,observed
    recorder.shutdown()
    if recorder.worker:recorder.worker.join(timeout=2)

def test_recording_copies_evidence_and_keeps_manual_reports_distinct(recording):
    recorder,source,profile,camera,observed=recording
    before=[p.read_bytes() for p in (source,profile,camera)]
    session=recorder.start('operator','test session','machine_zero','actual firmware')
    assert observed.wait(2)
    recorder.sample(session['id'])
    recorder.event(session['id'],'operator','datum_pressed','Pressed manually',x=0,y=0)
    recorder.event(session['id'],'operator','homing_observed_complete','Observed by operator')
    recorder.stop('operator',session_id=session['id'])
    assert [p.read_bytes() for p in (source,profile,camera)]==before
    folder=Path(session['folder'])
    rows=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    sample=next(x for x in rows if x['kind']=='visible_ui_sample')
    assert sample['observation']['controller_telemetry_verified'] is False
    manual=next(x for x in rows if x.get('action')=='homing_observed_complete')
    assert manual['source']=='operator_reported'
    assert all(x['timestamp_utc'].endswith('+00:00') for x in rows)
    contexts=list((folder/'contexts').glob('*.json'))
    assert contexts
    assert any(x.name.endswith('job.dxf') for x in (folder/'artifacts').iterdir())
    assert recorder.status()['active'] is None

def test_archive_contains_valid_hashes_and_no_unrelated_files(recording):
    recorder,source,profile,camera,_=recording
    private=source.parents[4]/'security/accounts.sqlite3'
    private.parent.mkdir(exist_ok=True);private.write_text('must not collect')
    session=recorder.start('operator')
    recorder.stop('operator')
    with zipfile.ZipFile(io.BytesIO(recorder.download(session['id']))) as z:
        assert z.testzip() is None
        manifest=json.loads(z.read('sha256.json'))
        for name,digest in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
        assert not any('accounts' in name for name in z.namelist())
        assert 'session.json' in z.namelist() and 'events.jsonl' in z.namelist()

def test_active_recording_and_stale_event_guards(recording):
    recorder,*_=recording
    session=recorder.start('operator')
    with pytest.raises(ValueError,match='already active'):recorder.start('other')
    with pytest.raises(ValueError):recorder.event(str(uuid4()),'operator','note')
    with pytest.raises(ValueError):recorder.event(session['id'],'operator','send_start')
    with pytest.raises(ValueError):recorder.stop('operator',session_id=str(uuid4()))
    assert recorder.status()['active']==session['id']
    recorder.stop('operator')
    with pytest.raises(ValueError):recorder.event(session['id'],'operator','note')
    with pytest.raises(ValueError):recorder.download('../../outside')

def test_restarted_app_marks_unresolved_recording_interrupted(tmp_path):
    folder=tmp_path/'logs/operator_sessions'/str(uuid4());folder.mkdir(parents=True)
    (folder/'session.json').write_text(json.dumps({'id':folder.name,'state':'recording','started_at':'2026-11-14'}))
    recorder=OperatorRecorder(lambda:tmp_path,lambda:{},lambda:{})
    status=recorder.status()
    assert status['active'] is None and status['sessions'][0]['state']=='interrupted'

def test_passive_lasercad_snapshot_never_requests_a_machine_action():
    from types import SimpleNamespace
    controller=LaserCADController(lambda:{})
    window={'hwnd':1,'title':'LaserCAD - job.dxf','pid':55,'executable':'LaserCAD.exe'}
    controller._window=lambda **kwargs:window
    controller.request=lambda *a,**kw:pytest.fail('Recorder must not send machine requests')
    controls=[{'control_id':1,'kind':'Button','text':'Start','enabled':True},
              {'control_id':2,'kind':'Edit','text':'private unrelated edit','enabled':True},
              {'control_id':3,'kind':'Static','text':'Working','enabled':True}]
    controller._coordinates=lambda controls:{'x':{'text':'123'}}
    controller._api=lambda:SimpleNamespace(controls=lambda window:controls,
        user=SimpleNamespace(IsWindowEnabled=lambda hwnd:True))
    result=controller.observation_snapshot()
    assert result['machine_commands_sent'] is False
    assert result['controller_telemetry_verified'] is False
    assert result['displayed_position_unverified']=={'x':'123'}
    assert not any(x['kind']=='Edit' for x in result['visible_text'])

@pytest.fixture
def recorder_api(recording,monkeypatch):
    recorder,source,profile,camera,_=recording
    # Router references this original object; isolate its observers and disk paths.
    target=main.operator_recorder
    for name in ('root_getter','context_getter','observer','interval','active','worker','recovered'):
        monkeypatch.setattr(target,name,getattr(recorder,name))
    monkeypatch.setattr(main.lasercad_controller,'request',lambda *a,**kw:pytest.fail('No machine requests'))
    yield TestClient(main.app),target
    target.shutdown()
    if target.worker:target.worker.join(timeout=2)

def test_authenticated_api_records_while_processing_without_mutating_workflow(recorder_api,monkeypatch):
    client,recorder=recorder_api
    original=main.WORKFLOW.copy()
    monkeypatch.setitem(main.WORKFLOW,'busy',True)
    acquired=main.operation_lock.acquire(blocking=False)
    assert acquired
    try:
        response=client.post('/api/laser-control/recordings',json={'origin_mode':'key_origin'})
        assert response.status_code==200,response.text
        session=response.json()['id']
        response=client.post('/api/laser-control/recordings/'+session+'/events',
                             json={'action':'start_pressed','notes':'Operator pressed manually'})
        assert response.status_code==200 and response.json()['machine_command_sent'] is False
        assert main.WORKFLOW['busy'] is True
        response=client.post('/api/laser-control/recordings/stop',json={'session_id':session})
        assert response.status_code==200
    finally:main.operation_lock.release()
    response=client.get('/api/laser-control/recordings/'+session+'/download')
    assert response.status_code==200 and response.headers['content-type']=='application/zip'
    client.cookies.clear()
    assert client.get('/api/laser-control/recordings').status_code==401

def test_recording_api_rejects_invalid_input_and_paths(recorder_api):
    client,recorder=recorder_api
    assert client.get('/api/laser-control/recordings/not-a-uuid/download').status_code==422
    assert client.post('/api/laser-control/recordings',json={'origin_mode':'invented'}).status_code==422
    session=client.post('/api/laser-control/recordings',json={}).json()['id']
    assert client.post('/api/laser-control/recordings/'+session+'/events',json={'action':'execute_start'}).status_code==422
    assert client.post('/api/laser-control/recordings/'+session+'/events',json={'action':'note','x':'NaN'}).status_code==422
