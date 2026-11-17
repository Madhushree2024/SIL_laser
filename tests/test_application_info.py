
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import app.application_info as info
import app.main as main

@pytest.mark.parametrize('paths,state',[
    ([], 'not_running'), (['selected.exe'],'matching'),
    (['old.exe'],'different'), (['selected.exe','old.exe'],'multiple'),
    (['selected.exe','selected.exe'],'multiple')])
def test_actual_paths_are_distinguished_from_selected_path(monkeypatch,tmp_path,paths,state):
    selected=tmp_path/'selected.exe'
    monkeypatch.setattr(info,'find_lasercad',lambda value:selected)
    windows=lambda:[{'title':'LaserCAD V8.28.3','executable':str(tmp_path/p)} for p in paths]
    result=info.application_info({'lasercad':{'executable':str(selected)}},windows)
    assert result['lasercad_state']==state
    assert result['resolved_lasercad_path']==str(selected.resolve())
    assert result['running_lasercad_paths']==list(dict.fromkeys(str((tmp_path/p).resolve()) for p in paths))

def test_failed_inspection_does_not_claim_selected_copy_is_running(monkeypatch,tmp_path):
    monkeypatch.setattr(info,'find_lasercad',lambda value:None)
    def unavailable():raise OSError('inspection unavailable')
    result=info.application_info({},unavailable)
    assert result['lasercad_state']=='unavailable' and not result['running_lasercad_paths']

def test_frozen_app_reports_actual_executable(monkeypatch,tmp_path):
    exe=tmp_path/'TemplateCutting.exe'
    monkeypatch.setattr(info,'sys',SimpleNamespace(frozen=True,executable=str(exe)))
    monkeypatch.setattr(info,'find_lasercad',lambda value:None)
    result=info.application_info({},lambda:[])
    assert result['desktop'] and result['app_path']==str(exe.resolve())

def test_authenticated_info_endpoint_is_read_only(monkeypatch):
    monkeypatch.setattr(main.lasercad_controller,'_api',lambda:SimpleNamespace(windows=lambda:[]))
    result=TestClient(main.app).get('/api/application-info')
    assert result.status_code==200
    assert result.json()['version']==main.APP_VERSION
    assert 'app_path' in result.json() and 'resolved_lasercad_path' in result.json()
