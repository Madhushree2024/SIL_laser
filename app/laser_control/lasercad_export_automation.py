"""One-shot LaserCAD parameter setup and Download Document submission.

Stop after posting Download Document. Completion is not checked; no automatic
Run Box, Origin, movement, Start or success popup follows this submission.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import ipaddress
import re
import json
from pathlib import Path
import sqlite3
from threading import Lock

from app.laser.lasercad_control import ControlError
from .dxf_validator import validate_dxf
from .cut_recipe import cut_recipe
from app.export.lasercad_bed_mapping import verify_import_snapshot


class ExportAutomation:
    def __init__(self, controller, root_getter, config_getter, ui_factory=None, machine_workflow=None, photo_getter=None):
        self.controller, self.root_getter, self.config_getter = controller, root_getter, config_getter
        self.ui_factory = ui_factory
        self.machine_workflow=machine_workflow
        self.photo_getter=photo_getter or (lambda:None)
        self.lock = Lock()
        self.state_lock = Lock()
        self.last = {'status':'idle','message':'Automatic preparation has not run yet.'}

    def status(self):
        with self.state_lock: return deepcopy(self.last)

    def _publish(self, **values):
        with self.state_lock: self.last.update(values)
        return self.status()

    def _database(self):
        folder = Path(self.root_getter()) / 'laser_control_state'
        folder.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(folder/'export_automation.sqlite3',timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS attempts (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, document TEXT NOT NULL)')
        return db

    def run(self, path, handoff):
        config = self.config_getter()
        options = config.get('lasercad',{}).get('prepare_and_download',{})
        if not isinstance(options,dict) or options.get('enabled') is not True:
            return {'status':'disabled','message':'Automatic parameter setup and download are disabled.'}
        if not self.lock.acquire(blocking=False):
            return {'status':'blocked','message':'Another LaserCAD preparation is active. No second sequence was started.'}
        acquired = False
        db = None
        claimed = False
        key = str(Path(path).resolve())
        digest = None
        try:
            with self.state_lock: self.last={}
            self._publish(status='preparing',path=key,stage='Checking exported DXF',download_requested=False,download_may_have_been_requested=False,
                          controller_acknowledged=False,transfer_completed=False,lasercad_reported_success=False,message='Checking the exported DXF before LaserCAD preparation.')
            mode=str(options.get('communication_mode','USB')).strip().upper()
            if mode=='USB':
                target=str(options.get('usb_port','MachName:_AWC_D2713611')).strip()
                if re.fullmatch(r'COM[1-9][0-9]{0,4}',target,re.I):target=target.upper()
                elif not re.fullmatch(r'MachName:[A-Za-z0-9_.-]{1,80}',target):
                    raise ControlError('Enter the exact LaserCAD USB entry, for example MachName:_AWC_D2713611, or an explicitly configured COM port.')
                connection='USB '+target
            elif mode=='IP':
                target=str(ipaddress.IPv4Address(options.get('ip','192.168.8.8')))
                connection='IP '+target
            else:raise ControlError('Unsupported LaserCAD communication mode.')
            speed,power=cut_recipe(options)
            if handoff.get('status') != 'launch_requested' or handoff.get('preview',{}).get('status') != 'refresh_requested' or handoff.get('preview',{}).get('ready') is not True:
                raise ControlError('LaserCAD import readiness was not confirmed. No automatic setup or download was requested.')
            drawing = Path(key)
            # Preserve the existing preflight for a separately enabled relative
            # machine-positioning mode. No machine cycle follows submission.
            from .machine_workflow import enabled as motion_enabled
            if motion_enabled(config):
                if self.machine_workflow is None:raise ControlError('Verified controller feedback is required before automatic transfer or motion.')
                self.machine_workflow.pre_transfer(drawing,handoff.get('import_mapping'),self.photo_getter())
            verify_import_snapshot(drawing, handoff.get('import_mapping'))
            validation = validate_dxf(drawing,config)
            if not validation['valid']:
                raise ControlError('The exported DXF failed validation. No download was requested.')
            # The normal app exports only red CUT geometry. Never turn a reference
            # outline, a second colour or an imported engraving into a cutting job.
            import ezdxf
            doc = ezdxf.readfile(drawing)
            entities = list(doc.modelspace())
            if not entities or any(e.dxf.layer != 'CUT' or e.dxf.get('color',256) not in {1,256}
                                   or doc.layers.get(e.dxf.layer).dxf.color != 1 for e in entities):
                raise ControlError('Automatic download requires the app\'s single red CUT layer, without a SHEATH/reference layer.')
            digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
            acquired = self.controller.lock.acquire(blocking=False)
            if not acquired: raise ControlError('Another LaserCAD request is active. No automatic preparation was started.')
            if not self.controller.job or self.controller.job.resolve() != drawing:
                raise ControlError('The current exported DXF changed. No automatic setup was requested.')
            window = self.controller._window(job_scoped=True)
            db = self._database()
            with db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT document FROM attempts WHERE path=?',(key,)).fetchone()
                if existing:
                    previous=json.loads(existing[0])
                    return self._publish(status='already_attempted',message='This DXF already had an automatic preparation attempt. Download is never repeated automatically.',previous=previous)
                db.execute('INSERT INTO attempts VALUES (?,?,?)',(key,digest,json.dumps(self.status())))
                claimed = True
            if self.ui_factory:
                ui = self.ui_factory(self.controller,window,drawing,digest)
            else:
                from .lasercad_export_ui import NativeExportUI
                ui = NativeExportUI(self.controller,window,drawing,digest)
            def stage(text): self._publish(stage=text,message=text+'...')
            with ui:
                stage('Selecting '+connection+' communication')
                ui.select_communication(target)
                stage('Checking LaserCAD mm/s units without opening settings')
                ui.verify_speed_units()
                stage(f'Applying Cut / {speed:g} mm/s / {power:g}%')
                ui.configure_cut(speed,power)
                stage('Verifying communication and saved cutting parameters')
                ui.verify(target,speed,power)
                stage('Opening Download')
                ui.open_download()
                # Persist before the final transfer click. A crash/timeout must
                # never make the same export eligible for an automatic retry.
                self._publish(stage='Submitting Download Document',message='Submitting Download Document once.',download_may_have_been_requested=True)
                with db: db.execute('UPDATE attempts SET document=? WHERE path=?',(json.dumps(self.status()),key))
                verify_import_snapshot(drawing, handoff.get('import_mapping'))
                ui.download_document()
                return self._publish(status='download_requested',stage='Download Document requested',
                                     download_requested=True,transfer_completed=False,lasercad_reported_success=False,
                                     completion_verification='not_requested',
                                     transfer_confirmation={'status':'not_checked','reason':'Post-download verification is disabled by request.'},
                                     run_box={'status':'not_requested','requested':False},
                                     communication_mode=mode,communication_target=target,
                                     speed_mm_s=speed,power_percent=power,work_mode='Cut',
                                     message='Download requested: Download Document was triggered once. Start remains manual.')

        except Exception as exc:
            may_have_sent=self.status().get('download_may_have_been_requested',False)
            return self._publish(status='transfer_unconfirmed' if may_have_sent else 'blocked',transfer_completed=False,
                                 message=str(exc)+' Automatic sequence stopped; review LaserCAD before any manual retry.')
        finally:
            try:
                if db is not None:
                    try:
                        if claimed:
                            with db: db.execute('UPDATE attempts SET document=? WHERE path=?',(json.dumps(self.status()),key))
                    finally: db.close()
            finally:
                if acquired: self.controller.lock.release()
                self.lock.release()
