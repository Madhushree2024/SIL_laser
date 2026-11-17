from io import StringIO
import json
import hashlib
from pathlib import Path
import ezdxf
import pytest
from fastapi.testclient import TestClient
import app.main as main


def drawing(units=4, points=None, layer='CUT'):
    doc = ezdxf.new('R2010'); doc.units = units
    if layer != '0': doc.layers.new(layer)
    doc.modelspace().add_lwpolyline(points or [(10,10),(80,10),(80,60),(10,60)], close=True, dxfattribs={'layer':layer})
    stream = StringIO(); doc.write(stream)
    return stream.getvalue().encode('utf-8')


@pytest.fixture
def laser_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(main,'ROOT',tmp_path)
    config = {'workspace':{'width_mm':900,'height_mm':680},'laser_control':{'controller_ip':'192.168.8.8'}}
    monkeypatch.setattr(main,'load_config',lambda:config)
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda *a:{'status':'launch_requested','message':'isolated'})
    monkeypatch.setattr(main.lasercad_controller,'set_job',lambda path:None)
    return TestClient(main.app,base_url='http://127.0.0.1'),tmp_path,config


def upload(client, content=None):
    response = client.post('/api/laser-control/jobs/upload',files={'file':('sheath.dxf',content or drawing(),'application/dxf')})
    assert response.status_code == 200,response.text
    return response.json()


def test_import_preview_history_and_private_download(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client)
    assert job['validation']['valid']
    assert job['validation']['dimensions_mm']=={'width':70,'height':50}
    assert job['validation']['preview'][0]['closed']
    status=client.get('/api/laser-control/status').json()
    assert status['job']['id']==job['id']
    assert status['controller']['ip']=='192.168.8.8'
    assert status['controller']['connected'] is None
    assert status['monitoring']['progress_percent'] is None
    assert status['safety']['start_allowed'] is False
    assert hashlib.sha256(client.get(f"/api/laser-control/jobs/{job['id']}/file").content).hexdigest()==job['sha256']
    assert not (root/'data/laser_control_state').exists()
    assert (root/'laser_control_state/history.sqlite3').is_file()
    assert client.get('/api/laser-control/jobs').json()['jobs'][0]['started_at'] is None


def test_generated_dxf_reception_is_idempotent(laser_workspace):
    client,root,config=laser_workspace
    path=root/'data/exports/test.dxf';path.parent.mkdir(parents=True);path.write_bytes(drawing())
    output={'path':str(path)}
    main._handoff_dxf(output)
    assert output['laser_control']['status']=='validated'
    main._handoff_dxf({'path':str(path)})
    assert len(client.get('/api/laser-control/jobs').json()['jobs'])==1


def test_parameters_snapshot_and_stale_revision(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client);url=f"/api/laser-control/jobs/{job['id']}"
    assert client.post(url+'/prepare',json={'revision':1}).status_code==409
    params={**job['parameters'],'speed_mm_s':20,'max_power_percent':40,'passes':3}
    updated=client.post(url+'/parameters',json={'revision':1,'parameters':params})
    assert updated.status_code==200,updated.text
    assert client.post(url+'/prepare',json={'revision':1}).status_code==409
    prepared=client.post(url+'/prepare',json={'revision':2})
    assert prepared.status_code==200,prepared.text
    assert prepared.json()['status']=='conversion_required'
    assert prepared.json()['prepared']['transfer_ready'] is False
    assert prepared.json()['controller_job'] is None
    revised=client.post(url+'/parameters',json={'revision':2,'parameters':params}).json()
    assert revised['revision']==3 and revised['prepared'] is None


def test_tampered_file_blocks_preparation(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client)
    path=root/'laser_control_state/jobs'/job['id']/f"{job['id']}.dxf";path.write_bytes(b'changed')
    assert client.post(f"/api/laser-control/jobs/{job['id']}/prepare",json={'revision':1}).status_code==409
    stored=client.get(f"/api/laser-control/jobs/{job['id']}").json()
    assert stored['last_error'] and stored['prepared'] is None


def test_fallback_opens_exact_private_job_without_applying_parameters(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    job=upload(client);calls=[]
    monkeypatch.setattr(main,'open_dxf_in_lasercad',lambda path,cfg:calls.append((path,cfg)) or {'status':'launch_requested'})
    result=client.post(f"/api/laser-control/jobs/{job['id']}/fallback",json={})
    assert result.status_code==200,result.text
    assert result.json()['parameters_applied'] is False
    assert hashlib.sha256(calls[0][0].read_bytes()).hexdigest()==job['sha256']
    assert calls[0][1]['lasercad']['auto_frame_after_dxf'] is False
    assert job['id'] in calls[0][0].name
    assert client.get('/api/laser-control/jobs').json()['jobs'][0]['completed_at'] is None


@pytest.mark.parametrize('action',['connect','disconnect','transfer','start','pause','resume','stop'])
def test_unverified_commands_are_blocked_and_audited(laser_workspace,action):
    client,root,config=laser_workspace
    job=upload(client)
    route='/api/laser-control/stop' if action=='stop' else '/api/laser-control/command'
    response=client.post(route,json={'action':action,'job_id':job['id']})
    assert response.status_code==409
    events=client.get(f"/api/laser-control/jobs/{job['id']}/events").json()['events']
    assert events[-1]['kind']=='command_blocked'
    assert client.get('/api/laser-control/jobs').json()['jobs'][0]['started_at'] is None


def test_stop_bypasses_preparation_lock_but_cannot_send_other_commands(laser_workspace):
    client,root,config=laser_workspace
    assert main.operation_lock.acquire(blocking=False)
    try:
        assert client.post('/api/laser-control/stop',json={'action':'start'}).status_code==400
        response=client.post('/api/laser-control/stop',json={'action':'stop'})
        assert response.status_code==409 and 'protocol' in response.json()['detail']
    finally:
        main.operation_lock.release()


def test_profiles_are_versioned_without_affecting_job(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client)
    body={'name':'Areca 2.5 mm','parameters':job['parameters']}
    assert client.post('/api/laser-control/profiles',json=body).json()['revision']==1
    body['parameters']['max_power_percent']=30
    assert client.post('/api/laser-control/profiles',json=body).json()['revision']==2
    assert client.get('/api/laser-control/status').json()['job']['parameters']['max_power_percent'] is None


def test_invalid_upload_can_be_reviewed_but_not_prepared_or_opened(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client,b'not DXF')
    assert not job['validation']['valid'] and job['status']=='invalid'
    assert client.post(f"/api/laser-control/jobs/{job['id']}/fallback",json={}).status_code==409


def test_unknown_job_and_bad_ipv4_configuration(laser_workspace,monkeypatch):
    client,root,config=laser_workspace
    assert client.get('/api/laser-control/jobs/absent').status_code==404
    monkeypatch.setattr(main,'save_config',lambda value:pytest.fail('Invalid config must not save'))
    assert client.post('/api/config',json={'laser_control':{'controller_ip':'not-an-ip'}}).status_code==400


def test_saved_envelope_failure_is_revalidated_without_changing_coordinates(laser_workspace):
    client,root,config=laser_workspace
    job=upload(client,drawing(points=[(-654.83,-356.13),(-502.43,-356.13),(-502.43,-203.73),(-654.83,-203.73)]))
    job['validation'].pop('policy')
    job['validation']['valid']=False
    job['validation']['errors']=['Geometry is outside the old coordinate envelope.']
    job['status']='invalid'
    main.laser_control_service.history().save(job,'legacy_validation')
    refreshed=client.get('/api/laser-control/status').json()['job']
    assert refreshed['validation']['valid'] and not refreshed['validation']['errors']
    assert refreshed['status']=='validated' and refreshed['revision']==job['revision']+1
    assert refreshed['validation']['bounds_mm']['min_x']==pytest.approx(-654.83)
    assert refreshed['sha256']==job['sha256']
