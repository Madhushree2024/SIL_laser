"""Native UI dispatch for explicit operator steps; no inferred completion."""
from pathlib import Path
import logging, re
from time import monotonic, sleep
from app.laser.lasercad_control import ControlError
from app.laser_control.lasercad_export_ui import NativeExportUI, normalize, usb_entry_matches, named_usb_entry

class DatumUI(NativeExportUI):
    def _identity(self):
        live = self.window_metadata(self.window['hwnd'])
        if not live or any(live[k] != self.window[k] for k in ('pid','executable','title')):
            raise ControlError('LaserCAD changed while preparing Datum. No further clicks were sent.')


def panel_identity(panel):
    return {k:panel[k] for k in ('window_id','process_id','window_title','communication_selection')}


def ensure_window(controller):
    # Launch only when there is no native LaserCAD main window at all. Ambiguous
    # or mismatched installations must be resolved by the operator.
    windows = [w for w in controller._api().windows() if w['title'].lower().startswith('lasercad')]
    if not windows: controller.open()
    until = monotonic()+20
    while True:
        try:return controller._window(job_scoped=False,require_ready=False)
        except ControlError:
            if windows or monotonic()>=until:raise
            sleep(.2)


def datum(controller, options, guard=lambda:None, *, host=None):
    if str(options.get('communication_mode','USB')).upper() != 'USB':
        raise ControlError('Manual Datum requires the configured USB machine selection.')
    if host is None:
        raise ControlError('Background Datum requires the desktop application. No Datum was sent.')
    target=str(options.get('usb_port','MachName:_AWC_D2713611'))
    if not named_usb_entry(target) and not re.fullmatch(r'COM[1-9][0-9]*',target,re.I):
        raise ControlError('Configure a USB machine name or COM port for manual Datum. No Datum was sent.')
    guard()
    window = ensure_window(controller)
    try:
        prepared=host({'action':'background','_background_window':window})
        if prepared.get('status')!='background':
            raise ControlError('Could not prepare LaserCAD in the background. No Datum was sent.')
        ui = DatumUI(controller,window,Path('.'),None)
        with ui:
            # Use the exact configured entry; never guess a USB port or open a
            # visible connection dialog from the Process Sheath screen.
            ui.select_usb(target,allow_mode_dialog=False)
            ui._main()
        panel=controller.status(job_scoped=False)
        if not panel.get('available'):raise ControlError(panel.get('message','LaserCAD not ready.'))
        if not any(usb_entry_matches(v,target) for v in panel.get('communication_selection',[])):
            raise ControlError('LaserCAD did not retain the configured USB selection. No Datum was sent.')
        if host({'action':'status'}).get('status')!='background':
            raise ControlError('Background preparation expired or the display changed. No Datum was sent.')
        guard()
        result=controller.request('datum',expected_panel=panel_identity(panel))
        return {**result,'communication_target':target,'background':True}
    finally:
        try:host({'action':'hide'})
        except Exception:
            # Do not turn an already delivered Datum into an apparent failure.
            # The clipped pane remains off screen and its lease will expire.
            logging.getLogger(__name__).exception('Could not release background Datum pane; do not automatically retry the command.')


def prepare_job_panel(controller, job, target):
    from app.export.lasercad_bed_mapping import verify_import_snapshot
    import hashlib
    path=Path(job['path'])
    if not controller.job or controller.job.resolve()!=path or hashlib.sha256(path.read_bytes()).hexdigest()!=job['sha256']:
        raise ControlError('The staged DXF changed. Do not run this cycle.')
    verify_import_snapshot(path,job['mapping'])
    window=controller._window(job_scoped=True,require_ready=False)
    if any(window[k]!=job['window'][k] for k in ('hwnd','pid','title')):
        raise ControlError('LaserCAD changed windows or documents. Start a new cycle after confirming machine idle.')
    ui=NativeExportUI(controller,window,path,job['sha256'])
    dialogs=ui._dialogs()
    if dialogs:
        # Space 2/3 is the operator acknowledgement of completed transfer, never
        # a success detection. Close only the idle, exact Download Document form.
        if len(dialogs)!=1 or normalize(dialogs[0]['title'])!='downloaddocument':
            raise ControlError('Close the LaserCAD prompt manually after reviewing it. No motion was requested.')
        dialog=dialogs[0]
        ids={c['control_id'] for c in ui.controls(dialog)}
        if not {1384,1015,1504,1779}.issubset(ids) or not ui.user.IsWindowEnabled(dialog['hwnd']):
            raise ControlError('Download is busy or its dialog is unrecognized. No motion was requested.')
        ui._one(dialog,1384,'button',['Download Document'])
        ui._post(dialog['hwnd'],0x0010)
        ui._closed(dialog)
    ui._main()
    panel=controller.status(job_scoped=True)
    if not panel.get('available') or not panel.get('job_matches') or not any(usb_entry_matches(v,target) for v in panel.get('communication_selection',[])):
        raise ControlError('The staged drawing or configured USB selection changed.')
    return panel_identity(panel)
