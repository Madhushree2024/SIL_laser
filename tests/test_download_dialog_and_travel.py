import ctypes,json
from pathlib import Path
from types import SimpleNamespace
import pytest,yaml,ezdxf
from app.laser_control.lasercad_export_ui import NativeExportUI
from app.laser_control import lasercad_export_ui as ui_module
from app.laser.lasercad_control import ControlError
from app.calibration.usable_travel_upgrade import upgrade_usable_travel
from app.calibration.machine_placement import load_machine_placement
from app.export.lasercad_bed_mapping import prepare_bed_copy
from test_lasercad_bed_mapping import layout


def dialog_ui(monkeypatch,child=True):
    ui=object.__new__(NativeExportUI);ui.window={'hwnd':1,'pid':77}
    ui.callback=lambda fn:fn
    titles={1:'LaserCAD - job.dxf',20:'Download Document',30:'Other',40:'Layer Parameters',1015:'FACE'}
    def pid(hwnd,result):result._obj.value=88 if hwnd==40 else 77
    parents={20:99,30:0,40:99,1899:20,1779:20}
    def children(root,visit,_):
        if child:
            for hwnd in [1,20,40]:visit(hwnd,0)
    def top(visit,_):
        for hwnd in ([30,40] if child else [20,30,40]):visit(hwnd,0)
    def kind(hwnd,buffer,length):buffer.value='Button' if hwnd in {1899,1779} else 'CustomModalClass'
    ui.user=SimpleNamespace(GetWindowThreadProcessId=pid,GetAncestor=lambda *a:99,
        IsChild=lambda parent,hwnd:parents.get(hwnd)==parent,GetParent=lambda hwnd:parents.get(hwnd,0),
        GetWindow=lambda *args:0,EnumWindows=top,EnumChildWindows=children,
        IsWindowVisible=lambda hwnd:hwnd!=1899,GetClassNameW=kind,
        GetDlgItem=lambda window,cid:cid,GetDlgCtrlID=lambda hwnd:hwnd)
    ui.text=lambda hwnd:titles.get(hwnd,'')
    ui.controls=lambda window:[{'hwnd':cid,'control_id':cid,'kind':'Edit' if cid==1015 else 'Button','enabled':True} for cid in [1015,1384,1504,1779]]
    ui._identity=lambda:None
    ui._wait=lambda predicate,*a,**k:predicate() or pytest.fail('Dialog was not recognized')
    monkeypatch.setattr(ui_module,'is_hosted_window',lambda hwnd:hwnd==1)
    return ui

@pytest.mark.parametrize('child',[True,False])
def test_custom_download_found_as_embedded_child_or_top_level_without_foreign_process(monkeypatch,child):
    ui=dialog_ui(monkeypatch,child)
    assert ui._dialogs()==[{'hwnd':20,'pid':77,'title':'Download Document'}]
    assert ui._dialog({1015,1384,1504,1779})['hwnd']==20

def test_owner_relation_is_followed_and_unrelated_window_rejected(monkeypatch):
    ui=dialog_ui(monkeypatch)
    ui.user.GetParent=lambda hwnd:0
    ui.user.GetWindow=lambda hwnd,flag:1 if hwnd==20 else 0
    assert ui._belongs(20)
    assert not ui._belongs(30) and not ui._belongs(40)

def test_hidden_usb_multidevice_option_is_read_and_document_requested_once(monkeypatch):
    ui=dialog_ui(monkeypatch);calls=[]
    ui._main=lambda:ui.window
    ui._click=lambda window,cid,labels:calls.append((window['hwnd'],cid))
    ui._send=lambda hwnd,msg,*args:0
    ui.open_download();ui.download_document()
    assert calls==[(1,1522),(20,1384)]
    assert ui.transfer_baseline=={20}

@pytest.mark.parametrize('selected',[1779,1899])
def test_visible_or_hidden_selected_download_option_blocks_submission(monkeypatch,selected):
    ui=dialog_ui(monkeypatch);calls=[]
    ui._main=lambda:ui.window
    ui._click=lambda window,cid,labels:calls.append(cid)
    ui._send=lambda hwnd,msg,*args:1 if hwnd==selected else 0
    with pytest.raises(ControlError,match='enabled'):ui.open_download()
    assert calls==[1522]


def test_915x630_upgrade_preserves_marker_geometry_and_machine_position(layout,tmp_path):
    exe,path,config=layout;root=tmp_path
    camera=root/'camera_calibration.npz';camera.write_bytes(b'unchanged lens calibration')
    markers=root/'data/calibration/aruco_viewer_bed_layout.json';marker_before=markers.read_bytes()
    survey=root/'data/calibration/machine_placement.json';before=json.loads(survey.read_text())
    source=path.read_bytes()
    report=upgrade_usable_travel(root,exe.parent.parent.parent/'_internal')
    updated=yaml.safe_load((root/'app/config/config.yaml').read_text())
    after=load_machine_placement(root,updated)
    assert updated['workspace']['width_mm']==915 and updated['workspace']['height_mm']==630
    assert updated['workspace']['physical_width_mm']==960 and updated['workspace']['physical_height_mm']==650
    assert after['bed_mm']==[915,630]
    for key in ['working_markers','machine_markers','working_to_machine']:
        assert before[key]==after[key]
    assert camera.read_bytes()==b'unchanged lens calibration' and markers.read_bytes()==marker_before
    assert Path(report['survey_backup']).is_file()
    (exe.parent.parent/'AWCCfg/SysCfg.ini').write_text('[X]\nRange=915\n[Y]\nRange=630\n')
    mapping=prepare_bed_copy(path,updated,exe)
    assert mapping['bed_width_mm']==915 and mapping['bed_height_mm']==630
    assert mapping['machine_job_origin_mm']==pytest.approx([150,140])
    assert not mapping['mirrored'] and not mapping['scaled']
    assert path.read_bytes()==source
    assert upgrade_usable_travel(root,exe.parent.parent.parent/'_internal')['status']=='already_applied'


def test_outside_new_travel_survey_is_preserved_and_blocked_not_shifted(layout,tmp_path):
    exe,path,config=layout
    survey=tmp_path/'data/calibration/machine_placement.json'
    data=json.loads(survey.read_text())
    for marker in data['machine_markers']:marker['x_mm']+=80
    data['working_to_machine'][0][2]+=80
    survey.write_text(json.dumps(data));before=survey.read_bytes()
    report=upgrade_usable_travel(tmp_path,exe.parent.parent.parent/'_internal')
    assert report['machine_survey'].startswith('preserved_requires_review')
    assert survey.read_bytes()==before
    updated=yaml.safe_load((tmp_path/'app/config/config.yaml').read_text())
    with pytest.raises(ValueError,match='Bed dimensions changed'):load_machine_placement(tmp_path,updated)


@pytest.mark.parametrize('panel_child',[True,False])
def test_permanent_control_panel_does_not_block_preparation(monkeypatch,panel_child):
    ui=dialog_ui(monkeypatch,child=False)
    ui.user.EnumWindows=lambda visit,_:visit(50,0)
    ui.user.EnumChildWindows=lambda root,visit,_:visit(50,0)
    ui._belongs=lambda hwnd:True
    ui.text=lambda hwnd:'Control Panel'
    ui.user.GetClassNameW=lambda hwnd,buffer,size:setattr(buffer,'value','#32770')
    ui.user.GetWindowLongW=lambda *args:0x40000000 if panel_child else 0x80000000
    ui.controls=lambda window:[{'control_id':cid} for cid in [1241,1674,1063,1522,1523]]
    ui.ready=lambda window:True
    assert ui._dialogs()==[]
    assert ui._main()==ui.window


def test_unnamed_child_panes_are_not_modal_but_real_download_is_found(monkeypatch):
    ui=dialog_ui(monkeypatch)
    original=ui.text
    ui.text=lambda hwnd:'Docked pane' if hwnd==50 else original(hwnd)
    def children(root,visit,_):
        for hwnd in [50,20]:visit(hwnd,0)
    ui.user.EnumChildWindows=children
    belongs=ui._belongs
    ui._belongs=lambda hwnd:True if hwnd==50 else belongs(hwnd)
    def kind(hwnd,buffer,length):buffer.value='#32770'
    ui.user.GetClassNameW=kind
    ui.user.GetWindowLongW=lambda hwnd,flag:0x40000000
    assert ui._dialogs()==[{'hwnd':20,'pid':77,'title':'Download Document'}]


def test_real_unexpected_popup_is_not_ignored_and_is_named_in_error(monkeypatch):
    ui=dialog_ui(monkeypatch,child=False)
    ui.user.EnumWindows=lambda visit,_:visit(20,0)
    ui.user.EnumChildWindows=lambda *args:None
    ui.text=lambda hwnd:'Replace existing file?'
    ui.user.GetClassNameW=lambda hwnd,buffer,size:setattr(buffer,'value','#32770')
    ui.user.GetWindowLongW=lambda *args:0x80000000
    ui.ready=lambda window:False
    with pytest.raises(ControlError,match='Replace existing file'):
        ui._main()


def test_unknown_child_modal_still_blocks_when_main_disabled(monkeypatch):
    ui=dialog_ui(monkeypatch,child=False)
    ui.user.EnumWindows=lambda visit,_:visit(20,0)
    ui.user.EnumChildWindows=lambda *args:None
    ui.text=lambda hwnd:'Unknown child prompt'
    ui.user.GetClassNameW=lambda hwnd,buffer,size:setattr(buffer,'value','#32770')
    ui.user.GetWindowLongW=lambda *args:0x40000000
    ui.ready=lambda window:False
    with pytest.raises(ControlError,match='Unknown child prompt'):
        ui._main()
