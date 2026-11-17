from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest
import ezdxf
from fastapi.testclient import TestClient
import app.main as main
from app.laser_control.machine_workflow import MachineWorkflow, REQUIRED, BLOCKER
from app.laser.lasercad_control import ControlError
from app.export.lasercad_bed_mapping import prepare_bed_copy
from test_lasercad_bed_mapping import layout


class Clock:
    def __init__(self):self.value=0
    def __call__(self):self.value+=.2;return self.value


class Feedback:
    commissioned=True
    capabilities=REQUIRED
    def __init__(self,clock):
        self.clock=clock;self.calls=[];self.seq=0;self.stale=False;self.bad=None;self.reject=None;self.freeze=None
        self.s={'device':'MachName:_AWC_D2713611','connected':True,'safety_ready':True,'alarm':False,
                'homed':False,'idle':True,'moving':False,'home_epoch':'epoch','position_mm':[10,10],
                'origin_mm':[0,0],'anchor':'top_right','origin_mode':'key_origin'}
    def read_state(self):
        self.seq+=1;r={**deepcopy(self.s),'sequence':self.seq,'observed_at':self.clock()- (3 if self.stale else 0)}
        if self.bad:r.update(self.bad)
        return r
    def command(self,action,request_id,**parameters):
        self.calls.append(action)
        if action==self.reject:raise ControlError('Uncertain command acceptance')
        if action=='home':self.s.update(homed=True,home_epoch='new-epoch')
        if action=='move_absolute':self.s['position_mm']=parameters['position_mm']
        if action=='set_origin':self.s['origin_mm']=parameters['position_mm']
        if action!=self.freeze:self.s['completed_command_id']=request_id
        if action=='start':self.s.update(idle=False,moving=True)
        return {'accepted':True,'request_id':request_id}


@pytest.fixture
def cycle(layout):
    exe,path,config=layout;root=path.parents[4]
    config['machine_workflow']={'enabled':True,'park_x_mm':0,'park_y_mm':0}
    config['lasercad']['prepare_and_download']={'enabled':True,'communication_mode':'USB','usb_port':'MachName:_AWC_D2713611','run_box_after_success':True}
    clock=Clock();backend=Feedback(clock)
    runner=MachineWorkflow(lambda:root,lambda:config,backend,clock,lambda _:None)
    photo=root/'data/photo.jpg';photo.write_bytes(b'fake photo fixture only')
    mapping=prepare_bed_copy(path,config,exe)
    backend.s['job_sha256']=mapping['import_sha256']
    return runner,backend,path,mapping,photo,config


def capture(cycle):
    runner,backend,path,mapping,photo,config=cycle
    runner.prepare_capture();runner.bind_capture(photo)


TRANSFER={'status':'completed','confirmation_source':'lasercad_download_dialog','dialog_id':13}


def test_real_production_adapter_is_absent_and_no_commands_or_state_created(cycle):
    runner,backend,path,mapping,photo,config=cycle
    production=MachineWorkflow(runner.root_getter,runner.config_getter)
    assert not production.status()['automatic_start_available']
    assert production.preview()['no_commands_sent']
    with pytest.raises(ControlError,match='not commissioned'):production.prepare_capture()
    assert not (path.parents[4]/'laser_control_state/machine_workflow.sqlite3').exists()
    assert backend.calls==[]


def test_correct_order_home_park_download_move_origin_frame_then_start(cycle):
    runner,backend,path,mapping,photo,config=cycle
    capture(cycle)
    assert backend.calls==['home','move_absolute']
    assert runner.status()['cycle']['stage']=='captured'
    runner.pre_transfer(path,mapping,photo)
    assert backend.calls==['home','move_absolute']
    result=runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls==['home','move_absolute','move_absolute','set_origin','frame','start']
    assert backend.s['position_mm']==pytest.approx([150,140])
    assert backend.s['origin_mm']==pytest.approx([150,140])
    assert result['status']=='start_requested'
    with pytest.raises(ControlError):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls.count('start')==1
    with pytest.raises(ControlError,match='idle'):runner.prepare_capture()


def test_optional_frame_disabled_but_origin_still_verified(cycle):
    runner,backend,path,mapping,photo,config=cycle
    config['lasercad']['prepare_and_download']['run_box_after_success']=False
    capture(cycle);runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls==['home','move_absolute','move_absolute','set_origin','start']


@pytest.mark.parametrize('bad',[{'safety_ready':False},{'alarm':True},{'connected':False},{'device':'MachName:OTHER'},{'idle':False},{'moving':True}])
def test_no_homing_on_bad_controller_feedback(cycle,bad):
    runner,backend,*_=cycle;backend.bad=bad
    with pytest.raises(ControlError):runner.prepare_capture()
    assert backend.calls==[]


def test_stale_feedback_does_not_allow_homing(cycle):
    runner,backend,*_=cycle;backend.stale=True
    with pytest.raises(ControlError,match='stale'):runner.prepare_capture()
    assert backend.calls==[]


@pytest.mark.parametrize('action',['home','move_absolute'])
def test_uncertain_initial_command_has_no_retry_or_capture(cycle,action):
    runner,backend,path,mapping,photo,config=cycle;backend.reject=action
    with pytest.raises(ControlError):runner.prepare_capture()
    before=list(backend.calls)
    with pytest.raises(ControlError):runner.prepare_capture()
    assert backend.calls==before
    replacement=MachineWorkflow(runner.root_getter,runner.config_getter,backend,runner.clock,lambda _:None)
    with pytest.raises(ControlError,match='unresolved'):replacement.prepare_capture()
    assert backend.calls==before


@pytest.mark.parametrize('result',['failed','unconfirmed'])
def test_failed_download_never_moves_to_job(cycle,result):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle)
    with pytest.raises(ControlError,match='successful'):runner.after_transfer(path,mapping,photo,{**TRANSFER,'status':result})
    assert backend.calls==['home','move_absolute']


@pytest.mark.parametrize('action',['move_absolute','set_origin','frame','start'])
def test_post_download_uncertain_command_stops_with_no_retry(cycle,action):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle);backend.reject=action
    with pytest.raises(ControlError):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls[-1]==action
    before=list(backend.calls)
    with pytest.raises(ControlError):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls==before


def test_runbox_completion_is_required_before_start(cycle):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle);backend.freeze='frame'
    with pytest.raises(ControlError,match='timeout'):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls[-1]=='frame' and 'start' not in backend.calls


@pytest.mark.parametrize('bad',[{'origin_mode':'current_position'},{'anchor':'bottom_left'},{'job_sha256':'old-job'},{'homed':False},{'home_epoch':'reset'}])
def test_mismatched_job_origin_mode_or_reset_prevents_post_download_motion(cycle,bad):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle);backend.bad=bad
    with pytest.raises(ControlError):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls==['home','move_absolute']


def test_actual_origin_readback_is_required(cycle):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle)
    original=backend.command
    def command(action,**kwargs):
        result=original(action,**kwargs)
        if action=='set_origin':backend.s['origin_mm']=[0,0]
        return result
    backend.command=command
    with pytest.raises(ControlError,match='readback'):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls[-1]=='set_origin'


def test_absolute_copy_cannot_be_combined_with_key_origin(cycle):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle)
    with pytest.raises(ControlError,match='offset twice'):runner.pre_transfer(path,{**mapping,'positioning_mode':'absolute_measured'},photo)


@pytest.mark.parametrize('change',['photo','settings','drawing','cancel'])
def test_changed_inputs_or_stop_invalidate_the_cycle(cycle,change):
    runner,backend,path,mapping,photo,config=cycle;capture(cycle)
    if change=='photo':photo.write_bytes(b'changed')
    if change=='settings':config['machine_workflow']['park_x_mm']=1
    if change=='drawing':path.write_bytes(path.read_bytes()+b'\n')
    if change=='cancel':runner.cancel()
    with pytest.raises((ControlError,ValueError)):runner.after_transfer(path,mapping,photo,TRANSFER)
    assert backend.calls==['home','move_absolute']


def test_relative_import_keeps_local_zero_without_machine_offset_twice(cycle):
    runner,backend,path,mapping,photo,config=cycle
    assert mapping['positioning_mode']=='key_origin_relative'
    assert mapping['machine_job_origin_mm']==pytest.approx([150,140])
    assert mapping['lasercad_page_reference_mm']==[0,0]
    assert mapping['import_dxf_reference_mm']==pytest.approx([950,650])
    old=list(ezdxf.readfile(path).modelspace());new=list(ezdxf.readfile(mapping['import_path']).modelspace())
    for a,b in zip(old,new):
        for p,q in zip(a.get_points(),b.get_points()):assert q[:2]==pytest.approx([p[0]+950,p[1]+650])


def test_readonly_api_never_dispatches_machine_commands(cycle,monkeypatch):
    runner,backend,*_=cycle
    monkeypatch.setattr(main,'machine_workflow',runner)
    response=TestClient(main.app).get('/api/laser-control/machine-workflow')
    assert response.status_code==200 and response.json()['no_commands_sent']
    assert backend.calls==[]


def test_capture_blocks_before_camera_without_adapter(cycle,monkeypatch):
    runner,backend,path,mapping,photo,config=cycle
    monkeypatch.setattr(main,'machine_workflow',MachineWorkflow(runner.root_getter,runner.config_getter))
    monkeypatch.setattr(main,'load_config',lambda:config)
    monkeypatch.setattr(main.camera_manager,'save_frame',lambda *args:pytest.fail('Camera must not capture before confirmed parking'))
    response=TestClient(main.app).post('/api/capture')
    assert response.status_code==400 and 'not commissioned' in response.json()['detail']


def test_motion_blocks_download_before_any_lasercad_ui_calls(setup):
    from test_export_automation import READY
    automation,path,controller,config,calls,fail,factory=setup
    config['machine_workflow']={'enabled':True}
    result=automation.run(path,READY)
    assert result['status']=='blocked' and calls==[]


# Share the existing deterministic Download Document harness, never a live window.
from test_export_automation import setup

def test_uncommissioned_job_cannot_open_or_reopen_relative_drawing(cycle,monkeypatch):
    runner,backend,path,mapping,photo,config=cycle
    monkeypatch.setattr(main,'machine_workflow',MachineWorkflow(runner.root_getter,runner.config_getter))
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *a:pytest.fail('Must not open uncommissioned job-relative drawing'))
    with pytest.raises(ControlError,match='not commissioned'):main._open_lasercad_job(path,config)


def test_relative_normalization_cannot_hide_physical_out_of_bed_position(cycle):
    runner,backend,path,mapping,photo,config=cycle
    sidecar=path.with_suffix('.alignment.json');data=json.loads(sidecar.read_text())
    data['design_reference_mm']=[-1000,-600];data['matrix']=[[1,0,1000],[0,1,600],[0,0,1]]
    sidecar.write_text(json.dumps(data))
    exe=path.parents[4]/'LaserCAD/AWCPrograms/LaserCAD.exe'
    # Reuse the fixture executable rather than dispatching anything to a window.
    from app.export.lasercad_bed_mapping import _workspace
    import test_lasercad_bed_mapping
    candidates=list(path.parents[4].rglob('LaserCAD.exe'))
    assert len(candidates)==1
    with pytest.raises(ValueError,match='outside'):prepare_bed_copy(path,config,candidates[0])


def test_download_dispatches_ack_then_machine_cycle_without_legacy_frame(setup):
    from test_export_automation import READY
    automation,path,controller,config,calls,fail,factory=setup
    config['machine_workflow']={'enabled':True};config['lasercad']['prepare_and_download']['run_box_after_success']=True
    class Motion:
        def pre_transfer(self,*args):calls.append(('motion_preflight',))
        def after_transfer(self,*args):
            calls.append(('verified_machine_sequence',))
            return {'status':'start_requested','message':'Simulated Start accepted once.'}
    automation.machine_workflow=Motion()
    original=automation.ui_factory
    def ui(*args):
        result=original(*args)
        result.acknowledge_success=lambda *a:calls.append(('ack_success',))
        return result
    automation.ui_factory=ui
    assert automation.run(path,READY)['status']=='machine_start_requested'
    assert calls[-2:]==[('ack_success',),('verified_machine_sequence',)]
    assert not any(c[0]=='frame' for c in calls)
    before=list(calls)
    assert automation.run(path,READY)['status']=='already_attempted'
    assert sum(c[0]=='verified_machine_sequence' for c in calls)==1
