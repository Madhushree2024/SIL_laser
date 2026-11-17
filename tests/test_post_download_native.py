from types import SimpleNamespace
import pytest
from app.laser_control.lasercad_export_ui import NativeExportUI
from app.laser.lasercad_control import ControlError

@pytest.fixture
def native():
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1};ui.download={'hwnd':2};ui.transfer_baseline={2}
    state={'dialogs':[{'hwnd':2},{'hwnd':3}],'calls':[],'closed':set(),'success':'Communicating successful!','communication':True}
    ui._identity=lambda:None
    ui._dialogs=lambda:state['dialogs']
    ui._owned_by_download=lambda hwnd:hwnd==3
    def controls(window):
        if window['hwnd']==3:return [{'text':state['success'],'kind':'static'}]
        return [{'control_id':cid} for cid in [1384,1015,1504,1779,1899]]
    ui.controls=controls
    def click(window,cid,labels):state['calls'].append(('ack',window['hwnd'],cid))
    ui._click=click
    def closed(window):
        state['closed'].add(window['hwnd']);state['dialogs']=[d for d in state['dialogs'] if d['hwnd']!=window['hwnd']]
    ui._closed=closed
    ui.user=SimpleNamespace(IsWindow=lambda hwnd:hwnd not in state['closed'])
    ui._post=lambda hwnd,msg:state['calls'].append(('close',hwnd,msg))
    ui.ready=lambda window:True
    ui._wait=lambda predicate,message:predicate() or pytest.fail(message)
    def main():
        assert not state['dialogs'];return ui.window
    ui._main=main
    ui._communication_selected=lambda target:state['communication']
    ui._one=lambda window,cid,kind,labels:{'hwnd':cid,'control_id':cid}
    ui.click=lambda window,button:state['calls'].append(('frame',button['control_id']))
    confirmation={'status':'completed','confirmation_source':'lasercad_download_dialog','dialog_id':3}
    return ui,state,confirmation

def test_exact_success_ack_download_close_then_single_frame(native):
    ui,state,confirmation=native
    result=ui.run_box_after_success(confirmation,'COM4',lambda:None)
    assert result['requested'] and result['physical_motion_verified'] is False
    assert state['calls']==[('ack',3,1),('close',2,0x0010),('frame',1100)]

@pytest.mark.parametrize('change',[{'status':'failed'},{'dialog_id':2},{'confirmation_source':'other'}])
def test_no_frame_or_ack_for_unconfirmed_stale_or_other_dialog(native,change):
    ui,state,confirmation=native
    with pytest.raises(ControlError):ui.run_box_after_success({**confirmation,**change},'COM4',lambda:None)
    assert not state['calls']

def test_changed_success_text_is_never_acknowledged(native):
    ui,state,confirmation=native;state['success']='Communication failed'
    with pytest.raises(ControlError):ui.run_box_after_success(confirmation,'COM4',lambda:None)
    assert not state['calls']

def test_changed_machine_selection_blocks_run_box(native):
    ui,state,confirmation=native;state['communication']=False
    with pytest.raises(ControlError):ui.run_box_after_success(confirmation,'COM4',lambda:None)
    assert not any(c[0]=='frame' for c in state['calls'])

def test_changed_import_guard_blocks_run_box(native):
    ui,state,confirmation=native
    def guard():raise ControlError('Import changed')
    with pytest.raises(ControlError):ui.run_box_after_success(confirmation,'COM4',guard)
    assert not any(c[0]=='frame' for c in state['calls'])
