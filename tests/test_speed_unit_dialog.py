
from types import SimpleNamespace
import pytest
from app.laser_control.lasercad_export_ui import NativeExportUI
from app.laser.lasercad_control import ControlError

def unit_ui(monkeypatch,unit=1):
    from app.laser_control import lasercad_speed_units
    ui=object.__new__(NativeExportUI)
    ui.window={'hwnd':1,'pid':77,'executable':'LaserCAD.exe'}
    ui.kernel=object();ui.process=10
    state={'ui_calls':[]}
    ui._main=lambda:ui.window
    ui._identity=lambda:None
    ui._post=lambda *args:pytest.fail('Speed-unit verification must not open settings')
    monkeypatch.setattr(lasercad_speed_units,'read_speed_unit',lambda *args:unit)
    return ui,state

def test_unit_check_opens_no_settings_dialog(monkeypatch):
    ui,state=unit_ui(monkeypatch)
    ui.verify_speed_units()
    ui._speed_units(check_only=True)
    assert state['ui_calls']==[]

def test_m_per_minute_stops_without_opening_or_changing_settings(monkeypatch):
    ui,state=unit_ui(monkeypatch,2)
    with pytest.raises(ControlError,match='m/min'):ui.verify_speed_units()
    assert state['ui_calls']==[]

def test_custom_class_owned_system_settings_dialog_is_detected():
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1,'pid':20}
    ui.callback=lambda callback:callback
    ui.text=lambda hwnd:{2:'System settings',3:'System settings',4:'Unrelated'}[hwnd]
    ui._belongs=lambda hwnd:hwnd!=3
    def enumerate_windows(callback,_):
        for hwnd in [2,3,4]:callback(hwnd,0)
    ui.user=SimpleNamespace(EnumWindows=enumerate_windows,EnumChildWindows=lambda *args:None,
        GetClassNameW=lambda hwnd,buffer,length:setattr(buffer,'value','CustomPropertySheet'),
        IsWindowVisible=lambda hwnd:True)
    assert ui._dialogs()==[{'hwnd':2,'pid':20,'title':'System settings'}]

@pytest.mark.parametrize('count,valid',[(1,True),(0,False),(2,False),(-1,False)])
def test_single_layer_guard_preserves_custom_output_checkbox(count,valid):
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1}
    ui._main=lambda:ui.window
    def one(window,cid,kind):
        assert window==ui.window and cid==1063
        return {'hwnd':1063}
    ui._one=one
    def send(hwnd,msg,*args):
        assert msg==0x1004, 'Do not mistake row-state images for subitem Output'
        return count
    ui._send=send
    if valid:ui._require_single_cut_layer()
    else:
        with pytest.raises(ControlError,match='one cutting layer'):ui._require_single_cut_layer()

def test_cut_setup_never_checks_z_enable_as_output():
    ui=object.__new__(NativeExportUI)
    dialog={'hwnd':2}
    fields=[{'hwnd':i} for i in [1195,1790,1832,1836]]
    values={1195:'Cut',1790:'100',1832:'50',1836:'40'}
    ui._layer_dialog=lambda:dialog
    def one(window,cid,kind):
        assert cid!=1848, 'Z movement control must never be used as output'
        return {'hwnd':cid}
    ui._one=one
    ui._select=lambda window,control,value:values.update({control['hwnd']:value})
    ui._wait=lambda predicate,message:predicate() or pytest.fail(message)
    ui.controls=lambda window:[{'control_id':1790,'kind':'Edit'}]
    ui._fields=lambda window:fields
    ui.text=lambda hwnd:values[hwnd]
    ui.set_text=lambda window,control,value:values.update({control['hwnd']:value})
    ui._click=lambda *args:None
    ui._closed=lambda window:None
    ui._main=lambda:None
    ui.configure_cut(40,50)
    assert values=={1195:'Cut',1790:'40',1832:'50',1836:'40'}
