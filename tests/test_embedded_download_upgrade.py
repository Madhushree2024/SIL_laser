
import yaml
import pytest
from desktop_runtime import upgrade_embedded_download_settings

def test_upgrade_preserves_survey_and_recipe_scope_and_only_runs_once(tmp_path):
    workspace=tmp_path/'workspace'
    cfg=workspace/'app/config/config.yaml';cfg.parent.mkdir(parents=True)
    original={'workspace':{'width_mm':950,'height_mm':650},'camera':{'device':3},
              'laser_control':{'host_lasercad':False,'split_after_dxf':True},
              'lasercad':{'executable':'old.exe','prepare_and_download':{'enabled':False,'run_box_after_success':False}},
              'machine_workflow':{'enabled':True,'park_x_mm':13}}
    cfg.write_text(yaml.safe_dump(original),encoding='utf-8')
    before=cfg.read_bytes()
    survey=workspace/'data/calibration/machine.json';survey.parent.mkdir(parents=True)
    survey.write_bytes(b'survey unchanged')
    camera=workspace/'camera_calibration.npz';camera.write_bytes(b'camera unchanged')
    bundle=tmp_path/'installed/_internal';bundle.mkdir(parents=True)
    exe=bundle.parent/'LaserCAD/AWCPrograms/LaserCAD.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'test')
    result=upgrade_embedded_download_settings(workspace,bundle)
    got=yaml.safe_load(cfg.read_text())
    assert got['laser_control']=={'host_lasercad':True,'split_after_dxf':False,'dashboard_enabled':False}
    opts=got['lasercad']['prepare_and_download']
    assert opts['enabled'] and opts['usb_port']=='MachName:_AWC_D2713611'
    assert opts['communication_mode']=='USB' and opts['speed_mm_s']==40 and opts['power_percent']==50
    assert opts['work_mode']=='Cut' and opts['run_box_after_success'] is False
    assert got['lasercad']['executable']==str(exe.resolve())
    assert not got['machine_workflow']['enabled'] and got['machine_workflow']['park_x_mm']==13
    assert got['camera']==original['camera'] and got['workspace']==original['workspace']
    assert survey.read_bytes()==b'survey unchanged' and camera.read_bytes()==b'camera unchanged'
    assert __import__('pathlib').Path(result['backup']).read_bytes()==before
    got['lasercad']['prepare_and_download']['enabled']=False
    cfg.write_text(yaml.safe_dump(got),encoding='utf-8')
    saved=cfg.read_bytes()
    assert upgrade_embedded_download_settings(workspace,bundle)['status']=='already_applied'
    assert cfg.read_bytes()==saved

@pytest.mark.parametrize('content',['[]','lasercad: invalid','lasercad:\n  prepare_and_download: invalid'])
def test_upgrade_invalid_settings_fail_without_replacing_them(tmp_path,content):
    cfg=tmp_path/'app/config/config.yaml';cfg.parent.mkdir(parents=True);cfg.write_text(content)
    with pytest.raises(RuntimeError):
        upgrade_embedded_download_settings(tmp_path,tmp_path/'_internal')
    assert cfg.read_text()==content
