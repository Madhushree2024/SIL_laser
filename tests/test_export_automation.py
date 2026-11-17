from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import hashlib,json,sqlite3
import ezdxf
import pytest
from app.laser_control.lasercad_export_automation import ExportAutomation
from test_laser_control_api import laser_workspace

READY={'status':'launch_requested','preview':{'status':'refresh_requested','ready':True}}

def drawing(path,layer='CUT'):
    doc=ezdxf.new('R2010');doc.units=4;doc.layers.add(layer,color=1)
    doc.modelspace().add_lwpolyline([(0,0),(100,0),(100,50),(0,50)],close=True,dxfattribs={'layer':layer})
    doc.saveas(path)

@pytest.fixture
def setup(tmp_path):
    path=tmp_path/'job.dxf';drawing(path)
    config={'workspace':{'width_mm':900,'height_mm':680},'lasercad':{'prepare_and_download':{
        'enabled':True,'run_box_after_success':False,'communication_mode':'USB','usb_port':'COM4','transfer_timeout_seconds':30,'speed_mm_s':40,'power_percent':50,'work_mode':'Cut'}}}
    controller=SimpleNamespace(job=path,lock=Lock(),_window=lambda **k:{'hwnd':1,'pid':2,'title':'LaserCAD - job.dxf'})
    calls=[];fail={'stage':None}
    class UI:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def call(self,stage,*args):
            calls.append((stage,*args))
            if fail['stage']==stage:raise RuntimeError('Unconfirmed '+stage)
        def select_communication(self,ip):self.call('communication',ip)
        def verify_speed_units(self):self.call('units')
        def configure_cut(self,speed,power):self.call('parameters',speed,power)
        def verify(self,ip,speed,power):self.call('verify',ip,speed,power)
        def open_download(self):self.call('download')
        def download_document(self):
            with sqlite3.connect(tmp_path/'laser_control_state/export_automation.sqlite3') as db:
                saved=json.loads(db.execute('SELECT document FROM attempts WHERE path=?',(str(controller.job.resolve()),)).fetchone()[0])
                assert saved['download_may_have_been_requested'] is True
                assert saved['stage']=='Submitting Download Document'
            self.call('download_document')
        def run_box_after_success(self,transfer,target,guard):
            guard();assert transfer['status']=='completed';self.call('frame',target)
            return {'status':'requested','requested':True,'physical_motion_verified':False}
        def wait_transfer_result(self,timeout):
            self.call('wait_transfer',timeout)
            return {'status':fail.get('result','completed'),'reason':'test transfer result','dialog_text':'Communicating successful!'}
    factory=lambda *a:UI()
    automation=ExportAutomation(controller,lambda:tmp_path,lambda:config,factory)
    return automation,path,controller,config,calls,fail,factory

def test_sequence_verifies_before_single_final_download_and_never_claims_machine_ack(setup):
    automation,path,controller,config,calls,fail,factory=setup
    result=automation.run(path,READY)
    assert result['status']=='download_requested'
    assert calls==[('communication','COM4'),('units',),('parameters',40,50),('verify','COM4',40,50),('download',),('download_document',)]
    assert result['controller_acknowledged'] is False and result['download_requested'] is True
    assert result['transfer_completed'] is False and result['lasercad_reported_success'] is False
    assert result['completion_verification']=='not_requested'
    assert not any(c[0] in {'start','origin','datum','frame'} for c in calls)

@pytest.mark.parametrize('stage',['communication','units','parameters','verify','download','download_document'])
def test_failure_stops_sequence_and_survives_restart_without_automatic_retry(setup,stage):
    automation,path,controller,config,calls,fail,factory=setup
    fail['stage']=stage
    result=automation.run(path,READY)
    assert result['status']==('transfer_unconfirmed' if stage=='download_document' else 'blocked')
    assert result['transfer_completed'] is False
    assert calls[-1][0]==stage
    count=len(calls)
    replacement=ExportAutomation(controller,automation.root_getter,lambda:config,factory)
    assert replacement.run(path,READY)['status']=='already_attempted'
    assert len(calls)==count
    if stage=='download_document':assert result['download_may_have_been_requested'] is True

def test_successful_same_export_is_not_downloaded_twice_and_next_export_has_fresh_state(setup):
    automation,path,controller,config,calls,fail,factory=setup
    assert automation.run(path,READY)['status']=='download_requested'
    assert automation.run(path,READY)['status']=='already_attempted'
    assert sum(c[0]=='download_document' for c in calls)==1
    next_path=path.with_name('next.dxf');drawing(next_path);controller.job=next_path
    fail['stage']='parameters'
    result=automation.run(next_path,READY)
    assert result['status']=='blocked'
    assert result['download_may_have_been_requested'] is False

@pytest.mark.parametrize('handoff',[{}, {'status':'warning'}, {'status':'launch_requested'}, {'status':'launch_requested','preview':{'status':'refresh_requested','ready':False}}])
def test_unconfirmed_import_never_touches_native_controls(setup,handoff):
    automation,path,controller,config,calls,fail,factory=setup
    assert automation.run(path,handoff)['status']=='blocked' and calls==[]

@pytest.mark.parametrize('change',['invalid_usb_port','speed','power','mode','disabled','changed_job','invalid_dxf','reference_layer','controller_busy'])
def test_invalid_inputs_and_wrong_geometry_never_touch_native_controls(setup,change):
    automation,path,controller,config,calls,fail,factory=setup
    options=config['lasercad']['prepare_and_download']
    if change=='invalid_usb_port':options['usb_port']='COM0'
    if change=='speed':options['speed_mm_s']=0
    if change=='power':options['power_percent']=101
    if change=='mode':options['work_mode']='Engrave'
    if change=='disabled':options['enabled']=False
    if change=='changed_job':controller.job=path.with_name('other.dxf')
    if change=='invalid_dxf':path.write_text('broken')
    if change=='reference_layer':drawing(path,'SHEATH')
    if change=='controller_busy':controller.lock.acquire()
    try:
        result=automation.run(path,READY)
        assert result['status']==('disabled' if change=='disabled' else 'blocked')
        assert calls==[]
    finally:
        if change=='controller_busy':controller.lock.release()

def test_export_hook_runs_new_sequence_and_skips_run_box(laser_workspace,monkeypatch):
    import app.main as main
    client,root,config=laser_workspace
    config['lasercad']={'prepare_and_download':{'enabled':True},'auto_frame_after_dxf':True}
    path=root/'data/exports/test.dxf';path.parent.mkdir(parents=True);drawing(path)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *a:dict(READY))
    calls=[]
    monkeypatch.setattr(main,'export_automation',SimpleNamespace(run=lambda p,h:calls.append(p) or {'status':'download_requested','message':'Requested'}))
    monkeypatch.setattr(main,'_request_export_boundary_frame',lambda *a:pytest.fail('This sequence must not Run Box'))
    output={'path':str(path)};main._handoff_dxf(output)
    assert calls==[str(path)]
    assert output['lasercad']['download_automation']['status']=='download_requested'

def test_native_presentation_does_not_wait_on_export_mutation_lock(laser_workspace,monkeypatch):
    import app.main as main
    client,root,config=laser_workspace
    monkeypatch.setattr(main.laser_control_service,'host_callback',lambda payload:{'status':'hosted'})
    main.operation_lock.acquire()
    try:
        result=client.post('/api/laser-control/lasercad-host',json={'action':'show','width':1000,'height':800,'viewport_width':1000,'viewport_height':836})
        assert result.status_code==200,result.text
    finally:main.operation_lock.release()


@pytest.mark.parametrize('outcome',['completed','failed','unconfirmed'])
def test_submission_is_saved_without_observing_legacy_transfer_results(setup,outcome):
    automation,path,controller,config,calls,fail,factory=setup
    fail['result']=outcome
    result=automation.run(path,READY)
    assert result['status']=='download_requested' and result['download_requested']
    assert not result['transfer_completed'] and not result['lasercad_reported_success']
    assert result['completion_verification']=='not_requested'
    assert calls[-1]==('download_document',)
    assert not any(c[0]=='wait_transfer' for c in calls)
    with sqlite3.connect(automation.root_getter()/'laser_control_state/export_automation.sqlite3') as db:
        record=json.loads(db.execute('SELECT document FROM attempts').fetchone()[0])
    assert record['status']=='download_requested' and not record['transfer_completed']
    count=len(calls);assert automation.run(path,READY)['status']=='already_attempted'
    assert len(calls)==count


def test_changed_import_copy_blocks_before_any_native_preparation(setup,tmp_path):
    automation,path,controller,config,calls,fail,factory=setup
    copy=tmp_path/'import.dxf';copy.write_bytes(path.read_bytes())
    mapping={'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'import_path':str(copy),'import_sha256':hashlib.sha256(copy.read_bytes()).hexdigest()}
    copy.write_bytes(copy.read_bytes()+b'changed')
    result=automation.run(path,{**READY,'import_mapping':mapping})
    assert result['status']=='blocked' and not calls
    assert 'import DXF changed' in result['message']


def test_import_changed_while_download_dialog_open_never_submits_document(setup,tmp_path):
    automation,path,controller,config,calls,fail,factory=setup
    copy=tmp_path/'import.dxf';copy.write_bytes(path.read_bytes())
    mapping={'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'import_path':str(copy),'import_sha256':hashlib.sha256(copy.read_bytes()).hexdigest()}
    def changed_ui(*args):
        ui=factory(*args);original=ui.open_download
        def open_and_change():
            original();copy.write_bytes(copy.read_bytes()+b'changed')
        ui.open_download=open_and_change
        return ui
    automation.ui_factory=changed_ui
    result=automation.run(path,{**READY,'import_mapping':mapping})
    assert result['transfer_completed'] is False
    assert not any(c[0]=='download_document' for c in calls)
    assert 'import DXF changed' in result['message']


def test_legacy_run_box_setting_never_triggers_post_download_actions(setup):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download']['run_box_after_success']=True
    result=automation.run(path,READY)
    assert result['status']=='download_requested' and not result['transfer_completed']
    assert not result['run_box']['requested']
    assert calls[-1]==('download_document',)
    assert not any(c[0] in {'wait_transfer','frame','start','origin','datum'} for c in calls)
    count=len(calls)
    replacement=ExportAutomation(controller,automation.root_getter,lambda:config,factory)
    assert replacement.run(path,READY)['status']=='already_attempted'
    assert len(calls)==count


def test_unused_timeout_and_run_box_preferences_cannot_block_submission(setup):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download'].update(transfer_timeout_seconds='obsolete',run_box_after_success='obsolete')
    assert automation.run(path,READY)['status']=='download_requested'
    assert calls[-1]==('download_document',)


def test_machine_positioning_preflight_is_preserved_without_post_download_cycle(setup):
    automation,path,controller,config,calls,fail,factory=setup
    config['machine_workflow']={'enabled':True}
    preflight=[]
    automation.machine_workflow=SimpleNamespace(
        pre_transfer=lambda *a:preflight.append(a),
        after_transfer=lambda *a:pytest.fail('No post-download machine cycle'))
    assert automation.run(path,READY)['status']=='download_requested'
    assert calls[-1]==('download_document',)
    assert len(preflight)==1


def test_copy_changed_after_submission_does_not_cause_post_click_checks(setup,tmp_path):
    automation,path,controller,config,calls,fail,factory=setup
    copy=tmp_path/'import.dxf';copy.write_bytes(path.read_bytes())
    mapping={'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'import_path':str(copy),'import_sha256':hashlib.sha256(copy.read_bytes()).hexdigest()}
    def ui_factory(*args):
        ui=factory(*args);submit=ui.download_document
        def clicked():
            submit();copy.write_bytes(copy.read_bytes()+b'changed after submission')
        ui.download_document=clicked
        ui.wait_transfer_result=lambda *a:pytest.fail('No transfer result query')
        ui.acknowledge_success=lambda *a:pytest.fail('No post-download dialog click')
        ui.run_box_after_success=lambda *a:pytest.fail('No Run Box')
        return ui
    automation.ui_factory=ui_factory
    result=automation.run(path,{**READY,'import_mapping':mapping})
    assert result['status']=='download_requested' and not result['transfer_completed']
    assert calls[-1]==('download_document',)
