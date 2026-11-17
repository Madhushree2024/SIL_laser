from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import desktop_runtime as desktop


def test_workspace_source_mode_does_not_move_existing_data(monkeypatch):
    monkeypatch.delattr(desktop.sys,'frozen',raising=False)
    assert desktop.workspace_path()==desktop.SOURCE_ROOT


def test_explicit_workspace_is_resolved(tmp_path):
    assert desktop.workspace_path(str(tmp_path))==tmp_path.resolve()


def test_seed_only_initializes_missing_files_and_never_overwrites_existing_values(tmp_path):
    bundle=tmp_path/'bundle';workspace=tmp_path/'workspace'
    for path,text in [('app/config/config.yaml','nesting: shared'),('camera_calibration.npz','saved calibration'),('data/templates/templates.json','[]')]:
        seed=bundle/'seed'/path;seed.parent.mkdir(parents=True,exist_ok=True);seed.write_text(text)
    desktop.prepare_workspace(workspace,bundle)
    config=workspace/'app/config/config.yaml';config.write_text('user settings')
    production=workspace/'data/production/production_2026-10-16.csv';production.write_text('user production')
    calibration=workspace/'camera_calibration.npz';calibration.write_text('user calibration')
    desktop.prepare_workspace(workspace,bundle)
    assert config.read_text()=='user settings'
    assert production.read_text()=='user production'
    assert calibration.read_text()=='user calibration'
    assert (workspace/'data/projects').is_dir()


def test_missing_configuration_stops_safely(tmp_path):
    with pytest.raises(RuntimeError,match='settings are missing'):desktop.prepare_workspace(tmp_path/'workspace',tmp_path/'empty-bundle')


def test_packaged_default_does_not_reuse_the_existing_project(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop.sys,'frozen',True,raising=False)
    monkeypatch.setattr(desktop.Path,'home',classmethod(lambda cls:tmp_path))
    project=tmp_path/'sheathTemplateCutting_manual_calibration'
    for name in ['data/templates/templates.json','app/config/config.yaml']:
        p=project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('preserved')
    assert desktop.workspace_path()==desktop.user_directory()/'Workspace'
    assert desktop.workspace_path()!=project.resolve()


def test_new_machine_uses_persistent_local_appdata(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop.sys,'frozen',True,raising=False)
    monkeypatch.setattr(desktop.Path,'home',classmethod(lambda cls:tmp_path/'home'))
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    assert desktop.workspace_path()==tmp_path/'local/SheathTemplateCuttingAutomation/Workspace'


def test_existing_backend_must_use_the_same_workspace(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop,'get_json',lambda *a:{'application':'areca-cutter','data_root':str(tmp_path)})
    assert desktop.existing_backend('http://127.0.0.1:8001',tmp_path)
    with pytest.raises(RuntimeError,match='different data folder'):desktop.existing_backend('http://127.0.0.1:8001',tmp_path/'other')


def test_older_browser_backend_is_reused_only_for_a_matching_project(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop,'get_json',lambda url,*a:{'application':'areca-cutter'} if url.endswith('/api/health') else {'project_dir':str(tmp_path/'data/projects/capture')})
    assert desktop.existing_backend('http://127.0.0.1:8001',tmp_path)


def test_unrelated_server_is_not_reused(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop,'get_json',lambda *a:{'application':'other'})
    assert not desktop.existing_backend('http://127.0.0.1:8001',tmp_path)


def test_attached_backend_is_not_stopped_or_mutated_on_close(monkeypatch,tmp_path):
    monkeypatch.setattr(desktop,'existing_backend',lambda *a:True)
    backend=desktop.Backend(tmp_path);backend.start();backend.stop()
    assert backend.server is None and backend.thread is None and backend.app_module is None


def test_owned_backend_checks_manual_lock_and_automatic_busy_state(tmp_path):
    backend=desktop.Backend(tmp_path)
    backend.app_module=SimpleNamespace(operation_lock=SimpleNamespace(locked=lambda:True),WORKFLOW={'busy':False})
    assert backend.busy()
    backend.app_module.operation_lock.locked=lambda:False
    backend.app_module.WORKFLOW['busy']=True
    assert backend.busy()
    backend.app_module.WORKFLOW['busy']=False
    assert not backend.busy()


def test_owned_backend_requests_graceful_stop_and_closes_its_socket(tmp_path):
    backend=desktop.Backend(tmp_path);events=[]
    backend.server=SimpleNamespace(should_exit=False)
    backend.thread=SimpleNamespace(join=lambda **kw:events.append('joined'),is_alive=lambda:False)
    backend.socket=SimpleNamespace(close=lambda:events.append('socket closed'))
    backend.stop()
    assert backend.server.should_exit and events==['joined','socket closed']


def test_desktop_only_workspace_keeps_source_assets_and_original_default(tmp_path):
    import os,subprocess,sys
    code='import json;from app.config.settings import ROOT,ASSET_ROOT;print(json.dumps([str(ROOT),str(ASSET_ROOT)]))'
    env=dict(os.environ);env['SHEATH_WORKSPACE']=str(tmp_path)
    output=subprocess.check_output([sys.executable,'-c',code],cwd=desktop.SOURCE_ROOT,env=env,text=True)
    root,assets=json.loads(output)
    assert Path(root)==tmp_path and Path(assets)==desktop.SOURCE_ROOT
    env.pop('SHEATH_WORKSPACE')
    output=subprocess.check_output([sys.executable,'-c',code],cwd=desktop.SOURCE_ROOT,env=env,text=True)
    root,assets=json.loads(output)
    assert root==assets

def test_desktop_uses_a_fresh_document_url_to_avoid_old_login_pages():
    from urllib.parse import urlsplit
    first=desktop.desktop_url('http://127.0.0.1:8001')
    second=desktop.desktop_url('http://127.0.0.1:8001')
    assert first!=second
    assert urlsplit(first).path=='/' and urlsplit(first).netloc=='127.0.0.1:8001'
    assert 'desktop_boot=' in urlsplit(first).query

def test_duplicate_launch_activates_existing_window_without_error_dialog():
    source=(desktop.SOURCE_ROOT/'desktop.py').read_text(encoding='utf-8')
    duplicate=source.split('if not instance.acquire():',1)[1].split('workspace =',1)[0]
    assert 'activate_existing_window()' in duplicate
    assert 'error_dialog' not in duplicate


def test_installer_requests_graceful_close_and_preserves_busy_guard():
    source=(desktop.SOURCE_ROOT/'packaging/Install.ps1').read_text(encoding='utf-8')
    assert 'CloseMainWindow()' in source and 'WaitForExit(30000)' in source
    assert 'Stop-Process' not in source
    assert source.index('WaitForExit(30000)') < source.index('Expand-Archive')


def test_activation_does_not_use_windows_api_on_other_platforms(monkeypatch):
    monkeypatch.setattr(desktop.sys,'platform','linux')
    assert desktop.activate_existing_window(timeout=0) is False

def test_backend_shutdown_cancels_a_lingering_preview_stream(monkeypatch,tmp_path):
    import asyncio
    import socket
    import time
    from urllib.request import urlopen
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route
    import app.main as main

    async def health(request):
        return JSONResponse({'application':'areca-cutter','data_root':str(tmp_path)})
    async def stream(request):
        async def frames():
            while True:
                yield b'frame'
                await asyncio.sleep(.1)
        return StreamingResponse(frames())
    monkeypatch.setattr(main,'ROOT',tmp_path)
    monkeypatch.setattr(main,'app',Starlette(routes=[Route('/api/health',health),Route('/stream',stream)]))
    monkeypatch.setattr(desktop,'existing_backend',lambda *args:False)
    backend=desktop.Backend(tmp_path,0)
    backend.start()
    response=urlopen(backend.url+'/stream',timeout=10)
    assert response.read(5)==b'frame'
    started=time.monotonic()
    try:
        backend.stop()
        assert not backend.thread.is_alive()
        assert time.monotonic()-started < 15
        assert backend.server.config.timeout_graceful_shutdown==5
    finally:
        response.close()
