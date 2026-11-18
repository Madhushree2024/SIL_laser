"""Persisted operator-stepped cycle. A UI click is never machine telemetry."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib, json, os
from pathlib import Path
from threading import RLock
from uuid import uuid4

READY = {'ready_process':'Refresh camera and capture through Download Document',
         'ready_start':'Start'}

class SpaceCycle:
    def __init__(self, root_getter):
        self.root_getter = root_getter
        self.lock = RLock()
        self.loaded_path = None
        self.state = None
        self.reset_storage_uncertain = False

    def _load(self):
        path = Path(self.root_getter())/'laser_control_state/space-cycle.json'
        if self.loaded_path == path: return
        self.loaded_path = path
        if path.exists():
            try:
                self.state = json.loads(path.read_text(encoding='utf-8'))
                legacy_idle = (self.state['stage'] == 'ready_datum' or (self.state['stage'] == 'ready_process' and self.state.get('sequence') == 'space-only-v1')) and not self.state.get('job')
                current_idle = (self.state['stage'] == 'ready_process' and not self.state.get('job')
                                and self.state.get('sequence') == 'space-start-tab-v2')
                if legacy_idle:
                    # Upgrade only an idle record. A pending or interrupted action
                    # must still require explicit operator recovery.
                    self.state = self._fresh()
                    self._save('two_space_tab_sequence_upgraded')
                elif not current_idle:
                    self.state.update(stage='interrupted', message='App restarted during a cycle. Confirm the machine is idle before resetting.')
                    self.state['revision'] += 1
                    self._save('restart_interrupted')
            except (ValueError, KeyError, TypeError):
                self.state = self._fresh('interrupted')
                self.state['message'] = 'Cycle record could not be read. Confirm machine idle before resetting.'
                self._save('unreadable_record')
        else:
            self.state = self._fresh()
            self._save('initialized')

    def _fresh(self, stage='ready_process'):
        return dict(cycle_id=str(uuid4()), revision=0, stage=stage, job=None, message='', sequence='space-start-tab-v2',
                    physical_completion_verified=False)

    def _save(self, event, *, snapshot=None):
        saved = self.state if snapshot is None else snapshot
        self.loaded_path.parent.mkdir(parents=True, exist_ok=True)
        record = {**deepcopy(saved), 'event':event, 'time':datetime.now(timezone.utc).isoformat()}
        if snapshot is not None: record['persistence'] = 'prepared_before_atomic_replace'
        # Journal before publishing state: no action may be dispatched if persistence fails.
        with (self.loaded_path.parent/'space-cycle-events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record)+'\n'); stream.flush(); os.fsync(stream.fileno())
        temporary = self.loaded_path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(saved,stream,indent=2); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(self.loaded_path)

    def status(self):
        with self.lock:
            self._load()
            return {**deepcopy(self.state), 'next_action':READY.get(self.state['stage'])}

    def _check(self, cycle_id, revision):
        self._load()
        if self.state['cycle_id'] != cycle_id or self.state['revision'] != revision:
            raise ValueError('This Space press is stale or already used. Refresh cycle status.')

    def claim(self, cycle_id, revision, stage, request_id, *, workflow_mode="automated"):
        with self.lock:
            self._check(cycle_id,revision)
            if stage not in READY: raise ValueError('This action is not part of the current two-Space workflow.')
            if self.state['stage'] != stage: raise ValueError('This stage cannot run now.')
            pending = {'ready_process':'processing',
                       'ready_start':'start_pending'}[stage]
            if stage=='ready_process':self.state['workflow_mode']=workflow_mode
            self.state.update(stage=pending, revision=revision+1, request_id=str(request_id), message='Request pending; repeated Space presses are ignored.')
            self._save(stage+'_claimed')
            return deepcopy(self.state)

    def finish(self, pending, next_stage, **updates):
        with self.lock:
            self._load()
            if self.state['stage'] != pending: raise ValueError('Cycle was interrupted; no next step is authorized.')
            self.state.update(stage=next_stage, revision=self.state['revision']+1, **updates)
            self._save(next_stage)
            return self.status()

    def interrupt(self, message):
        with self.lock:
            self._load()
            if self.state['stage'] == 'ready_process' and self.state.get('job') is None: return
            self.state.update(stage='interrupted',revision=self.state['revision']+1,message=str(message))
            self._save('interrupted')

    def reset(self, cycle_id, revision, completed, operator_confirmed_idle):
        with self.lock:
            self._check(cycle_id,revision)
            if not operator_confirmed_idle: raise ValueError('Confirm the machine is idle.')
            expected = {'awaiting_completion','awaiting_manual_completion'} if completed else {'interrupted'}
            if self.state['stage'] not in expected: raise ValueError('Wait for the current action, or interrupt the cycle before resetting.')
            if self.reset_storage_uncertain:
                raise ValueError('Reset storage could not be verified. Restore storage access and reopen the app before retrying.')
            before = deepcopy(self.state)
            self._save('operator_confirmed_cutting_complete' if completed else 'operator_confirmed_idle_recovery')
            candidate = self._fresh()
            candidate['reset_from'] = {'cycle_id':cycle_id, 'revision':revision,
                                       'completed':bool(completed), 'operator_confirmed_idle':True}
            try:
                # Persist the candidate before exposing ready_process to any caller.
                self._save('reset', snapshot=candidate)
            except Exception:
                # Reconcile an uncertain outcome (e.g. an exception after replace).
                # Never publish ready_process unless that exact record is durable.
                try: durable = json.loads(self.loaded_path.read_text(encoding='utf-8'))
                except (OSError, ValueError): durable = None
                if durable == candidate:
                    self.state = candidate
                    return self.status()
                self.state = before
                self.reset_storage_uncertain = durable != before
                raise
            self.state = candidate
            return self.status()


def config_fingerprint(root, config):
    chosen = {k:config.get(k) for k in ('camera','workspace','calibration','lasercad','machine_workflow')}
    digest = hashlib.sha256(json.dumps(chosen,sort_keys=True,default=str).encode())
    for path in sorted((Path(root)/'data/calibration').glob('*')):
        if path.is_file(): digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()
