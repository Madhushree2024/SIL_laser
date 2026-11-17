import json
from pathlib import Path
from types import SimpleNamespace
import pytest,yaml
from app.laser_control.cut_recipe import cut_recipe,upgrade_cut_recipe
from app.laser_control.lasercad_export_ui import NativeExportUI
from test_export_automation import setup,READY
from test_laser_control_api import laser_workspace

@pytest.mark.parametrize('speed,power',[(90,10),(75.5,12.5),(40,50),(90,0),(120,100)])
def test_configured_values_reach_layer_and_verification_without_post_download_wait(setup,speed,power):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download'].update(speed_mm_s=speed,power_percent=power)
    result=automation.run(path,READY)
    assert result['status']=='download_requested'
    assert ('parameters',speed,power) in calls and ('verify','COM4',speed,power) in calls
    assert result['speed_mm_s']==speed and result['power_percent']==power
    assert calls[-1]==('download_document',)
    assert not any(c[0] in {'wait_transfer','frame','start'} for c in calls)

@pytest.mark.parametrize('speed,power',[(0,10),(-1,10),(float('inf'),10),(float('nan'),10),(True,10),(90,-1),(90,101),(90,float('nan')),(90,True),(None,10)])
def test_invalid_values_block_before_native_controls(setup,speed,power):
    automation,path,controller,config,calls,fail,factory=setup
    config['lasercad']['prepare_and_download'].update(speed_mm_s=speed,power_percent=power)
    assert automation.run(path,READY)['status']=='blocked'
    assert not calls

@pytest.mark.parametrize('minimum,maximum,expected',[(40,10,10),(5,10,5),(0,10,0),(40,0,0),(40,35.5,35.5)])
def test_lower_maximum_clamps_minimum_first_and_preserves_other_settings(minimum,maximum,expected):
    ui=object.__new__(NativeExportUI);dialog={'hwnd':2}
    fields=[{'hwnd':i} for i in [1195,1790,1832,1836]]
    values={1195:'Cut',1790:'40',1832:'50',1836:str(minimum),777:'other channel unchanged'}
    writes=[]
    ui._layer_dialog=lambda:dialog
    ui._one=lambda window,cid,kind:{'hwnd':cid}
    ui._select=lambda window,control,value:values.update({control['hwnd']:value})
    ui._wait=lambda predicate,message:predicate() or pytest.fail(message)
    ui.controls=lambda window:[{'control_id':1790,'kind':'Edit'}]
    ui._fields=lambda window:fields
    ui.text=lambda hwnd:values[hwnd]
    def write(window,control,value):writes.append(control['hwnd']);values[control['hwnd']]=value
    ui.set_text=write;ui._click=lambda *a:None;ui._closed=lambda *a:None;ui._main=lambda:None
    ui.configure_cut(90,maximum)
    assert float(values[1790])==90 and float(values[1832])==maximum and float(values[1836])==expected
    assert values[777]=='other channel unchanged'
    if minimum>maximum:assert writes.index(1836)<writes.index(1832)
    else:assert 1836 not in writes


def test_defaults_upgrade_once_and_keeps_later_manual_recipe(tmp_path):
    path=tmp_path/'app/config/config.yaml';path.parent.mkdir(parents=True)
    config={'workspace':{'width_mm':915,'height_mm':630},'camera':{'device':4},
        'lasercad':{'prepare_and_download':{'speed_mm_s':40,'power_percent':50,'enabled':True}}}
    path.write_text(yaml.safe_dump(config));before=path.read_bytes()
    camera=tmp_path/'camera_calibration.npz';camera.write_bytes(b'unchanged lens')
    profile=tmp_path/'data/calibration/marker.json';profile.parent.mkdir(parents=True);profile.write_bytes(b'unchanged markers')
    report=upgrade_cut_recipe(tmp_path)
    saved=yaml.safe_load(path.read_text())
    assert cut_recipe(saved['lasercad']['prepare_and_download'])==(90,10)
    assert saved['workspace']==config['workspace'] and saved['camera']==config['camera']
    assert camera.read_bytes()==b'unchanged lens' and profile.read_bytes()==b'unchanged markers'
    assert Path(report['config_backup']).read_bytes()==before
    saved['lasercad']['prepare_and_download'].update(speed_mm_s=75,power_percent=8)
    path.write_text(yaml.safe_dump(saved));manual=path.read_bytes()
    assert upgrade_cut_recipe(tmp_path)['status']=='already_applied' and path.read_bytes()==manual


def test_settings_api_saves_manual_values_and_rejects_bad_power_without_overwrite(laser_workspace,monkeypatch):
    import app.main as main
    from app.config import settings
    client,root,config=laser_workspace
    target=root/'app/config/config.yaml';target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(settings,'CONFIG_PATH',target)
    monkeypatch.setattr(main,'load_config',settings.load_config)
    monkeypatch.setattr(main,'save_config',settings.save_config)
    custom={**config,'lasercad':{'prepare_and_download':{'enabled':True,'work_mode':'Cut','speed_mm_s':85.5,'power_percent':7.5}}}
    response=client.post('/api/config',json=custom)
    assert response.status_code==200,response.text
    saved=client.get('/api/config').json()
    assert cut_recipe(saved['lasercad']['prepare_and_download'])==(85.5,7.5)
    before=target.read_bytes();custom['lasercad']['prepare_and_download']['power_percent']=101
    response=client.post('/api/config',json=custom)
    assert response.status_code==400 and target.read_bytes()==before


def test_settings_form_has_editable_speed_and_power():
    source=(Path(__file__).parents[1]/'frontend/app.js').read_text(encoding='utf-8')
    assert '["lasercad.prepare_and_download.speed_mm_s", "Cutting speed (mm/s)", "number", 90' in source
    assert '["lasercad.prepare_and_download.power_percent", "Maximum power (%)", "number", 10' in source
