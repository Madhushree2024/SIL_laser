from pathlib import Path
import hashlib
import pytest
from app.export import lasercad_import_coordinates as module


@pytest.mark.parametrize('encoding',['latin-1','utf-8-sig','utf-16le','utf-16be'])
def test_changes_only_import_docking_preserving_encoding_comments_and_lines(encoding):
    text='[ImportFile]\r\nDxfUnit=0\r\nImportDockLoction = 1 ; centre\r\n[UserCfg]\r\nPageZeroPos=3\r\nCom=4\r\n'
    prefix=b'\xff\xfe' if encoding=='utf-16le' else b'\xfe\xff' if encoding=='utf-16be' else b''
    data=prefix+text.encode(encoding)
    updated,old=module.no_docking_bytes(data)
    assert old==1
    assert updated==prefix+text.replace('= 1 ; centre','= 0 ; centre').encode(encoding)
    assert module.no_docking_bytes(updated)==(updated,0)


@pytest.mark.parametrize('text',['[ImportFile]\nOther=1\n','[ImportFile]\nImportDockLoction=9\n',
 '[ImportFile]\nImportDockLoction=1\nImportDockLoction=0\n','[ImportFile]\nImportDockLoction=1\n[ImportFile]\n'])
def test_unknown_or_ambiguous_preferences_are_not_guessed(text):
    with pytest.raises(ValueError):module.no_docking_bytes(text.encode())


@pytest.fixture
def install(tmp_path,monkeypatch):
    exe=tmp_path/'LaserCAD'/'AWCPrograms'/'LaserCAD.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'Never executed')
    settings=exe.parent.parent/'AWCCfg'/'SoftCfg.ini';settings.parent.mkdir()
    settings.write_bytes(b'[ImportFile]\r\nImportDockLoction=1\r\n[UserCfg]\r\nPageZeroPos=3\r\nCom=4\r\n')
    monkeypatch.setattr(module,'SUPPORTED_EXECUTABLE_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest())
    monkeypatch.setattr(module,'running_lasercad',lambda:False)
    return exe,settings


def test_closed_installation_backed_up_and_repeat_is_idempotent(install):
    exe,settings=install;before=settings.read_bytes()
    result=module.prepare_import_coordinates(exe)
    assert result['changed'] and result['mode']=='None'
    assert Path(result['backup']).read_bytes()==before
    assert settings.read_bytes()==before.replace(b'Loction=1',b'Loction=0')
    assert result['coordinates_verified_in_lasercad'] is False
    assert not module.prepare_import_coordinates(exe)['changed']
    assert len(list(settings.parent.glob('coordinate_import_backups/*/SoftCfg.ini')))==1


def test_running_lasercad_blocks_setting_change_and_does_not_launch(install,monkeypatch):
    exe,settings=install;before=settings.read_bytes()
    monkeypatch.setattr(module,'running_lasercad',lambda:True)
    with pytest.raises(ValueError,match='Close LaserCAD once'):module.prepare_import_coordinates(exe)
    assert settings.read_bytes()==before


def test_other_binary_is_rejected_without_editing_settings(install,monkeypatch):
    exe,settings=install;before=settings.read_bytes()
    exe.write_bytes(b'Different version')
    with pytest.raises(ValueError,match='not been verified'):module.prepare_import_coordinates(exe)
    assert settings.read_bytes()==before


def test_handoff_cannot_launch_if_coordinate_setup_fails(install,tmp_path,monkeypatch):
    from app.export import lasercad as cad
    exe,settings=install;dxf=tmp_path/'sample.dxf';dxf.write_text('unchanged export')
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda _:exe)
    def fail(*args):raise ValueError('Close LaserCAD once before import')
    monkeypatch.setattr(cad,'prepare_import_coordinates',fail)
    monkeypatch.setattr(cad.subprocess,'Popen',lambda *a,**k:pytest.fail('Unverified import launched'))
    result=cad.open_dxf_in_lasercad(dxf,{})
    assert result['status']=='warning' and 'Close LaserCAD' in result['message']
    assert dxf.read_text()=='unchanged export'


def test_coordinate_setup_precedes_exact_file_handoff(install,tmp_path,monkeypatch):
    from app.export import lasercad as cad
    from types import SimpleNamespace
    exe,settings=install;dxf=tmp_path/'sample.dxf';dxf.write_text('saved unchanged geometry')
    calls=[]
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda _:exe)
    def setup(path):
        assert path==exe;calls.append('coordinate preference')
        return {'status':'configured','mode':'None','value':0}
    def launch(args,**kwargs):
        assert args==[str(exe),str(dxf.resolve())]
        assert kwargs['shell'] is False
        calls.append('DXF launch');return SimpleNamespace(pid=123)
    monkeypatch.setattr(cad,'prepare_import_coordinates',setup)
    monkeypatch.setattr(cad.subprocess,'Popen',launch)
    monkeypatch.setattr(cad,'prepare_lasercad_preview',lambda *a,**k:{'status':'refresh_requested','ready':True})
    result=cad.open_dxf_in_lasercad(dxf,{})
    assert calls==['coordinate preference','DXF launch']
    assert result['status']=='launch_requested' and result['import_coordinates']['value']==0
    assert dxf.read_text()=='saved unchanged geometry'
