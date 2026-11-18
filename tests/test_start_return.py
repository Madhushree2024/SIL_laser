from copy import deepcopy
import json
import pytest
from app.laser_control.space_cycle import SpaceCycle


def pending(tmp_path):
    cycle=SpaceCycle(lambda:tmp_path);s=cycle.status()
    cycle.claim(s['cycle_id'],s['revision'],'ready_process','capture')
    cycle.finish('processing','ready_start',job={'path':'job.dxf'})
    s=cycle.status();cycle.claim(s['cycle_id'],s['revision'],'ready_start','start')
    return cycle,s


def test_start_rearms_without_claiming_idle_or_completion(tmp_path):
    cycle,old=pending(tmp_path);s=cycle.return_after_start({'requested':True})
    assert s['stage']=='ready_process' and s['job'] is None
    assert s['cycle_id']!=old['cycle_id'] and s['physical_completion_verified'] is False
    assert s['started_from']['cycle_id']==old['cycle_id'] and s['started_from']['revision']==old['revision']
    assert 'reset_from' not in s and 'operator_confirmed_idle' not in s
    assert SpaceCycle(lambda:tmp_path).status()['cycle_id']==s['cycle_id']
    with pytest.raises(ValueError):cycle.return_after_start({'requested':True})
    events=(tmp_path/'laser_control_state/space-cycle-events.jsonl').read_text()
    assert 'operator_confirmed_cutting_complete' not in events
    cycle.claim(s['cycle_id'],s['revision'],'ready_process','next')
    assert cycle.status()['stage']=='processing'


def test_start_rearm_persistence_failure_does_not_publish_ready(tmp_path,monkeypatch):
    cycle,_=pending(tmp_path);before=deepcopy(cycle.state);save=cycle._save
    def fail(event,**kwargs):
        if event=='start_requested_return_to_process':raise OSError('disk full')
        return save(event,**kwargs)
    monkeypatch.setattr(cycle,'_save',fail)
    with pytest.raises(OSError):cycle.return_after_start({'requested':True})
    assert cycle.state==before and json.loads(cycle.loaded_path.read_text())==before


def test_exception_after_atomic_start_rearm_is_reconciled(tmp_path,monkeypatch):
    cycle,_=pending(tmp_path);save=cycle._save
    def fail_after(event,**kwargs):
        save(event,**kwargs)
        if event=='start_requested_return_to_process':raise OSError('after commit')
    monkeypatch.setattr(cycle,'_save',fail_after)
    assert cycle.return_after_start({'requested':True})['stage']=='ready_process'
    assert json.loads(cycle.loaded_path.read_text())==cycle.state
