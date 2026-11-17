import pytest
from app.laser.lasercad_control import ControlError
from app.laser_control.lasercad_export_ui import usb_entry_matches
from test_transfer_confirmation import usb
from test_export_automation import setup,READY

DEVICE='MachName:_AWC_D2713611'

def test_exact_named_dropdown_entry_is_selected_and_read_back(usb):
    ui,state=usb;state['checked']=True
    state['values']=['MachName:COM4',DEVICE+'0',DEVICE]
    ui.select_communication(DEVICE)
    assert state['text']==DEVICE and ui._communication_selected(DEVICE)
    assert state['calls']==[]

@pytest.mark.parametrize('entries',[[DEVICE+'0'],['MachName:COM4'],['Other:'+DEVICE]])
def test_missing_exact_name_never_falls_back_to_com4_or_similar_name(usb,entries):
    ui,state=usb;state['checked']=True;state['values']=entries
    state['rows']=['Machine: COM4',DEVICE+'0']
    with pytest.raises(ControlError):ui.select_communication(DEVICE)
    assert state['selected'] is None and state['text']!='MachName:COM4'

def test_duplicate_exact_names_are_ambiguous_and_not_selected(usb):
    ui,state=usb;state['checked']=True;state['values']=[DEVICE,DEVICE]
    with pytest.raises(ControlError,match='More than one'):ui.select_communication(DEVICE)
    assert not state['calls']

def test_named_entry_can_be_selected_after_switching_to_usb_mode(usb):
    ui,state=usb;state['rows']=['Other',DEVICE];state['values']=['Other',DEVICE]
    ui.select_communication(DEVICE)
    assert state['selected']==1 and state['text']==DEVICE
    assert state['calls']==[('button',1241),('button',1610),('close',2)]

def test_named_machine_change_is_detected_before_download_or_frame(usb):
    ui,state=usb;state['checked']=True;state['values']=[DEVICE]
    ui.select_communication(DEVICE);state['text']=DEVICE+'0'
    assert not ui._communication_selected(DEVICE)

@pytest.mark.parametrize('other',['COM4','MachName:COM4','MachName:_AWC_D27136110','Other:'+DEVICE])
def test_named_match_is_exact_not_a_substring_or_com_port(other):
    assert not usb_entry_matches(other,DEVICE)
    assert usb_entry_matches(DEVICE,DEVICE)

def test_download_uses_requested_named_device_without_automatic_run_box(setup):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download'].update(usb_port=DEVICE,run_box_after_success=True)
    result=automation.run(path,READY)
    assert result['download_requested'] and not result['transfer_completed']
    assert calls[0]==('communication',DEVICE) and calls[-1]==('download_document',)
    assert not any(c[0]=='frame' for c in calls)
    assert result['communication_target']==DEVICE

def test_absent_setting_defaults_to_named_machine_instead_of_com4(setup):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download'].pop('usb_port')
    assert automation.run(path,READY)['communication_target']==DEVICE
    assert calls[0]==('communication',DEVICE)
