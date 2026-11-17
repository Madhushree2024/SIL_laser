"""Verified controller cycle. No guessed DLL calls or UI-as-telemetry fallback.

Production deliberately has no motion adapter until the supplier SDK is commissioned.
An adapter must provide fresh, command-correlated controller feedback, not text
read back from editable LaserCAD coordinate fields. No adapter is loaded from YAML.
"""
from copy import deepcopy
from pathlib import Path
from threading import RLock, Event
from time import monotonic, sleep
from uuid import uuid4
import hashlib
import json
import math
import sqlite3

from app.calibration.machine_placement import load_machine_placement
from app.export.lasercad_bed_mapping import verify_import_snapshot
from app.laser.lasercad_control import ControlError

REQUIRED = {'home','move_absolute','set_origin','frame','start','status','origin_readback','interlocks','job_identity','completion'}
BLOCKER = ('Automatic Datum / movement / Origin / Run Box / Start is not commissioned: '
           'a documented AWC7813 controller adapter with fresh position, origin, job, '
           'completion and interlock feedback is missing. LaserCAD UI clicks are not this feedback.')


def enabled(config):
    value=config.get('machine_workflow',{}).get('enabled',False)
    if not isinstance(value,bool):raise ControlError('machine_workflow.enabled must be true or false.')
    return value


class MachineWorkflow:
    def __init__(self,root_getter,config_getter,backend=None,clock=monotonic,pause=sleep):
        self.root_getter,self.config_getter,self.backend=root_getter,config_getter,backend
        self.clock,self.pause=clock,pause
        self.lock=RLock();self.cancelled=Event();self.current=None;self.published=None;self.last_sequence=-1
        self.guard=lambda:None

    def capabilities(self):
        return bool(self.backend is not None and getattr(self.backend,'commissioned',False) is True
                    and REQUIRED<=set(self.backend.capabilities))

    def status(self):
        config=self.config_getter()
        return {'enabled':enabled(config),'controller_feedback_available':self.capabilities(),
                'commissioned':self.capabilities(),'message':BLOCKER if not self.capabilities() else 'Controller adapter commissioned; each cycle still requires fresh feedback and a valid survey.',
                'cycle':deepcopy(self.published),'automatic_start_available':self.capabilities() and enabled(config)}

    def cancel(self):
        # Does not claim to stop hardware. Priority Stop is dispatched separately.
        self.cancelled.set()

    def _save(self):
        folder=Path(self.root_getter())/'laser_control_state';folder.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(folder/'machine_workflow.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS cycles (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO cycles VALUES (?,?)',(self.current['id'],json.dumps(self.current)))
        self.published=deepcopy(self.current)

    def _event(self,stage,**values):
        self.current.update(stage=stage,**values);self._save()

    def _settings(self):
        config=self.config_getter();options=config.get('machine_workflow',{})
        if not enabled(config):raise ControlError('Automatic machine workflow is disabled.')
        if not self.capabilities():raise ControlError(BLOCKER)
        mode=config.get('lasercad',{}).get('prepare_and_download',{})
        if mode.get('enabled') is not True or mode.get('communication_mode','USB')!='USB':
            raise ControlError('The automatic machine cycle requires confirmed automatic USB download.')
        if not isinstance(mode.get('run_box_after_success',True),bool):raise ControlError('Optional Run Box must be true or false.')
        target=mode.get('usb_port','MachName:_AWC_D2713611')
        if not isinstance(target,str) or not target.startswith('MachName:'):raise ControlError('The automatic machine cycle requires an exact named controller identity.')
        load_machine_placement(self.root_getter(),config)
        park=[float(options.get('park_x_mm',0)),float(options.get('park_y_mm',0))]
        width=float(config['workspace']['width_mm']);height=float(config['workspace']['height_mm'])
        if any(not math.isfinite(v) for v in park) or not 0<=park[0]<=width or not 0<=park[1]<=height:
            raise ControlError('Capture parking point is outside the configured machine bed.')
        return config,park,target

    def require_available(self):
        self._settings()
        return True

    def _state(self,homed=True,idle=True,job=None):
        if self.cancelled.is_set():raise ControlError('Machine cycle cancelled. No subsequent automatic commands allowed.')
        self.guard()
        config,_,target=self._settings()
        if self.current and 'config_sha256' in self.current and hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()!=self.current['config_sha256']:
            raise ControlError('Settings changed during the cycle. No subsequent command allowed.')
        state=self.backend.read_state()
        timestamp=state.get('observed_at');sequence=state.get('sequence')
        if (not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp) or not 0<=self.clock()-timestamp<=1
            or not isinstance(sequence,int) or sequence<=self.last_sequence):
            raise ControlError('Controller feedback is stale or repeated.')
        self.last_sequence=sequence
        if state.get('device')!=target or state.get('connected') is not True:raise ControlError('Controller identity or connection changed.')
        if state.get('safety_ready') is not True or state.get('alarm') is not False:raise ControlError('Controller interlocks or alarm state do not permit the cycle.')
        if homed and state.get('homed') is not True:raise ControlError('Machine is not homed.')
        if idle and (state.get('idle') is not True or state.get('moving') is not False):raise ControlError('Machine has not confirmed idle and stopped.')
        if self.current and 'epoch' in self.current and state.get('home_epoch')!=self.current['epoch']:
            raise ControlError('Machine reset/rehome changed the camera-to-machine reference.')
        if job is not None and (state.get('job_sha256')!=job or state.get('anchor')!='top_right' or state.get('origin_mode')!='key_origin'):
            raise ControlError('Controller job, top-right anchor or Key Origin mode is not verified.')
        return state

    @staticmethod
    def _near(values,target):
        return (isinstance(values,(list,tuple)) and len(values)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in values)
                and all(abs(a-b)<=0.1 for a,b in zip(values,target)))

    def _dispatch(self,action,**parameters):
        request_id=str(uuid4())
        # Persist before sending. An exception or restart must never retry a command.
        self.current.setdefault('commands',[]).append({'action':action,'request_id':request_id,'parameters':parameters,'outcome':'pending'})
        self._event(action+'_pending')
        if self.cancelled.is_set():raise ControlError('Cycle cancelled before dispatch.')
        self.guard()
        result=self.backend.command(action,request_id=request_id,**parameters)
        if not isinstance(result,dict) or result.get('request_id')!=request_id or result.get('accepted') is not True:
            raise ControlError(action+' acceptance is uncertain. No retry or next command allowed.')
        return request_id

    def _wait(self,request_id,*,homed=True,target=None,origin=None,job=None):
        deadline=self.clock()+60
        while True:
            state=self._state(homed=False,idle=False,job=job)
            if state.get('completed_command_id')==request_id and state.get('idle') is True and state.get('moving') is False:
                if homed and state.get('homed') is not True:raise ControlError('Homing completion was not confirmed.')
                if target is not None and not self._near(state.get('position_mm'),target):raise ControlError('Head did not reach the requested position.')
                if origin is not None and not self._near(state.get('origin_mm'),origin):raise ControlError('Saved job origin readback does not match.')
                self.current['commands'][-1]['outcome']='completed';self._save();return state
            if self.clock()>=deadline:raise ControlError('Controller completion timeout. No retry or subsequent command allowed.')
            self.pause(.1)

    def prepare_capture(self):
        with self.lock:
            config,park,_=self._settings()
            if self.current and self.current['stage'] not in {'completed','capture_failed'}:
                if self.current['stage']=='start_requested':
                    state=self._state(idle=True,job=self.current['job_sha256'])
                    if state.get('completed_command_id')!=self.current['start_request_id']:raise ControlError('Previous cut completion is not confirmed.')
                    self._event('completed')
                else:raise ControlError('An existing machine cycle needs recovery; no automatic retry.')
            # Unresolved persisted cycles survive process restart and block a new sequence.
            folder=Path(self.root_getter())/'laser_control_state';db_path=folder/'machine_workflow.sqlite3'
            if db_path.exists():
                with sqlite3.connect(db_path) as db:
                    rows=db.execute('SELECT document FROM cycles').fetchall()
                if any(json.loads(row[0]).get('stage') not in {'completed','capture_failed'} for row in rows):
                    raise ControlError('An earlier machine cycle has an unresolved outcome. Inspect the machine; automatic recovery/retry is disabled.')
            self.cancelled.clear();self.guard=lambda:None
            self.current={'id':str(uuid4()),'stage':'preflight','commands':[]}
            self._save()
            try:
                state=self._state(homed=False)
                command=self._dispatch('home')
                state=self._wait(command)
                if state.get('home_epoch') is None:raise ControlError('Controller did not identify the new homing reference.')
                self.current['epoch']=state['home_epoch']
                # Always request/confirm the parking position; reset may return to an old job origin.
                command=self._dispatch('move_absolute',position_mm=park)
                self._wait(command,target=park)
                self._event('parked',park_mm=park,config_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest())
                return self.current['id']
            except Exception as exc:
                self._event('interrupted',error=str(exc));raise

    def bind_capture(self,path):
        with self.lock:
            if not self.current or self.current['stage']!='parked':raise ControlError('No verified capture parking lease.')
            self._state()
            path=Path(path).resolve()
            self._event('captured',photo_path=str(path),photo_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def pre_transfer(self,path,mapping,photo):
        with self.lock:
            config,park,_=self._settings()
            if not self.current or self.current['stage']!='captured':raise ControlError('Capture must follow verified Datum and parking in this app session.')
            if hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()!=self.current['config_sha256']:
                raise ControlError('Settings changed after capture. No automatic transfer or motion.')
            photo=Path(photo).resolve()
            if str(photo)!=self.current['photo_path'] or hashlib.sha256(photo.read_bytes()).hexdigest()!=self.current['photo_sha256']:
                raise ControlError('The current photograph is not the parked capture for this cycle.')
            if not mapping or mapping.get('positioning_mode')!='key_origin_relative':raise ControlError('The downloaded geometry must be job-relative; absolute placement would apply the offset twice.')
            verify_import_snapshot(path,mapping)
            if not self._near(self._state().get('position_mm'),park):raise ControlError('Head moved away from capture parking before transfer.')
            return True

    def after_transfer(self,path,mapping,photo,transfer):
        with self.lock:
            self.pre_transfer(path,mapping,photo)
            if transfer.get('status')!='completed' or transfer.get('confirmation_source')!='lasercad_download_dialog':
                raise ControlError('Fresh successful Download Document confirmation is required.')
            target=mapping['machine_job_origin_mm'];job=mapping['import_sha256']
            config=self.config_getter();w=config['workspace']['width_mm'];h=config['workspace']['height_mm']
            if not self._near(target,target) or not 0<=target[0]<=w or not 0<=target[1]<=h:raise ControlError('Job origin is outside the bed.')
            self.current['job_sha256']=job
            self.guard=lambda:verify_import_snapshot(path,mapping)
            try:
                self._state(job=job)
                command=self._dispatch('move_absolute',position_mm=target)
                self._wait(command,target=target,job=job)
                self._state(job=job)
                command=self._dispatch('set_origin',position_mm=target)
                self._wait(command,target=target,origin=target,job=job)
                if config['lasercad']['prepare_and_download'].get('run_box_after_success',True):
                    self._state(job=job)
                    command=self._dispatch('frame')
                    self._wait(command,job=job)
                # Check origin and interlocks again after framing, immediately before Start.
                state=self._state(job=job)
                if not self._near(state.get('origin_mm'),target):raise ControlError('Origin changed before Start.')
                command=self._dispatch('start')
                self._event('start_requested',start_request_id=command)
                return {'status':'start_requested','requested':True,'message':'Start accepted once. Physical cutting completion is still pending controller confirmation.'}
            except Exception as exc:
                self._event('interrupted',error=str(exc));raise

    def preview(self):
        config=self.config_getter();options=config.get('machine_workflow',{})
        return {**self.status(),'no_commands_sent':True,'park_mm':[options.get('park_x_mm',0),options.get('park_y_mm',0)],
                'sequence':['Datum: wait for homing','Park: confirm position and stopped','Capture four markers and sheath','Nest and export local job (0,0)',
                            'Download Document: require fresh success','Move to surveyed machine job corner: confirm arrival',
                            'Set Key Origin: verify origin readback','Optional Run Box: wait for completion','Start once after job/interlock checks'],
                'blocker':None if self.capabilities() else BLOCKER}
