from pathlib import Path
from types import SimpleNamespace
import hashlib
import pytest
from fastapi import HTTPException
import app.export.lasercad as cad
import app.main as main
from test_automated_workflow import workspace, payload, finish

@pytest.fixture(autouse=True)
def isolate_preview(monkeypatch):
    monkeypatch.setattr(cad,'prepare_import_coordinates',lambda *a:{'status':'configured','mode':'None'})
    monkeypatch.setattr(cad,'prepare_lasercad_preview',lambda *a,**kw:{'status':'refresh_requested','activated':True,'redraw_requested':True})

@pytest.fixture
def dxf(tmp_path):
    path=tmp_path/'exported files'/'layout & spaces.dxf'
    path.parent.mkdir();path.write_text('saved DXF geometry')
    return path


def test_launch_passes_exact_saved_file_without_shell_or_geometry_changes(dxf,tmp_path,monkeypatch):
    exe=tmp_path/'Laser CAD'/'LaserCAD.exe';exe.parent.mkdir();exe.write_bytes(b'fake test executable; never executed')
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda configured:exe)
    calls=[]
    monkeypatch.setattr(cad.subprocess,'Popen',lambda args,**kw:calls.append((args,kw)) or SimpleNamespace(pid=123))
    before=hashlib.sha256(dxf.read_bytes()).hexdigest()
    result=cad.open_dxf_in_lasercad(dxf,{})
    assert result['status']=='launch_requested' and result['pid']==123
    assert calls[0][0]==[str(exe),str(dxf.resolve())]
    assert calls[0][1]['shell'] is False
    assert hashlib.sha256(dxf.read_bytes()).hexdigest()==before


def test_not_installed_preserves_export_and_does_not_launch(dxf,monkeypatch):
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda path:None)
    monkeypatch.setattr(cad.subprocess,'Popen',lambda *a,**k:pytest.fail('Must not launch'))
    result=cad.open_dxf_in_lasercad(dxf,{})
    assert result['status']=='not_installed' and dxf.is_file()
    assert 'Install LaserCAD' in result['message']


@pytest.mark.parametrize('options',[{'auto_open_dxf':False},{'auto_open_dxf':'false'},'invalid'])
def test_disabled_and_invalid_options_do_not_launch(dxf,options,monkeypatch):
    monkeypatch.setattr(cad.subprocess,'Popen',lambda *a,**k:pytest.fail('Must not launch'))
    status=cad.open_dxf_in_lasercad(dxf,{'lasercad':options})['status']
    assert status==('disabled' if options=={'auto_open_dxf':False} else 'warning')


def test_bad_paths_and_launch_errors_return_warning(dxf,tmp_path,monkeypatch):
    monkeypatch.setattr(cad.sys,'platform','win32')
    result=cad.open_dxf_in_lasercad(dxf,{'lasercad':{'executable':str(tmp_path/'not-cad.exe')}})
    assert result['status']=='warning'
    monkeypatch.setattr(cad,'find_lasercad',lambda path:tmp_path/'LaserCAD.exe')
    def fail(*a,**k):raise OSError('Launch denied')
    monkeypatch.setattr(cad.subprocess,'Popen',fail)
    assert cad.open_dxf_in_lasercad(dxf,{})['status']=='warning'
    assert cad.open_dxf_in_lasercad(tmp_path/'missing.dxf',{})['status']=='warning'


def test_registry_and_standard_install_detection(tmp_path,monkeypatch):
    exe=tmp_path/'LaserCAD'/'AWCPrograms'/'LaserCAD.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'test')
    monkeypatch.setattr(cad,'_registered_executables',lambda:iter([str(exe)]))
    assert cad.find_lasercad()==exe.resolve()
    monkeypatch.setattr(cad,'_registered_executables',lambda:iter([]))
    monkeypatch.setenv('ProgramW6432',str(tmp_path))
    monkeypatch.setenv('SystemDrive',str(tmp_path))
    assert cad.find_lasercad()==exe.resolve()
    assert cad.find_lasercad(str(exe))==exe.resolve()


def test_svg_and_non_windows_do_not_launch(dxf,monkeypatch):
    monkeypatch.setattr(cad,'find_lasercad',lambda path:pytest.fail('Must not discover'))
    assert cad.open_dxf_in_lasercad(dxf.with_suffix('.svg'),{})['status']=='skipped'
    monkeypatch.setattr(cad.sys,'platform','linux')
    assert cad.open_dxf_in_lasercad(dxf,{})['status']=='unavailable'


def install_layout(root,monkeypatch):
    monkeypatch.setattr(main,'ROOT',root)
    monkeypatch.setattr(main,'get_laser_alignment',lambda:{'enabled':False})
    monkeypatch.setattr(main,'STATE',{'project_dir':str(root),'sheath_polygon':[[0,0],[50,0],[50,50],[0,50]],
        'nesting_result':{'placements':[{'template_id':'x','polygon':[[1,1],[10,1],[10,10],[1,10]]}]}})
    monkeypatch.setattr(main,'WORKFLOW',{'busy':False})


def test_manual_handoff_occurs_after_dxf_csv_save_not_svg(tmp_path,monkeypatch):
    install_layout(tmp_path,monkeypatch);calls=[]
    def open_file(path,config):
        assert Path(path).is_file() and Path(path).with_suffix('.csv').is_file()
        calls.append(path);return {'status':'launch_requested','message':'Sent to LaserCAD'}
    monkeypatch.setattr(main,'open_dxf_in_lasercad',open_file)
    output=main.export(main.ExportRequest(format='dxf'))
    assert len(calls)==1 and output['lasercad']['status']=='launch_requested'
    main._handoff_dxf(output);assert len(calls)==1
    assert 'lasercad' not in main.export(main.ExportRequest(format='svg'))
    assert len(calls)==1


def test_failed_csv_export_never_launches_lasercad(tmp_path,monkeypatch):
    install_layout(tmp_path,monkeypatch)
    def fail(*a,**k):raise OSError('Disk full')
    monkeypatch.setattr(main,'write_csv_report',fail)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *a:pytest.fail('Must not launch'))
    with pytest.raises(HTTPException):
        main.export(main.ExportRequest(format='dxf'))


def test_automatic_handoff_once_after_daily_record_and_polling_never_relaunches(workspace,monkeypatch):
    client,manager,root=workspace;calls=[];recorded=[]
    original=main._save_daily_production
    def record(status):
        value=original(status);recorded.append(value);return value
    def open_file(path,config):
        assert recorded and Path(recorded[0]['path']).is_file()
        assert main.WORKFLOW['busy'];calls.append(path)
        return {'status':'not_installed','message':'DXF saved; install LaserCAD.'}
    monkeypatch.setattr(main,'_save_daily_production',record)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',open_file)
    assert client.post('/api/workflow',json=payload(manager)).status_code==202
    result=finish(client)
    assert result['stage']=='Completed' and len(calls)==1
    assert result['result']['export']['lasercad']['status']=='not_installed'
    for _ in range(5):assert client.get('/api/workflow').status_code==200
    assert len(calls)==1 and not main.operation_lock.locked()


def test_failed_daily_record_defers_handoff_until_successful_retry(workspace,monkeypatch):
    client,manager,root=workspace;calls=[];original=main._save_daily_production
    def fail(status):raise OSError('Daily CSV locked')
    monkeypatch.setattr(main,'_save_daily_production',fail)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda path,config:calls.append(path) or {'status':'launch_requested','message':'Sent'})
    assert client.post('/api/workflow',json=payload(manager)).status_code==202
    result=finish(client);assert result['stage']=='Failed' and not calls
    monkeypatch.setattr(main,'_save_daily_production',original)
    retry=client.post('/api/production/retry',json={})
    assert retry.status_code==200 and retry.json()['workflow']['stage']=='Completed'
    assert len(calls)==1
    assert client.post('/api/production/retry',json={}).status_code==400
    assert len(calls)==1


def test_preview_failure_preserves_successfully_exported_dxf(dxf,tmp_path,monkeypatch):
    exe=tmp_path/'LaserCAD.exe';exe.write_bytes(b'not executed')
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda _:exe)
    monkeypatch.setattr(cad.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(pid=123))
    monkeypatch.setattr(cad,'prepare_lasercad_preview',lambda *a,**kw:{'status':'warning','message':'Window did not become ready'})
    before=dxf.read_bytes()
    r=cad.open_dxf_in_lasercad(dxf,{})
    assert r['status']=='warning' and r['pid']==123
    assert dxf.read_bytes()==before


def test_continuous_exports_are_serialized_through_preview(dxf,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    exe=tmp_path/'LaserCAD.exe';exe.write_bytes(b'not executed')
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda _:exe)
    launches=[];entered=Event();release=Event();second_started=Event()
    monkeypatch.setattr(cad.subprocess,'Popen',lambda *a,**kw:launches.append(a) or SimpleNamespace(pid=123))
    def prepare(*a):
        entered.set();assert release.wait(2)
        return {'status':'refresh_requested'}
    monkeypatch.setattr(cad,'prepare_lasercad_preview',prepare)
    def second():
        second_started.set();return cad.open_dxf_in_lasercad(dxf,{})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(cad.open_dxf_in_lasercad,dxf,{})
        try:
            assert entered.wait(1)
            later=pool.submit(second);assert second_started.wait(1)
            assert len(launches)==1
        finally:release.set()
        assert first.result()['status']=='launch_requested'
        assert later.result()['status']=='launch_requested'
    assert len(launches)==2
