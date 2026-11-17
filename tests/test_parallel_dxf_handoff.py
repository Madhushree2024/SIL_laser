from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from uuid import uuid4
import app.main as main
from test_automated_workflow import workspace, payload, finish
from test_laser_control_api import laser_workspace
from test_lasercad import install_layout


def test_automatic_export_publishes_job_while_lasercad_preview_waits(workspace,monkeypatch):
    client,manager,root=workspace
    entered,release=Event(),Event()
    calls=[]
    def slow_lasercad(path,config):
        calls.append(path);entered.set()
        assert release.wait(10)
        return {'status':'launch_requested'}
    monkeypatch.setattr(main,'open_dxf_in_lasercad',slow_lasercad)
    try:
        assert client.post('/api/workflow',json=payload(manager)).status_code==202
        assert entered.wait(5)
        status=client.get('/api/workflow').json()
        assert status['busy'] and status['stage']=='Exporting'
        output=status['artifacts']['export']
        assert output['laser_control']['job_id']
        assert output['daily_production']
        assert 'lasercad' not in output
        assert client.get('/api/laser-control/status').json()['job']['id']==output['laser_control']['job_id']
        assert Path(output['path']).is_file()
    finally:
        release.set()
        completed=finish(client)
    assert completed['stage']=='Completed'
    assert len(calls)==1


def test_manual_export_has_matching_job_before_http_response_finishes(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    install_layout(root/'data/projects/manual-export',monkeypatch)
    monkeypatch.setattr(main,'ROOT',root)
    entered,release=Event(),Event()
    request_id=str(uuid4())
    def slow_lasercad(path,config):
        entered.set();assert release.wait(10)
        return {'status':'launch_requested'}
    monkeypatch.setattr(main,'open_dxf_in_lasercad',slow_lasercad)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(client.post,'/api/export',json={'format':'dxf','ui_request_id':request_id})
        try:
            assert entered.wait(5)
            job=client.get('/api/laser-control/status').json()['job']
            assert job['ui_request_id']==request_id
            assert Path(job['source_path']).is_file()
            assert not pending.done()
        finally:
            release.set()
        result=pending.result(5)
    assert result.status_code==200,result.text
    assert result.json()['laser_control']['job_id']==job['id']
