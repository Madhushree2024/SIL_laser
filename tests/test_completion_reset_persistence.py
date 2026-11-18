from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pytest
from app.laser_control.space_cycle import SpaceCycle


def completed_cycle(tmp_path):
    cycle=SpaceCycle(lambda:tmp_path);cycle.status()
    cycle.state.update(stage='awaiting_completion',revision=5,job={'path':'job.dxf'},request_id='old-request')
    cycle._save('prepared_test')
    return cycle,deepcopy(cycle.state)


def reset(cycle,old):
    return cycle.reset(old['cycle_id'],old['revision'],True,True)


def test_reset_persists_receipt_before_publishing_fresh_state(tmp_path,monkeypatch):
    cycle,old=completed_cycle(tmp_path);original=cycle._save;seen=[]
    def save(event,**kwargs):
        if event=='reset':seen.append(deepcopy(cycle.state));assert cycle.state==old
        return original(event,**kwargs)
    monkeypatch.setattr(cycle,'_save',save)
    status=reset(cycle,old)
    assert seen==[old] and status['stage']=='ready_process' and status['job'] is None
    assert status['reset_from']=={'cycle_id':old['cycle_id'],'revision':old['revision'],'completed':True,'operator_confirmed_idle':True}
    assert json.loads(cycle.loaded_path.read_text())==cycle.state
    assert SpaceCycle(lambda:tmp_path).status()==status
    records=[json.loads(line) for line in cycle.loaded_path.with_name('space-cycle-events.jsonl').read_text().splitlines()]
    assert records[-2]['event']=='operator_confirmed_cutting_complete'
    assert records[-1]['event']=='reset' and records[-1]['persistence']=='prepared_before_atomic_replace'


@pytest.mark.parametrize('point',['approval','candidate'])
def test_save_failure_keeps_original_memory_and_durable_cycle(tmp_path,monkeypatch,point):
    cycle,old=completed_cycle(tmp_path);original=cycle._save
    def fail(event,**kwargs):
        if event==('operator_confirmed_cutting_complete' if point=='approval' else 'reset'):raise OSError('disk unavailable')
        return original(event,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(cycle,'_save',fail)
        with pytest.raises(OSError):reset(cycle,old)
    assert cycle.state==old and json.loads(cycle.loaded_path.read_text())==old
    assert reset(cycle,old)['stage']=='ready_process'


@pytest.mark.parametrize('after_replace',[False,True])
def test_atomic_replace_failure_is_reconciled_against_actual_disk(tmp_path,monkeypatch,after_replace):
    cycle,old=completed_cycle(tmp_path);original=Path.replace;calls=0
    def replace(path,target):
        nonlocal calls
        if path==cycle.loaded_path.with_suffix('.tmp'):
            calls+=1
            if calls==2:
                if after_replace:original(path,target)
                raise OSError('replace outcome uncertain')
        return original(path,target)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'replace',replace)
        if after_replace:
            result=reset(cycle,old);assert result['stage']=='ready_process'
        else:
            with pytest.raises(OSError):reset(cycle,old)
    assert cycle.state==json.loads(cycle.loaded_path.read_text())
    if after_replace:
        with pytest.raises(ValueError,match='stale'):reset(cycle,old)
    else:assert cycle.state==old


@pytest.mark.parametrize('failure_number',[3,4])
def test_reset_fsync_failure_never_publishes_uncommitted_ready_state(tmp_path,monkeypatch,failure_number):
    import app.laser_control.space_cycle as module
    cycle,old=completed_cycle(tmp_path);original=module.os.fsync;count=0
    def fsync(fd):
        nonlocal count
        count+=1
        if count==failure_number:raise OSError('fsync failed')
        return original(fd)
    with monkeypatch.context() as patch:
        patch.setattr(module.os,'fsync',fsync)
        with pytest.raises(OSError):reset(cycle,old)
    assert cycle.state==old and json.loads(cycle.loaded_path.read_text())==old


def test_unknown_storage_outcome_blocks_later_resets_until_reload(tmp_path,monkeypatch):
    cycle,old=completed_cycle(tmp_path);original=cycle._save;read=Path.read_text
    def fail(event,**kwargs):
        if event=='reset':raise OSError('write failed')
        return original(event,**kwargs)
    def unreadable(path,*args,**kwargs):
        if path==cycle.loaded_path:raise PermissionError('cannot reconcile')
        return read(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(cycle,'_save',fail);patch.setattr(Path,'read_text',unreadable)
        with pytest.raises(OSError):reset(cycle,old)
    assert cycle.state==old and cycle.reset_storage_uncertain
    with pytest.raises(ValueError,match='storage could not be verified'):reset(cycle,old)


def test_concurrent_duplicate_reset_has_one_committed_result(tmp_path):
    cycle,old=completed_cycle(tmp_path)
    def attempt(_):
        try:return reset(cycle,old)['cycle_id']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert sum(value is not None for value in results)==1
    assert cycle.state['stage']=='ready_process'
    records=[json.loads(line) for line in cycle.loaded_path.with_name('space-cycle-events.jsonl').read_text().splitlines()]
    assert sum(row['event']=='reset' for row in records)==1


def test_idle_confirmation_cannot_be_bypassed(tmp_path):
    cycle,old=completed_cycle(tmp_path)
    with pytest.raises(ValueError,match='Confirm the machine is idle'):cycle.reset(old['cycle_id'],old['revision'],True,False)
    assert cycle.state==old


from test_laser_control_api import laser_workspace

def test_reset_endpoint_returns_durable_receipt_and_rejects_replayed_request(laser_workspace,monkeypatch):
    import app.main as main
    client,root,config=laser_workspace
    client.get('/api/laser-control/space-cycle')
    main.space_cycle.state.update(stage='awaiting_completion',revision=5,job={'path':'job.dxf'})
    main.space_cycle._save('prepared_api_test')
    old=main.space_cycle.status()
    def forbidden(*args,**kwargs):raise AssertionError('Reset must not send a machine command')
    monkeypatch.setattr(main.lasercad_controller,'request',forbidden)
    payload={'cycle_id':old['cycle_id'],'revision':old['revision'],'completed':True,'operator_confirmed_idle':True}
    response=client.post('/api/laser-control/space-cycle/reset',json=payload)
    assert response.status_code==200,response.text
    current=response.json();assert current['stage']=='ready_process' and current['reset_from']==payload
    assert client.get('/api/laser-control/space-cycle').json()==current
    assert client.post('/api/laser-control/space-cycle/reset',json=payload).status_code==409
