import ctypes
import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.laser_control import lasercad_speed_units as units
from app.laser_control import lasercad_export_ui as ui_module
from app.laser_control.lasercad_export_ui import NativeExportUI
from app.laser.lasercad_control import ControlError

@pytest.mark.parametrize('unit',[1,2])
def test_loaded_unit_read_is_bound_to_supported_binary_and_module(monkeypatch,tmp_path,unit):
    exe=tmp_path/'LaserCAD.exe';exe.write_bytes(b'known executable')
    monkeypatch.setattr(units,'SUPPORTED_EXECUTABLE_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest())
    monkeypatch.setattr(units,'_module_base',lambda kernel,pid,path:0x12340000)
    calls=[]
    def read(process,address,result,size,count):
        calls.append((process,address,size))
        result._obj.value=unit;count._obj.value=4;return True
    value=units.read_speed_unit(SimpleNamespace(ReadProcessMemory=read),11,77,exe)
    assert value==unit
    assert calls==[(11,0x12340000+units.SPEED_UNIT_RVA,4)]

def test_unrecognised_executable_cannot_use_private_unit_offset(tmp_path):
    exe=tmp_path/'LaserCAD.exe';exe.write_bytes(b'unknown')
    with pytest.raises(ControlError,match='not supported'):
        units.read_speed_unit(object(),11,77,exe)

@pytest.mark.parametrize('value,count,ok',[(0,4,True),(3,4,True),(1,2,True),(1,4,False)])
def test_invalid_or_incomplete_memory_read_stops(monkeypatch,tmp_path,value,count,ok):
    exe=tmp_path/'LaserCAD.exe';exe.write_bytes(b'known executable')
    monkeypatch.setattr(units,'SUPPORTED_EXECUTABLE_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest())
    monkeypatch.setattr(units,'_module_base',lambda *args:0x400000)
    def read(process,address,result,size,out):
        result._obj.value=value;out._obj.value=count;return ok
    with pytest.raises(ControlError):units.read_speed_unit(SimpleNamespace(ReadProcessMemory=read),11,77,exe)

@pytest.mark.parametrize('hosted,pid,parent,expected',[
    (True,77,99,True),(False,77,99,False),(True,78,99,False),
    (True,77,100,False),(False,77,1,True)])
def test_popup_must_belong_to_exact_process_and_native_window_or_host(monkeypatch,hosted,pid,parent,expected):
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1,'pid':77}
    def getpid(hwnd,out):out._obj.value=pid
    ui.user=SimpleNamespace(GetWindowThreadProcessId=getpid,IsChild=lambda *a:False,
        GetWindow=lambda *args:0,GetAncestor=lambda hwnd,flag:99,GetParent=lambda hwnd:parent if hwnd==2 else 0)
    monkeypatch.setattr(ui_module,'is_hosted_window',lambda hwnd:hosted)
    assert ui._belongs(2) is expected

def test_layer_double_click_targets_red_first_column():
    ui=object.__new__(NativeExportUI)
    written=[];posted=[];ui.remote=1234
    ui._write_remote=written.append
    ui._read_remote=lambda length:struct.pack('<4i',0,10,400,40)
    ui._post=lambda hwnd,msg,flags,point:posted.append((msg,point&0xffff,point>>16))
    def send(hwnd,msg,*args):
        if msg==0x1038:return 1
        assert msg==0x101D and args==(0,)
        return 50
    ui._send=send
    ui._row_click({'hwnd':7},0,double=True,subitem=0)
    assert struct.unpack('<4i',written[0])==(0,0,0,0)
    assert all(x==25 and y==25 for _,x,y in posted)
    assert any(msg==0x0203 for msg,_,_ in posted)

def test_layer_dialog_uses_colour_cell_and_expected_controls():
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1}
    listing={'hwnd':7}
    ui._require_single_cut_layer=lambda:listing
    ui._one=lambda *args:listing
    ui._send=lambda *args:1
    calls=[]
    ui._row_click=lambda control,index,**kwargs:calls.append((index,kwargs))
    def dialog(ids):
        assert ids=={1195,1063,1,2}
        return {'hwnd':9}
    ui._dialog=dialog
    assert ui._layer_dialog()=={'hwnd':9}
    assert calls==[(0,{'double':True,'subitem':0})]
