from types import SimpleNamespace
import pytest
from app.laser_control.lasercad_transfer_result import classify_message,wait_for_transfer
from app.laser_control.lasercad_export_ui import NativeExportUI
from app.laser.lasercad_control import ControlError


@pytest.mark.parametrize('text',['Communicating successful!','communicating successful !','Communicating successful.',' Communicating\nsuccessful! '])
def test_only_explicit_success_is_completed(text):
    assert classify_message([text])['status']=='completed'

@pytest.mark.parametrize('text',['Communicating unsuccessful!','Network Communicating unsuccessful!','Communication failed','Connection failed','Transfer timeout','USB not connected'])
def test_errors_never_match_success(text):
    assert classify_message([text])['status']=='failed'

@pytest.mark.parametrize('text',['Not communicating successful!','Download Document','Download click delivered','Replace existing file?','Communicating successful! But failed.','File sent'])
def test_unknown_text_is_never_completion(text):
    assert classify_message([text])['status']!='completed'

def test_failure_wins_over_success_in_ambiguous_message():
    assert classify_message(['Communicating successful!','Communication failed'])['status']=='failed'

def monitor(observe):
    now=[0]
    return wait_for_transfer(observe,{1},5,clock=lambda:now[0],pause=lambda seconds:now.__setitem__(0,now[0]+seconds))

def test_stale_and_unrelated_success_messages_are_ignored():
    response=monitor(lambda:[{'hwnd':1,'owned_by_download':True,'texts':['Communicating successful!']},
                             {'hwnd':2,'owned_by_download':False,'texts':['Communicating successful!']}])
    assert response['status']=='unconfirmed' and 'Timed out' in response['reason']

def test_current_owned_success_is_the_confirmation():
    response=monitor(lambda:[{'hwnd':2,'owned_by_download':True,'texts':['Communicating successful!']}])
    assert response['status']=='completed' and response['dialog_id']==2

def test_fresh_unexpected_prompt_and_multiple_dialogs_are_unconfirmed():
    prompt={'hwnd':2,'owned_by_download':True,'texts':['Replace existing file?']}
    assert monitor(lambda:[prompt])['status']=='unconfirmed'
    success={'hwnd':3,'owned_by_download':True,'texts':['Communicating successful!']}
    assert monitor(lambda:[prompt,success])['status']=='unconfirmed'

@pytest.fixture
def usb():
    ui=object.__new__(NativeExportUI)
    ui.window={'hwnd':1};ui.download={'hwnd':2}
    state={'checked':False,'rows':['Machine A: COM40','Machine B: COM4'],
           'values':['Machine A:COM40','Machine B:COM4'],'text':'Old network:192.168.8.8','selected':None,'calls':[]}
    ui.user=SimpleNamespace(GetWindow=lambda *args:0,GetParent=lambda hwnd:{3:2,2:1,1:0,4:1}.get(hwnd,0))
    ui._main=lambda:ui.window
    def click(window,cid,labels):
        state['calls'].append(('button',cid))
        if cid==1610:state['checked']=True
    ui._click=click;ui._dialog=lambda ids:{'hwnd':2}
    ui._one=lambda window,cid,*args:{'hwnd':cid,'control_id':cid}
    def send(hwnd,msg,*args):
        if msg==0x00F0:return 1 if state['checked'] else 0
        if msg==0x1004:return len(state['rows'])
        if msg==0x102C:return 2 if state['selected']==args[0] else 0
        raise AssertionError('Unexpected native operation')
    ui._send=send
    ui._read_accessible_row=lambda listing,index:state['rows'][index]
    ui._row_click=lambda listing,index:state.update(selected=index)
    def wait(predicate,*args,**kwargs):
        assert predicate(),'Expected observation did not become true'
    ui._wait=wait;ui._post=lambda *args:state['calls'].append(('close',args[0]))
    ui._closed=lambda window:None
    ui._combo_values=lambda combo:state['values'] if state['checked'] else ['Machine:192.168.8.8']
    ui._select=lambda window,combo,text:state.update(text=text)
    ui.text=lambda hwnd:state['text']
    return ui,state

def test_usb_mode_and_exact_port_are_selected_without_ip_fallback(usb):
    ui,state=usb;ui.select_communication('COM4')
    assert state['selected']==1 and state['text']=='Machine B:COM4'
    assert state['calls']==[('button',1241),('button',1610),('close',2)]
    assert ui._communication_selected('COM4')

@pytest.mark.parametrize('rows',[['Only COM40'],['First COM4','Second COM4'],[]])
def test_missing_or_ambiguous_usb_port_never_selects_a_different_device(usb,rows):
    ui,state=usb;state['rows']=rows
    with pytest.raises(ControlError,match='Configure exactly one'):ui.select_usb('COM4')
    assert state['selected'] is None

def test_usb_verification_detects_changed_entry(usb):
    ui,state=usb;ui.select_usb('COM4');state['text']='Another name:COM4'
    assert not ui._communication_selected('COM4')

def test_only_download_owned_dialog_is_accepted(usb):
    ui,state=usb
    assert ui._owned_by_download(3)
    assert not ui._owned_by_download(4)


def test_existing_usb_dropdown_entry_is_selected_directly(usb):
    ui,state=usb
    state['checked']=True
    ui.select_usb('COM4')
    assert state['text']=='Machine B:COM4'
    assert state['calls']==[]
    assert state['selected'] is None
