"""Opt-in operator observations. Never sends controller or LaserCAD commands."""
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic
from uuid import UUID, uuid4
import hashlib
import io
import json
import zipfile

ACTIONS = {'note','datum_pressed','homing_observed_complete','read_position_pressed',
           'move_pressed','arrival_observed','origin_pressed','download_pressed',
           'download_success_observed','run_box_pressed','framing_observed_complete',
           'start_pressed','pause_pressed','resume_pressed','stop_pressed',
           'cutting_observed_complete','alarm_observed'}
LIMIT = 64 * 1024 * 1024

def utc():
    return datetime.now(timezone.utc).isoformat()

def redact(value):
    """Exclude secret-bearing configuration keys; event notes remain operator data."""
    if isinstance(value,dict):
        return {k:('[redacted]' if any(word in str(k).lower() for word in
                 ('password','passwd','secret','token','credential','cookie','authorization'))
                 else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    return value

def atomic(path, value):
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)

class OperatorRecorder:
    def __init__(self, root_getter, context_getter, observer, interval=2):
        self.root_getter,self.context_getter,self.observer=root_getter,context_getter,observer
        self.interval=interval
        self.lock=RLock()
        self.active=None
        self.cancel=Event()
        self.worker=None
        self.recovered=set()

    def _base(self):
        base=Path(self.root_getter()).resolve()/'logs/operator_sessions'
        base.mkdir(parents=True,exist_ok=True)
        if base not in self.recovered:
            for folder in base.iterdir():
                if not folder.is_dir():continue
                try:
                    UUID(folder.name)
                    meta=json.loads((folder/'session.json').read_text(encoding='utf-8'))
                    if meta['state']=='recording':
                        meta.update(state='interrupted',ended_at=utc(),end_reason='Application restarted; recording was not resumed.')
                        atomic(folder/'session.json',meta)
                except (ValueError,KeyError,OSError):
                    continue
            self.recovered.add(base)
        return base

    def _folder(self,session_id):
        return self._base()/str(UUID(str(session_id)))

    def _append(self,folder,event):
        path=folder/'events.jsonl'
        if path.exists() and path.stat().st_size>LIMIT:
            raise ValueError('Recording event limit reached.')
        event={'timestamp_utc':utc(),**event}
        with path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
            stream.flush()

    def _context(self,folder):
        context=redact(self.context_getter())
        root=Path(self.root_getter()).resolve()
        artifacts=folder/'artifacts'
        artifacts.mkdir(exist_ok=True)
        candidates=[
            root/'camera_calibration.npz',
            root/'data/calibration/aruco_viewer_bed_layout.json',
            root/'data/calibration/machine_placement.json',
            root/'data/calibration/laser_alignment.json',
        ]
        # Only server-selected current-job files inside the workspace data folder.
        job=context.get('job',{})
        for name in ('path','source_path'):
            raw=job.get(name)
            if raw:
                p=Path(raw).resolve()
                if p.is_relative_to(root/'data'):
                    candidates += [p,p.with_suffix('.alignment.json')]
        files=[]
        for p in dict.fromkeys(candidates):
            if not p.is_file():continue
            if p.stat().st_size>10*1024*1024:
                files.append({'name':p.name,'skipped':'File exceeds 10 MB diagnostic limit.'})
                continue
            data=p.read_bytes()
            digest=hashlib.sha256(data).hexdigest()
            target=artifacts/(digest[:16]+'-'+p.name)
            if not target.exists():
                total=sum(x.stat().st_size for x in artifacts.iterdir() if x.is_file())
                if total+len(data)>LIMIT:
                    files.append({'name':p.name,'sha256':digest,'skipped':'Artifact limit reached.'})
                    continue
                target.write_bytes(data)
            files.append({'source_path':str(p),'saved_as':'artifacts/'+target.name,'sha256':digest})
        context={**context,'artifacts':files,'controller_telemetry_verified':False}
        encoded=json.dumps(context,sort_keys=True,allow_nan=False).encode()
        digest=hashlib.sha256(encoded).hexdigest()
        contexts=folder/'contexts';contexts.mkdir(exist_ok=True)
        target=contexts/(digest+'.json')
        if not target.exists():atomic(target,context)
        return 'contexts/'+target.name

    def start(self,actor,notes='',origin_mode='unknown',firmware=''):
        with self.lock:
            if self.active:raise ValueError('A recording is already active.')
            folder=self._folder(uuid4());folder.mkdir()
            meta={'schema_version':1,'id':folder.name,'state':'recording','started_at':utc(),
                  'started_by':actor,'notes':notes,'operator_reported_origin_mode':origin_mode,
                  'operator_reported_firmware':firmware,'folder':str(folder),
                  'sample_interval_seconds':self.interval,'maximum_duration_seconds':3600,
                  'limits':'Visible UI observations and manual event notes; no USB traffic, automatic click detection, or verified controller feedback.',
                  'commands_sent_by_recorder':False}
            atomic(folder/'session.json',meta)
            try:context=self._context(folder)
            except Exception as exc:
                meta.update(state='failed',ended_at=utc(),end_reason=str(exc))
                atomic(folder/'session.json',meta)
                raise
            self._append(folder,{'kind':'recording_started','actor':actor,'context_file':context})
            (folder/'READ ME.txt').write_text(
                'OPERATOR OBSERVATIONS - NOT VERIFIED CONTROLLER TELEMETRY\n'
                'session.json: recording metadata and operator-reported settings.\n'
                'events.jsonl: UTC timestamps, visible UI samples and manually recorded actions.\n'
                'contexts/: selected app settings, job identity and export automation snapshots.\n'
                'artifacts/: available DXF/alignment and calibration copies with content hashes.\n'
                'Position edit fields may be stale or operator-entered. Button enabled state does\n'
                'not prove readiness or physical completion. Static/status text may be incomplete.\n'
                'The recorder does not capture USB packets, inject clicks, press Start or move hardware.\n'
                'Use recorded_event entries to correlate manual actions; the recorder does not\n'
                'automatically detect every LaserCAD click. Clock is UTC; operator reports are labelled.\n'
                'Stop recording does not stop the machine. Local files are only shared by downloading\n'
                'and deliberately sending them. Account databases and unrelated apps are excluded.\n',
                encoding='utf-8')
            self.cancel=Event()
            self.active={'id':folder.name,'folder':folder,'began':monotonic(),'last':None,'last_saved':0}
            self.worker=Thread(target=self._run,args=(folder.name,self.cancel),daemon=True,name='operator-observation-recorder')
            self.worker.start()
            return meta

    def sample(self,session_id):
        # Do not hold the file lock while reading a potentially busy LaserCAD UI.
        try:observation=self.observer()
        except Exception as exc:observation={'available':False,'error':str(exc)}
        with self.lock:
            if not self.active or self.active['id']!=session_id:return
            folder=self.active['folder']
            context=self._context(folder)
            payload={'observation':observation,'context_file':context}
            signature=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
            now=monotonic()
            if signature!=self.active['last'] or now-self.active['last_saved']>=15:
                self._append(folder,{'kind':'visible_ui_sample','source':'LaserCAD visible UI, unverified',
                                     'elapsed_seconds':round(now-self.active['began'],3),**payload})
                self.active.update(last=signature,last_saved=now)

    def _run(self,session_id,cancel):
        while not cancel.is_set():
            try:
                self.sample(session_id)
                with self.lock:
                    if not self.active or self.active['id']!=session_id:return
                    expired=monotonic()-self.active['began']>=3600
                if expired:
                    self.stop('system','Maximum one-hour recording duration reached.')
                    return
            except Exception as exc:
                with self.lock:
                    if not self.active or self.active['id']!=session_id:return
                    self.stop('system','Recorder stopped after observation/write error: '+str(exc))
                return
            cancel.wait(self.interval)

    def event(self,session_id,actor,action,notes='',x=None,y=None):
        if action not in ACTIONS:raise ValueError('Unknown operator event.')
        with self.lock:
            if not self.active or self.active['id']!=str(session_id):
                raise ValueError('This recording is not active.')
            self._append(self.active['folder'],{'kind':'recorded_event','action':action,'actor':actor,
                'source':'operator_reported','notes':notes,'operator_reported_position_mm':{'x':x,'y':y}})
            return {'saved':True,'action':action,'machine_command_sent':False}

    def stop(self,actor,reason='Operator stopped recording.',session_id=None):
        with self.lock:
            if not self.active:raise ValueError('No recording is active.')
            if session_id is not None and self.active['id']!=session_id:
                raise ValueError('The active recording changed; refresh before stopping it.')
            folder=self.active['folder'];self.cancel.set()
            meta=json.loads((folder/'session.json').read_text(encoding='utf-8'))
            meta.update(state='stopped',ended_at=utc(),ended_by=actor,end_reason=reason)
            try:self._append(folder,{'kind':'recording_stopped','actor':actor,'reason':reason})
            finally:
                atomic(folder/'session.json',meta)
                self.active=None
            return meta

    def shutdown(self):
        with self.lock:
            if self.active:self.stop('system','Application closed; recording stopped.')

    def status(self):
        with self.lock:
            rows=[]
            for p in self._base().glob('*/session.json'):
                try:rows.append(json.loads(p.read_text(encoding='utf-8')))
                except (ValueError,OSError):continue
            rows.sort(key=lambda x:x.get('started_at',''),reverse=True)
            return {'active':self.active['id'] if self.active else None,'sessions':rows[:50],
                    'machine_commands_sent':False}

    def download(self,session_id):
        with self.lock:
            folder=self._folder(session_id)
            if not (folder/'session.json').is_file():raise KeyError('Recording not found.')
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                manifest={}
                for p in sorted(folder.rglob('*')):
                    if p.is_file() and not p.is_symlink():
                        data=p.read_bytes();relative=p.relative_to(folder).as_posix()
                        manifest[relative]=hashlib.sha256(data).hexdigest()
                        archive.writestr(relative,data)
                archive.writestr('sha256.json',json.dumps(manifest,indent=2))
            return output.getvalue()
