from pathlib import Path
from threading import RLock
from .cutting_parameters import CuttingParameters
from .dxf_job_manager import DxfJobManager
from .job_history import JobHistory
from .job_transfer import prepare_snapshot
from .machine_status import monitoring_status
from .material_profiles import MaterialProfiles
from .safety_interlocks import safety_status
from .trocen_connection import TrocenConnection
from .lasercad_jobs import job_controls
from .supervised_cycle import SupervisedCycles, CUTTING
from uuid import uuid4


class LaserControlService:
    def __init__(self, root_getter, config_getter, fallback_open, fallback_select, fallback_status=None, fallback_command=None):
        self.root_getter, self.config_getter = root_getter, config_getter
        self.fallback_open, self.fallback_select = fallback_open, fallback_select
        self.fallback_status = fallback_status or (lambda: {'available': False, 'actions': {}})
        self.fallback_command = fallback_command
        self.connection = TrocenConnection()
        self.lock = RLock()
        self.stores = {}
        self.layout_callback = None
        self.host_callback = None
        self.export_automation_status = lambda: {}
        self.cycle_session = str(uuid4())
        self.layout_status = {'status':'idle','message':'Split screen arranges Laser Control and the matching LaserCAD window side by side.'}

    def history(self):
        root = Path(self.root_getter()).resolve()
        if root not in self.stores:
            history = JobHistory(root)
            with history.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS selection(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.stores[root] = history
        return self.stores[root]

    def select(self, job_id):
        active = self.cycles().status()['active']
        if active and active.get('job_id'):
            self.cycles().guard_action('select', job_id)
        history = self.history()
        job = self.manager().refresh_validation(history.get(job_id))
        with history.connect() as db:
            db.execute("INSERT INTO selection VALUES ('current',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (job_id,))
        return job

    def current(self):
        history = self.history()
        with history.connect() as db:
            row = db.execute("SELECT value FROM selection WHERE key='current'").fetchone()
        return self.manager().refresh_validation(history.get(row[0])) if row else None

    def manager(self):
        return DxfJobManager(self.history(), self.config_getter())

    def cycles(self):
        return SupervisedCycles(self.history(), self.cycle_session)

    def receive(self, content, filename, source='upload', source_path=''):
        with self.lock:
            job = self.manager().receive(content, filename, source, source_path)
            return self.select(job['id'])

    def receive_generated(self, path, ui_request_id=None):
        path = Path(path).resolve()
        # The export hook supplies an existing file inside this project's data root.
        if not path.is_relative_to((Path(self.root_getter()) / 'data').resolve()):
            raise ValueError('Generated DXF is outside this project data folder.')
        job = self.receive(path.read_bytes(), path.name, 'generated', str(path))
        if ui_request_id is not None:
            job['ui_request_id'] = str(ui_request_id)
            self.history().save(job, 'export_request_registered', {'ui_request_id': str(ui_request_id)})
        return job

    def status(self):
        job = self.current()
        return {'controller': self.connection.status(self.config_getter()), 'monitoring': monitoring_status(),
                'safety': safety_status(), 'job': job, 'fallback_available': True,
                'desktop_layout_supported': self.layout_callback is not None,
                'supervised': self.cycles().status(),
                'desktop_layout_status': self.layout_status,
                'split_after_dxf': self.config_getter().get('laser_control', {}).get('split_after_dxf', True),
                'message': 'Offline job management is available. Direct controller integration awaits a verified Ethernet SDK/protocol.'}

    def host_status(self):
        if self.host_callback is None:
            return {'supported':False,'status':'unavailable','message':'The LaserCAD tab requires the Windows desktop application.'}
        return self.host_callback({'action':'status'})

    def host_view(self, payload):
        if self.host_callback is None:
            raise ValueError('The LaserCAD tab requires the Windows desktop application.')
        return {**self.host_callback(payload), 'automation': self.export_automation_status()}

    def controls_status(self):
        return job_controls(self.current(), self.manager(), self.fallback_status())

    def layout(self, action):
        try:
            if self.config_getter().get('laser_control', {}).get('dashboard_enabled', True) is False:
                raise ValueError('Split view is temporarily disabled. Use the full-window LaserCAD tab.')
            if self.layout_callback is None:
                raise ValueError('Split view requires the Windows desktop application.')
            if action == 'split' and not self.controls_status()['matching_job']:
                raise ValueError('Open the selected DXF in LaserCAD before arranging split view.')
            self.layout_status = self.layout_callback(action)
            return self.layout_status
        except (ValueError, OSError) as exc:
            self.layout_status = {'status':'unavailable','message':str(exc)}
            raise

    def update_parameters(self, job_id, parameters: CuttingParameters, revision):
        with self.lock:
            self.cycles().guard_action('edit', job_id)
            job = self.history().get(job_id)
            self.check_revision(job, revision)
            job.update(parameters=parameters.model_dump(), revision=job['revision']+1, prepared=None,
                       status='validated' if job['validation']['valid'] else 'invalid', last_error=None)
            return self.history().save(job, 'parameters_saved', {'revision': job['revision'], 'parameters': job['parameters']})

    @staticmethod
    def check_revision(job, revision):
        if job['revision'] != revision:
            raise ValueError('This job changed in another session. Refresh and review its current revision.')

    def prepare(self, job_id, revision):
        with self.lock:
            job = self.history().get(job_id)
            self.check_revision(job, revision)
            try:
                # Validate against current bed settings without changing the DXF.
                from .dxf_validator import validate_dxf
                job['validation'] = validate_dxf(self.manager().path(job), self.config_getter())
                snapshot = prepare_snapshot(job, CuttingParameters(**job['parameters']))
            except (ValueError, OSError) as exc:
                job.update(last_error=str(exc), prepared=None)
                self.history().save(job, 'preparation_failed', {'error': str(exc)})
                raise
            job.update(prepared=snapshot, status='conversion_required', last_error=None)
            return self.history().save(job, 'preparation_snapshot', snapshot)

    def open_fallback(self, job_id):
        with self.lock:
            self.cycles().guard_action('open', job_id)
            job = self.history().get(job_id)
            from .dxf_validator import validate_dxf
            path = self.manager().path(job)
            validation = validate_dxf(path, self.config_getter())
            if not validation['valid']:
                raise ValueError('DXF validation failed: ' + ' '.join(validation['errors']))
            config = self.config_getter()
            if config.get('workspace',{}).get('coordinate_frame')=='id12_negative_xy':
                # The immutable snapshot lacks the source photo-position sidecar.
                original=Path(job.get('source_path') or '').resolve()
                if (job.get('source')!='generated' or not original.is_relative_to((Path(self.root_getter())/'data').resolve())
                    or not original.is_file()):
                    raise ValueError('This job has no original photo-position reference. Capture, nest and export a fresh job.')
                import hashlib
                if hashlib.sha256(original.read_bytes()).hexdigest()!=job['sha256']:
                    raise ValueError('The source DXF changed. Capture, nest and export again.')
                path=original
            self.fallback_select(path)
            if self.host_callback is None: config = {**config, 'laser_control':{**config.get('laser_control', {}), 'host_lasercad':False}}
            result = self.fallback_open(path, {**config, 'lasercad': {
                **self.config_getter().get('lasercad', {}), 'auto_open_dxf': True, 'auto_frame_after_dxf': False}})
            self.history().save(job, 'lasercad_handoff', {'result': result, 'parameters_applied': False})
            return {'handoff': result, 'parameters_applied': False,
                    'message': 'DXF handoff only. Verify speed, power, passes, layers and origin in LaserCAD; dashboard parameters were not applied. No machine command was sent.'}

    def command(self, action, job_id=None):
        if job_id:
            job = self.history().get(job_id)
            job['last_error'] = self.connection.status(self.config_getter())['blocker']
            self.history().save(job, 'command_blocked', {'action': action, 'reason': self.connection.status(self.config_getter())['blocker']})
        self.connection.command(action)

    def revalidate(self, job_id):
        with self.lock:
            job = self.history().get(job_id)
            from .dxf_validator import validate_dxf
            job['validation'] = validate_dxf(self.manager().path(job), self.config_getter())
            if not job['validation']['valid']:
                job.update(status='invalid', prepared=None)
            elif job['status'] == 'invalid':
                job['status'] = 'validated'
            return self.history().save(job, 'revalidated', {'valid': job['validation']['valid']})

    def request_fallback_command(self, action, job_id, request_id, operator_confirmed=False,
                                 revision=None, dxf_sha256=None, observed_state=None, window_title=None, communication_selection=None):
        if action not in {'download', 'frame', 'stop', 'start', 'pause', 'resume'}:
            raise ValueError('Unsupported LaserCAD job action.')
        if action == 'stop':
            # Never delay a native Stop behind job validation or history I/O.
            if not self.fallback_command:
                raise ValueError('LaserCAD bridge is unavailable.')
            result = self.fallback_command('stop', request_id)
            try:
                if job_id:
                    self.history().save(self.history().get(job_id), 'lasercad_job_request', {'action':'stop','request_id':str(request_id),'result':result})
            except Exception:
                result = {**result,'history_warning':'Stop delivery was requested, but its history could not be saved. Inspect the machine.'}
            return {**result,'mode':'lasercad','controller_acknowledged':False}
        job = self.history().get(job_id) if job_id else None
        self.cycles().guard_action(action, job_id)
        active = self.cycles().status()['active']
        if action == 'download' and active and active['state'] == 'reviewed':
            self.cycles().mutate(active['id'], {'reviewed'}, 'review_invalidated', {'state':'awaiting_review','review':None})
        controls = job_controls(job, self.manager(), self.fallback_status())
        if not self.fallback_command or not controls['actions'].get(action):
            raise ValueError('LaserCAD action unavailable. Open the matching DXF and its Control Panel, then refresh. No request was sent.')
        if action in {'start','pause','resume'}:
            if not job or not operator_confirmed:
                raise ValueError('Explicit operator confirmation is required for this job action.')
            self.check_revision(job, revision)
            if dxf_sha256 != job['sha256'] or window_title != controls['window_title']:
                raise ValueError('The confirmed job or LaserCAD window changed. Review and confirm again.')
            if communication_selection != controls['communication_selection']:
                raise ValueError('LaserCAD communication selection changed. Review the machine selection and confirm again.')
            if action == 'pause' and observed_state != 'running':
                raise ValueError('Pause uses a toggle: the operator must confirm the machine is currently running.')
            if action == 'resume' and observed_state != 'paused':
                raise ValueError('Resume uses a toggle: the operator must confirm the machine is currently paused.')
        try:
            cycle = self.cycles().before_start(job, controls, request_id) if action == 'start' else None
            if action in {'start','pause','resume'}:
                if cycle:
                    result = self.fallback_command(action, request_id, True, controls, cycle['id'])
                else:
                    result = self.fallback_command('pause' if action == 'resume' else action, request_id, True, controls)
            else:
                result = self.fallback_command(action, request_id)
            if not result.get('requested'):
                raise ValueError('Request delivery is unconfirmed. No automatic retry will be made.')
            if cycle:
                self.cycles().requested(cycle['id'])
        except Exception as exc:
            if job:
                self.history().save(job, 'lasercad_job_request_unconfirmed', {'action': action, 'error': str(exc)})
            raise
        if job:
            self.history().save(job, 'lasercad_job_request', {'action': action, 'request_id': str(request_id), 'result': result,
                'operator_confirmed': operator_confirmed, 'operator_observed_state': observed_state,
                'revision': revision, 'dxf_sha256': dxf_sha256, 'window_title': window_title,
                'communication_selection': communication_selection})
        return {**result, 'mode': 'lasercad', 'controller_acknowledged': False,
                'message': result.get('message', '') + ' Dashboard cutting parameters have not been applied to LaserCAD.'}

    def review_cycle(self, cycle_id, revision, digest, title, modes, actor):
        cycle = self.cycles().status()['active']
        if not cycle or cycle['id'] != cycle_id or not cycle.get('job_id'):
            raise ValueError('No prepared supervised cycle is selected.')
        job = self.history().get(cycle['job_id'])
        controls = self.controls_status()
        self.check_revision(job, revision)
        if not job['validation']['valid'] or not controls['matching_job'] or job['sha256'] != digest or controls['window_title'] != title or controls['communication_selection'] != modes:
            raise ValueError('The selected DXF or LaserCAD machine/window changed. Refresh and review again.')
        return self.cycles().review(cycle_id, job, controls, actor)

    def profiles(self):
        return MaterialProfiles(self.history())

    def recent(self):
        return [{key: job[key] for key in ('id','filename','status','revision','created_at','updated_at','parameters','started_at','completed_at','execution_seconds','last_error')}
                for job in self.history().recent()]
