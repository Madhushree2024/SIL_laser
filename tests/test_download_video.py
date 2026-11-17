from copy import deepcopy
import pytest
from app.laser_control.lasercad_transfer_result import wait_for_transfer
from app.laser_control.download_notification import success_popup_text,notify_download_success
from test_export_automation import setup,READY

PROGRESS={'hwnd':2,'title':'Dealing...','owned_by_download':True,'texts':['Loading  the document']}
SUCCESS={'hwnd':3,'title':'LaserCAD','owned_by_download':True,'texts':['Communicating successful!']}

def monitor(sequence):
    now=[0];samples=iter(sequence)
    return wait_for_transfer(lambda:next(samples,[]),{1},5,
        clock=lambda:now[0],pause=lambda dt:now.__setitem__(0,now[0]+dt))

def test_loading_progress_then_fresh_success():
    result=monitor([[PROGRESS],[PROGRESS],[SUCCESS]])
    assert result['status']=='completed' and result['dialog_id']==3

def test_loading_progress_is_not_completion_and_times_out():
    now=[0]
    result=wait_for_transfer(lambda:[PROGRESS],{1},5,
        clock=lambda:now[0],pause=lambda dt:now.__setitem__(0,now[0]+dt))
    assert result['status']=='unconfirmed' and 'Timed out' in result['reason']

def test_unknown_dialog_and_failure_are_not_hidden_by_progress():
    for text,status in [('Replace existing file?','unconfirmed'),('Communication failed','failed')]:
        prompt={**SUCCESS,'texts':[text]}
        assert monitor([[PROGRESS,prompt]])['status']==status

def test_progress_with_wrong_title_is_not_ignored():
    result=monitor([[{**PROGRESS,'title':'Unexpected prompt'}]])
    assert result['status']=='unconfirmed' and 'unexpected' in result['reason'].lower()

def confirmed():
    return {'transfer_completed':True,'lasercad_reported_success':True,
        'message':'File transfer completed. Start remains manual.',
        'transfer_confirmation':{'status':'completed','confirmation_source':'lasercad_download_dialog',
            'dialog_id':3,'dialog_text':'Communicating successful!'}}

def test_success_popup_requires_actual_confirmation():
    result=confirmed()
    assert success_popup_text(result).startswith('Successful!')
    for key,value in [('transfer_completed',False),('lasercad_reported_success',False)]:
        changed=deepcopy(result);changed[key]=value;assert success_popup_text(changed) is None
    for key,value in [('status','unconfirmed'),('confirmation_source','click'),('dialog_id',None),('dialog_text','Communication failed')]:
        changed=deepcopy(result);changed['transfer_confirmation'][key]=value
        assert success_popup_text(changed) is None
    # Unconfirmed results never import Windows forms or call the UI getter.
    notify_download_success(lambda:pytest.fail('No popup expected'),{})

def test_click_only_flow_never_calls_old_completion_popup(setup):
    automation,path,controller,config,calls,fail,factory=setup
    automation.completion_notifier=lambda result:pytest.fail('No Successful popup for submission')
    result=automation.run(path,READY)
    assert result['status']=='download_requested'
    assert success_popup_text(result) is None
    assert calls[-1]==('download_document',)
    assert automation.run(path,READY)['status']=='already_attempted'
