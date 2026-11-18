from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from fastapi.testclient import TestClient
from app.laser_control.space_cycle import SpaceCycle,config_fingerprint
from app.laser_control.space_keys import SpaceEdge
from test_laser_control_api import laser_workspace

def claim(cycle,stage):
    s=cycle.status();return cycle.claim(s['cycle_id'],s['revision'],stage,str(uuid4()))

def test_two_steps_never_skip_and_manual_completion_resets(tmp_path):
    c=SpaceCycle(lambda:tmp_path);initial=c.status()
    with pytest.raises(ValueError):claim(c,'ready_frame')
    assert initial['stage']=='ready_process'
    claim(c,'ready_process')
    with pytest.raises(ValueError):c.claim(initial['cycle_id'],initial['revision'],'ready_process',str(uuid4()))
    c.finish('processing','ready_start',job={'path':'one.dxf'});claim(c,'ready_start')
    s=c.finish('start_pending','awaiting_completion')
    with pytest.raises(ValueError):claim(c,'ready_datum')
    with pytest.raises(ValueError):c.reset(s['cycle_id'],s['revision'],True,False)
    result=c.reset(s['cycle_id'],s['revision'],True,True)
    assert result['stage']=='ready_process' and result['cycle_id']!=initial['cycle_id']
    assert result['job'] is None and result['physical_completion_verified'] is False
    events=(tmp_path/'laser_control_state/space-cycle-events.jsonl').read_text()
    assert 'operator_confirmed_cutting_complete' in events

@pytest.mark.parametrize('stage',['datum_pending','processing','ready_frame','frame_pending','ready_start','start_pending','awaiting_completion'])
def test_restart_never_replays_motion_or_start(tmp_path,stage):
    c=SpaceCycle(lambda:tmp_path);s=c.status();c.state['stage']=stage;c._save('test')
    reopened=SpaceCycle(lambda:tmp_path);r=reopened.status()
    assert r['stage']=='interrupted' and r['revision']>s['revision']
    with pytest.raises(ValueError):claim(reopened,'ready_datum')
    assert reopened.reset(r['cycle_id'],r['revision'],False,True)['stage']=='ready_process'

def test_stop_invalidates_pending_and_stale_keypress(tmp_path):
    c=SpaceCycle(lambda:tmp_path);claim(c,'ready_process');s=c.status()
    c.interrupt('Stop requested')
    with pytest.raises(ValueError):c.finish('processing','ready_start')
    with pytest.raises(ValueError):c.claim(s['cycle_id'],s['revision'],'ready_process',str(uuid4()))

def test_persistence_failure_prevents_dispatch(tmp_path,monkeypatch):
    c=SpaceCycle(lambda:tmp_path);c.status()
    monkeypatch.setattr(c,'_save',lambda event:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):claim(c,'ready_process')

def test_key_edge_consumes_matching_up_and_drops_repeat():
    edge=SpaceEdge()
    assert edge.key(True,True)==(True,True)
    assert edge.key(True,True)==(True,False)
    assert edge.key(False,False)==(True,False)
    assert edge.key(True,False)==(False,False)
    assert edge.key(True,True)==(False,False)
    assert edge.key(False,False)==(False,False)
    assert edge.key(True,True)==(True,True)

def test_calibration_change_invalidates_fingerprint(tmp_path):
    a=config_fingerprint(tmp_path,{'workspace':{'width_mm':915}})
    p=tmp_path/'data/calibration/survey.json';p.parent.mkdir(parents=True);p.write_text('new position')
    assert config_fingerprint(tmp_path,{'workspace':{'width_mm':915}})!=a

def test_api_steps_dispatch_once_and_block_job_mutations(laser_workspace,monkeypatch):
    import app.main as main
    import app.laser_control.space_native as native
    client,root,config=laser_workspace
    calls=[]
    monkeypatch.setattr(native,'datum',lambda *a:(calls.append('datum') or {'requested':True}))
    monkeypatch.setattr(native,'prepare_job_panel',lambda *a:{'window_id':1})
    monkeypatch.setattr(main.lasercad_controller,'request',lambda action,**kw:(calls.append((action,kw)) or {'requested':True}))
    s=client.get('/api/laser-control/space-cycle').json()
    def advance(s):return client.post('/api/laser-control/space-cycle/action',json={'cycle_id':s['cycle_id'],'revision':s['revision'],'expected_stage':s['stage'],'request_id':str(uuid4())})
    assert s['stage']=='ready_process' and not calls
    datum_payload={'cycle_id':s['cycle_id'],'revision':s['revision'],'expected_stage':'ready_datum','request_id':str(uuid4())}
    assert client.post('/api/laser-control/space-cycle/action',json=datum_payload).status_code==409
    assert not calls
    claim(main.space_cycle,'ready_process')
    s=main.space_cycle.finish('processing','ready_start',job={'config':config_fingerprint(root,config),'target':'test'})
    for endpoint in ['/api/export','/api/capture','/api/config']:
        assert client.post(endpoint,json={}).status_code==409
    response=advance(s);assert response.status_code==200,response.text
    assert response.json()['stage']=='ready_process' and calls[-1][0]=='start'
    assert response.json()['started_from']['cycle_id']==s['cycle_id']
    assert response.json()['physical_completion_verified'] is False
    assert calls[-1][1]['confirm_start'] is True
    assert advance(s).status_code==409
    completed=client.post('/api/laser-control/space-cycle/reset',json={'cycle_id':response.json()['cycle_id'],'revision':response.json()['revision'],'completed':True,'operator_confirmed_idle':True})
    assert completed.status_code==409
    assert calls[-1][0]=='start' and len(calls)==1

def test_uncertain_dispatch_is_interrupted_not_retried(laser_workspace,monkeypatch):
    import app.main as main
    import app.laser_control.space_native as native
    client,root,config=laser_workspace;calls=[]
    def fail(action,**kw):calls.append(action);raise RuntimeError('delivery unknown')
    monkeypatch.setattr(native,'prepare_job_panel',lambda *a:{'window_id':1})
    monkeypatch.setattr(main.lasercad_controller,'request',fail)
    claim(main.space_cycle,'ready_process')
    s=main.space_cycle.finish('processing','ready_start',job={'config':config_fingerprint(root,config),'target':'test'})
    payload={'cycle_id':s['cycle_id'],'revision':s['revision'],'expected_stage':s['stage'],'request_id':str(uuid4())}
    assert client.post('/api/laser-control/space-cycle/action',json=payload).status_code==409
    assert client.get('/api/laser-control/space-cycle').json()['stage']=='interrupted'
    assert client.post('/api/laser-control/space-cycle/action',json=payload).status_code==409
    assert calls==['start']

def test_no_motion_if_staged_settings_changed(laser_workspace,monkeypatch):
    import app.main as main
    client,root,config=laser_workspace
    claim(main.space_cycle,'ready_process')
    s=main.space_cycle.finish('processing','ready_start',job={'config':'old'})
    calls=[];monkeypatch.setattr(main.lasercad_controller,'request',lambda *a,**kw:calls.append(a))
    r=client.post('/api/laser-control/space-cycle/action',json={'cycle_id':s['cycle_id'],'revision':s['revision'],'expected_stage':s['stage'],'request_id':str(uuid4())})
    assert r.status_code==409 and not calls


from test_automated_workflow import workspace,payload,finish

@pytest.mark.parametrize('download_status',['download_requested','blocked','transfer_unconfirmed'])
def test_first_space_uses_real_detection_nesting_export_and_only_fresh_download(workspace,monkeypatch,download_status):
    import app.main as main
    client,manager,root=workspace
    config=main.load_config();config['lasercad']={'prepare_and_download':{'enabled':True}}
    monkeypatch.setattr(main,'load_config',lambda:config)
    s=main.space_cycle.status()
    handoffs=[]
    def handoff(output):
        handoffs.append(output['path'])
        output['lasercad']={'import_mapping':None,'download_automation':{'status':download_status,'download_requested':download_status=='download_requested','communication_target':'MachName:_AWC_D2713611'}}
    monkeypatch.setattr(main,'_handoff_dxf',handoff)
    monkeypatch.setattr(main.lasercad_controller,'_window',lambda **kw:{'hwnd':1,'pid':2,'title':'LaserCAD - fresh.dxf'})
    request=payload(manager,space_cycle_id=s['cycle_id'],space_revision=s['revision'],request_id=str(uuid4()))
    r=client.post('/api/workflow',json=request);assert r.status_code==202,r.text
    result=finish(client)
    c=main.space_cycle.status()
    if download_status=='download_requested':
        assert result['stage']=='Completed',result
        assert c['stage']=='ready_start' and c['job']['path']==handoffs[-1]
        assert c['job']['config']==config_fingerprint(root,config)
    else:
        assert result['stage']=='Failed' and c['stage']=='interrupted'
    assert len(handoffs)==1
    assert client.post('/api/workflow',json=request).status_code==409


@pytest.mark.parametrize('case',['clean','download','progress','other','changed','wrong_usb'])
def test_native_next_step_checks_document_dialog_and_usb_before_click(tmp_path,monkeypatch,case):
    import hashlib
    import app.laser_control.space_native as native
    path=tmp_path/'job.dxf';path.write_bytes(b'checked drawing')
    window={'hwnd':1,'pid':2,'title':'LaserCAD - job.dxf'}
    closed=[]
    dialog={'hwnd':20,'title':'Download Document'}
    class UI:
        def __init__(self,*a):self.user=SimpleNamespace(IsWindowEnabled=lambda hwnd:True)
        def _dialogs(self):
            if case=='progress':return [dialog,{'hwnd':30,'title':'Dealing...'}]
            if case=='other':return [{'hwnd':40,'title':'Overwrite?'}]
            return [dialog] if case=='download' else []
        def controls(self,d):return [{'control_id':cid} for cid in [1384,1015,1504,1779]]
        def _one(self,*a):return {'hwnd':1384}
        def _post(self,*a):closed.append(a)
        def _closed(self,*a):pass
        def _main(self):pass
    monkeypatch.setattr(native,'NativeExportUI',UI)
    controller=SimpleNamespace(job=path,_window=lambda **kw:window,status=lambda **kw:{'available':True,'job_matches':True,'window_id':1,'process_id':2,'window_title':window['title'],'communication_selection':['wrong' if case=='wrong_usb' else 'MachName:test']})
    job={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mapping':None,'window':dict(window)}
    if case=='changed':path.write_bytes(b'changed')
    if case in {'progress','other','changed','wrong_usb'}:
        with pytest.raises(Exception):native.prepare_job_panel(controller,job,'MachName:test')
        assert not closed
    else:
        assert native.prepare_job_panel(controller,job,'MachName:test')['window_id']==1
        assert len(closed)==(1 if case=='download' else 0)


@pytest.mark.parametrize('code',['Space'])
def test_native_dispatch_carries_the_pressed_key_without_sending_commands(code):
    from app.laser_control.space_keys import DesktopSpaceKeys
    scripts=[]
    bridge=None
    def evaluate(script):
        scripts.append(script)
        bridge.stop.set()
    bridge=DesktopSpaceKeys(SimpleNamespace(evaluate_js=evaluate),None)
    bridge.queue.put_nowait(code)
    bridge._dispatch()
    assert scripts==['window.SpaceCycle && window.SpaceCycle.nativePress('+repr(code)+')']
    assert bridge.active is False


@pytest.mark.parametrize('legacy_stage',['ready_datum'])
def test_idle_legacy_record_migrates_without_datum_and_invalidates_old_snapshot(tmp_path,legacy_stage):
    path=tmp_path/'laser_control_state/space-cycle.json';path.parent.mkdir(parents=True)
    old={'cycle_id':str(uuid4()),'revision':4,'stage':legacy_stage,'job':None,'message':'Old Datum instruction'}
    path.write_text(json.dumps(old),encoding='utf-8')
    c=SpaceCycle(lambda:tmp_path);current=c.status()
    assert current['stage']=='ready_process' and current['next_action']=='Refresh camera and capture through Download Document'
    assert current['sequence']=='space-start-tab-v2' and current['cycle_id']!=old['cycle_id']
    assert not current['message']
    with pytest.raises(ValueError):c.claim(old['cycle_id'],old['revision'],'ready_process',str(uuid4()))
    assert SpaceCycle(lambda:tmp_path).status()==current


def test_fresh_idle_cycle_remains_ready_on_restart_and_stop_sends_no_action(tmp_path):
    c=SpaceCycle(lambda:tmp_path);initial=c.status()
    assert SpaceCycle(lambda:tmp_path).status()==initial
    c.interrupt('Stop requested')
    assert c.status()==initial


def test_native_completion_status_is_disabled_without_a_completion_stage_callback():
    from app.laser_control.space_keys import DesktopSpaceKeys
    bridge=DesktopSpaceKeys(None,None)
    assert set(bridge.edges)=={0x20}
    assert not bridge.completion_key_ready()


def test_legacy_after_datum_state_requires_idle_confirmation_not_assumed_homing_completion(tmp_path):
    path=tmp_path/'laser_control_state/space-cycle.json';path.parent.mkdir(parents=True)
    old={'cycle_id':str(uuid4()),'revision':4,'stage':'ready_process','job':None,'last_request':{'requested':True},'message':'Datum requested'}
    path.write_text(json.dumps(old),encoding='utf-8')
    c=SpaceCycle(lambda:tmp_path);state=c.status()
    assert state['stage']=='interrupted'
    assert c.reset(state['cycle_id'],state['revision'],False,True)['stage']=='ready_process'


def test_native_enter_is_available_only_while_awaiting_completion_and_not_already_dispatching():
    from app.laser_control.space_keys import DesktopSpaceKeys
    stage=['ready_process']
    bridge=DesktopSpaceKeys(None,None,completion_ready=lambda:stage[0]=='awaiting_completion')
    for value in ['ready_process','processing','ready_frame','ready_start','start_pending','interrupted']:
        stage[0]=value;assert not bridge.completion_key_ready()
    stage[0]='awaiting_completion';assert bridge.completion_key_ready()
    bridge.active=True;assert not bridge.completion_key_ready()


def test_native_enter_callback_failure_does_not_enable_the_key():
    from app.laser_control.space_keys import DesktopSpaceKeys
    def fail():raise RuntimeError('stage unavailable')
    assert not DesktopSpaceKeys(None,None,completion_ready=fail).completion_key_ready()


def test_idle_previous_three_space_sequence_migrates_but_downloaded_job_does_not(tmp_path):
    p=tmp_path/'laser_control_state/space-cycle.json';p.parent.mkdir(parents=True)
    old={'cycle_id':str(uuid4()),'revision':4,'stage':'ready_process','job':None,'sequence':'space-only-v1'}
    p.write_text(json.dumps(old),encoding='utf-8')
    c=SpaceCycle(lambda:tmp_path);s=c.status()
    assert s['stage']=='ready_process' and s['sequence']=='space-start-tab-v2' and s['cycle_id']!=old['cycle_id']
    assert SpaceCycle(lambda:tmp_path).status()==s
    old.update(stage='ready_frame',job={'path':'old.dxf'})
    p.write_text(json.dumps(old),encoding='utf-8')
    assert SpaceCycle(lambda:tmp_path).status()['stage']=='interrupted'


def test_old_run_box_stage_is_rejected_by_action_endpoint(laser_workspace,monkeypatch):
    import app.main as main
    client,root,config=laser_workspace
    calls=[];monkeypatch.setattr(main.lasercad_controller,'request',lambda *a,**kw:calls.append(a))
    s=main.space_cycle.status()
    response=client.post('/api/laser-control/space-cycle/action',json={'cycle_id':s['cycle_id'],'revision':s['revision'],'expected_stage':'ready_frame','request_id':str(uuid4())})
    assert response.status_code==409 and not calls



def test_native_held_start_space_cannot_also_complete_or_capture_without_release():
    edge=SpaceEdge()
    assert edge.key(True,True)==(True,True)  # Start's deliberate physical press
    assert edge.key(True,True)==(True,False) # still held after Start response
    assert edge.key(True,True)==(True,False)
    assert edge.key(False,True)==(True,False)
    assert edge.key(True,True)==(True,True)  # completion's separate physical press
    assert edge.key(True,True)==(True,False) # cannot reuse it for the next capture
    assert edge.key(False,True)==(True,False)
    assert edge.key(True,True)==(True,True)  # next cycle requires another press
