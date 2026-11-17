from uuid import uuid4
from copy import deepcopy
import pytest
from test_laser_control_api import laser_workspace


def payload(**changes):return {'request_id':str(uuid4()),'operator_confirmed_idle':True,**changes}

@pytest.mark.parametrize('stage',['ready_process','interrupted'])
def test_explicit_datum_requests_home_once_without_advancing_or_resetting_cycle(laser_workspace,monkeypatch,stage):
 import app.main as main
 import app.laser_control.space_native as native
 client,root,config=laser_workspace
 main.space_cycle.status()
 if stage=='interrupted':main.space_cycle.state.update(stage=stage);main.space_cycle._save('test')
 before=deepcopy(main.space_cycle.status());data=(root/'laser_control_state/space-cycle.json').read_bytes()
 calls=[]
 def datum(controller,options,guard,**kwargs):guard();calls.append('datum');return {'requested':True}
 monkeypatch.setattr(native,'datum',datum)
 p=payload();r=client.post('/api/laser-control/manual-datum',json=p)
 assert r.status_code==200,r.text
 assert calls==['datum'] and main.space_cycle.status()==before
 assert (root/'laser_control_state/space-cycle.json').read_bytes()==data
 assert client.post('/api/laser-control/manual-datum',json=p).status_code==409 and calls==['datum']

@pytest.mark.parametrize('stage',['processing','ready_frame','frame_pending','ready_start','start_pending','awaiting_completion'])
def test_manual_datum_cannot_interrupt_an_active_workflow(laser_workspace,monkeypatch,stage):
 import app.main as main
 import app.laser_control.space_native as native
 client,root,config=laser_workspace
 main.space_cycle.status();main.space_cycle.state['stage']=stage
 monkeypatch.setattr(native,'datum',lambda *a:pytest.fail('Datum sent during active cycle'))
 assert client.post('/api/laser-control/manual-datum',json=payload()).status_code==409
 assert main.space_cycle.status()['stage']==stage


def test_manual_datum_requires_idle_confirmation_and_operate_permission(laser_workspace,monkeypatch):
 import app.main as main
 import app.laser_control.space_native as native
 from app.security.access import required_permission
 client,root,config=laser_workspace
 monkeypatch.setattr(native,'datum',lambda *a:pytest.fail('Unconfirmed Datum'))
 assert client.post('/api/laser-control/manual-datum',json=payload(operator_confirmed_idle=False)).status_code==409
 assert required_permission('POST','/api/laser-control/manual-datum')=='operate'


def test_stop_cancels_manual_datum_before_dispatch(laser_workspace,monkeypatch):
 import app.main as main
 import app.laser_control.space_native as native
 client,root,config=laser_workspace
 def datum(controller,options,guard,**kwargs):
  main.manual_datum_cancel.set();guard();pytest.fail('Click after Stop')
 monkeypatch.setattr(native,'datum',datum)
 assert client.post('/api/laser-control/manual-datum',json=payload()).status_code==409
 assert main.space_cycle.status()['stage']=='ready_process'


@pytest.mark.parametrize('problem',['none','missing_usb','wrong_usb','expired','stop'])
def test_background_datum_selects_usb_before_click_and_hides_on_every_exit(monkeypatch,problem):
 import app.laser_control.space_native as native
 from app.laser.lasercad_control import ControlError
 events=[];window={'hwnd':1,'pid':2,'executable':'LaserCAD.exe','title':'LaserCAD'}
 target='MachName:_AWC_D2713611'
 monkeypatch.setattr(native,'ensure_window',lambda c:events.append('ensure') or window)
 class UI:
  def __init__(self,*a):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def select_usb(self,value,*,allow_mode_dialog):
   assert value==target and allow_mode_dialog is False;events.append('usb')
   if problem=='missing_usb':raise ControlError('USB entry missing')
  def _main(self):events.append('ready')
 monkeypatch.setattr(native,'DatumUI',UI)
 panel={'available':True,'window_id':1,'process_id':2,'window_title':'LaserCAD','communication_selection':['wrong' if problem=='wrong_usb' else target]}
 class Controller:
  def status(self,**kw):events.append('status');return panel
  def request(self,action,**kw):assert action=='datum';assert kw['expected_panel']['communication_selection']==[target];events.append('datum');return {'requested':True}
 def host(payload):
  events.append(payload['action'])
  return {'status':'hidden' if payload['action']=='hide' or problem=='expired' and payload['action']=='status' else 'background'}
 def guard():
  if problem=='stop' and 'usb' in events:raise ValueError('Stopped')
 if problem=='none':
  result=native.datum(Controller(),{'communication_mode':'USB','usb_port':target},guard,host=host)
  assert result['background'] is True and result['communication_target']==target
  assert events.index('background')<events.index('usb')<events.index('datum')
 else:
  with pytest.raises((ControlError,ValueError)):native.datum(Controller(),{'usb_port':target},guard,host=host)
  assert 'datum' not in events
 assert events[-1]=='hide'


def test_background_datum_requires_desktop_host_before_launching(monkeypatch):
 import app.laser_control.space_native as native
 from app.laser.lasercad_control import ControlError
 monkeypatch.setattr(native,'ensure_window',lambda c:pytest.fail('No desktop host: must not launch'))
 with pytest.raises(ControlError,match='desktop'):native.datum(None,{})


def test_background_usb_selection_never_opens_select_mode_when_device_missing():
 from app.laser_control.lasercad_export_ui import NativeExportUI
 from app.laser.lasercad_control import ControlError
 ui=object.__new__(NativeExportUI);ui.window={};ui._main=lambda:None;ui._ip_combo=lambda:{};ui._combo_values=lambda c:['COM4']
 ui._click=lambda *a:pytest.fail('No visible communication dialog in background Datum')
 with pytest.raises(ControlError,match='Configure it'):ui.select_usb('MachName:_AWC_D2713611',allow_mode_dialog=False)



def test_background_datum_rejects_ip_target_before_launch(monkeypatch):
 import app.laser_control.space_native as native
 from app.laser.lasercad_control import ControlError
 monkeypatch.setattr(native,'ensure_window',lambda c:pytest.fail('Invalid USB target must not launch'))
 with pytest.raises(ControlError,match='USB machine'):native.datum(None,{'usb_port':'192.168.8.8'},host=lambda p:{})


def test_background_cleanup_failure_does_not_mask_delivered_datum(monkeypatch):
 import app.laser_control.space_native as native
 target='MachName:_AWC_D2713611'
 monkeypatch.setattr(native,'ensure_window',lambda c:{})
 class UI:
  def __init__(self,*a):pass
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def select_usb(self,*a,**k):pass
  def _main(self):pass
 monkeypatch.setattr(native,'DatumUI',UI)
 calls=[]
 class Controller:
  def status(self,**k):return {'available':True,'window_id':1,'process_id':2,'window_title':'LaserCAD','communication_selection':[target]}
  def request(self,action,**k):calls.append(action);return {'requested':True}
 def host(p):
  if p['action']=='hide':raise ValueError('display busy')
  return {'status':'background'}
 assert native.datum(Controller(),{'usb_port':target},host=host)['requested'] is True and calls==['datum']
