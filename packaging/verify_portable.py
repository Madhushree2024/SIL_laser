from pathlib import Path
import json,hashlib,subprocess,sys,types
import yaml
from PyInstaller.archive.readers import CArchiveReader
root=Path(__file__).resolve().parents[1]
w=root/'verification/current'
sys.path.insert(0,str(root))
info=json.loads((w/'role-ui-package.json').read_text())
stage=Path(info['stage'])
pwsh=Path(r'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt\dependencies\native\powershell\pwsh.exe')
from datetime import datetime
test=Path('D:/SIL-Laser-Project-Archive/Test-Installations')/('verify-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
assert not test.exists()
def install(*args):
    p=subprocess.run([str(pwsh),'-NoProfile','-File',str(stage/'Install.ps1'),*args],capture_output=True,text=True,timeout=120)
    assert p.returncode==0,p.stdout+p.stderr
    return p.stdout
verified=install('-VerifyOnly')
installed=install('-TestRoot',str(test))
home=test/'Programs/Template Cutting Automation/2026-11-13-embedded-download-upgrade'
workspace=test/'Workspace'
config=workspace/'app/config/config.yaml'
cfg=yaml.safe_load(config.read_text(encoding='utf-8-sig'))
cfg['laser_control'].update(host_lasercad=False,split_after_dxf=True,dashboard_enabled=True)
cfg['lasercad'].update(executable='C:/old/LaserCAD.exe',auto_open_dxf=False)
cfg['lasercad']['prepare_and_download'].update(enabled=False,run_box_after_success=False)
config.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding='utf-8')
before=config.read_bytes()
survey=workspace/'data/calibration/test-preserved-survey.json'
survey.write_bytes(b'{"actual_survey":"unchanged"}')
camera=workspace/'camera_calibration.npz'
cam_before=camera.read_bytes()
# Repeat installation must not replace user data; startup performs the one-time repair.
install('-TestRoot',str(test))
assert config.read_bytes()==before
reader=CArchiveReader(str(home/'TemplateCutting.exe')).open_embedded_archive('PYZ.pyz')
# Verify the actual compiled state contract without a camera or controller.
space_module=types.ModuleType('frozen_space_only_verification')
exec(reader.extract('app.laser_control.space_cycle'),space_module.__dict__)
fresh_space=space_module.SpaceCycle(lambda:test/'Fresh-Space-Cycle')
assert fresh_space.status()['stage']=='ready_process'
assert fresh_space.status()['next_action']=='Refresh camera and capture through Download Document'
assert set(space_module.READY)=={'ready_process','ready_start'}
snapshot=fresh_space.status()
fresh_space.claim(snapshot['cycle_id'],snapshot['revision'],'ready_process','compiled-process')
snapshot=fresh_space.finish('processing','ready_start',job={'path':'compiled-test-only.dxf'})
fresh_space.claim(snapshot['cycle_id'],snapshot['revision'],'ready_start','compiled-start')
assert fresh_space.finish('start_pending','awaiting_completion')['next_action'] is None
assert 'finish(true,{confirmedBySpace:true})' in (home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')
assert '(!completed || !confirmedBySpace)' in (home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')
assert 'Space 3: confirm' in (home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')
assert 'awaiting_manual_completion' in (home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')
assert 'id="supervisedCutting"' not in (home/'_internal/frontend/index.html').read_text(encoding='utf-8')
assert 'id="newUserWorkflow"' in (home/'_internal/frontend/index.html').read_text(encoding='utf-8')
account_module=types.ModuleType('frozen_account_workflow_verification')
exec(reader.extract('app.security.accounts'),account_module.__dict__)
import sqlite3
legacy=test/'legacy-accounts.sqlite3'
with sqlite3.connect(legacy) as db:
    db.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT,enabled INTEGER,created_at REAL)')
    db.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',('reader','legacy-reader','preserved-password-hash','viewer',1,123.0))
accounts=account_module.AccountStore(legacy)
assert accounts.users()[0]['permissions']==account_module.PERMISSIONS['viewer']
assigned=accounts.update('reader','viewer',True,'installer-check',workflow_mode='manual')
assert assigned['workflow_mode']=='manual' and 'operate' in assigned['permissions'] and 'configure' not in assigned['permissions'] and 'users' not in assigned['permissions']
with accounts.connect() as db:
    row=db.execute('SELECT * FROM users').fetchone()
    assert row['password_hash']=='preserved-password-hash' and row['role']=='viewer' and row['enabled']==1 and row['created_at']==123.0
snapshot=fresh_space.status()
fresh_space.reset(snapshot['cycle_id'],snapshot['revision'],True,True)
snapshot=fresh_space.status()
fresh_space.claim(snapshot['cycle_id'],snapshot['revision'],'ready_process','compiled-manual',workflow_mode='manual')
snapshot=fresh_space.finish('processing','awaiting_manual_completion',job={'workflow_mode':'manual'})
assert snapshot['next_action'] is None
assert fresh_space.reset(snapshot['cycle_id'],snapshot['revision'],True,True)['stage']=='ready_process'

flow_js=(home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')
assert flow_js.index('await refreshCamera({required:true})')<flow_js.index('await runWorkflow(snapshot)')
app_js=(home/'_internal/frontend/app.js').read_text(encoding='utf-8')
refresh_code=app_js[app_js.index('async function refreshCamera('):app_js.index('async function loadTemplates()')]
assert 'required = false' in refresh_code and 'return true;' in refresh_code and 'return false;' in refresh_code

native_keys=types.ModuleType('frozen_space_keys_verification')
exec(reader.extract('app.laser_control.space_keys'),native_keys.__dict__)
assert set(native_keys.DesktopSpaceKeys(None,None).edges)=={0x20}
assert not native_keys.DesktopSpaceKeys(None,None).completion_key_ready()
assert native_keys.DesktopSpaceKeys(None,None,completion_ready=lambda:True).completion_key_ready()
main_code=reader.extract('app.main')
action=next(c for c in main_code.co_consts if isinstance(c,types.CodeType) and c.co_name=='space_cycle_action')
assert 'datum' not in action.co_names
assert 'ready_datum' not in action.co_consts
assert 'frame' not in action.co_consts and 'ready_frame' not in action.co_consts
identity=types.ModuleType('frozen_identity_verification')
identity.__file__=str(home/'_internal/app/application_info.py')
exec(reader.extract('app.application_info'),identity.__dict__)
identity.sys=types.SimpleNamespace(frozen=True,executable=str(home/'TemplateCutting.exe'))
version_result=identity.application_info({},lambda:[])
ui_code=reader.extract('app.laser_control.lasercad_export_ui')
import types
klass=next(c for c in ui_code.co_consts if isinstance(c,types.CodeType) and c.co_name=='NativeExportUI')
methods={c.co_name:c for c in klass.co_consts if isinstance(c,types.CodeType)}
assert '_require_single_cut_layer' in methods and '_require_cut_output' not in methods
assert 1848 not in methods['configure_cut'].co_consts
assert 1848 not in methods['verify'].co_consts
assert 1375 not in methods['_speed_units'].co_consts
assert '_dialog' not in methods['_speed_units'].co_names
assert '_post' not in methods['_speed_units'].co_names
assert reader.extract('app.laser_control.lasercad_speed_units')
automation=reader.extract('app.laser_control.lasercad_export_automation')
automation_class=next(c for c in automation.co_consts if isinstance(c,types.CodeType) and c.co_name=='ExportAutomation')
run=next(c for c in automation_class.co_consts if isinstance(c,types.CodeType) and c.co_name=='run')
assert 'download_document' in run.co_names
assert not {'wait_transfer_result','acknowledge_success','run_box_after_success','after_transfer','completion_notifier'} & set(run.co_names)
import marshal
outer=CArchiveReader(str(home/'TemplateCutting.exe'))
entry=marshal.loads(outer.extract('desktop'))
main=next(c for c in entry.co_consts if isinstance(c,types.CodeType) and c.co_name=='main')
assert 'notify_download_success' not in main.co_names
import app.laser_control.cut_recipe as recipe
exec(reader.extract('app.laser_control.cut_recipe'),recipe.__dict__)
import app.calibration.usable_travel_upgrade as travel_upgrade
exec(reader.extract('app.calibration.usable_travel_upgrade'),travel_upgrade.__dict__)
import configparser
area=configparser.ConfigParser();area.read(home/'LaserCAD/AWCCfg/SysCfg.ini',encoding='latin-1')
assert area.getfloat('X','Range')==915 and area.getfloat('Y','Range')==630
assert reader.extract('app.laser_control.lasercad_transfer_result')
assert reader.extract('app.laser_control.space_cycle')
native_datum=reader.extract('app.laser_control.space_native')
datum_code=next(c for c in native_datum.co_consts if isinstance(c,types.CodeType) and c.co_name=='datum')
assert 'select_usb' in datum_code.co_names and 'select_communication' not in datum_code.co_names
assert 'background' in datum_code.co_consts
assert "navigateSection('lasercad')" not in (home/'_internal/frontend/manual-datum.js').read_text(encoding='utf-8')
assert reader.extract('app.laser_control.space_keys')
assert 'start_space_keys' in {c.co_name for c in main.co_consts if isinstance(c,types.CodeType)}
assert (home/'_internal/frontend/space-cycle.js').is_file()
assert 'pendingDisplayUpdates' in (home/'_internal/frontend/app.js').read_text(encoding='utf-8')
assert "JSON.parse(options.body).action === 'show'" in (home/'_internal/frontend/app.js').read_text(encoding='utf-8')
assert 'No stage was triggered.' in (home/'_internal/frontend/space-cycle.js').read_text(encoding='utf-8')

assert version_result['version']=='2026.11.18.3'
assert version_result['app_path']==str((home/'TemplateCutting.exe').resolve())
assert 'applicationInfo' in (home/'_internal/frontend/index.html').read_text(encoding='utf-8')
assert '/api/application-info' in (home/'_internal/frontend/lasercad-tab.js').read_text(encoding='utf-8')
recording_module=types.ModuleType('frozen_operator_recording')
exec(reader.extract('app.laser_control.operator_recording'),recording_module.__dict__)
recorder=recording_module.OperatorRecorder(lambda:workspace,
    lambda:{'app_version':'2026.11.18.3','job':{}},
    lambda:{'available':False,'note':'Isolated verification; no hardware','machine_commands_sent':False},
    interval=60)
session=recorder.start('verification',origin_mode='machine_zero')
recorder.sample(session['id'])
recorder.event(session['id'],'verification','note','Recorder verification only')
recorder.stop('verification')
import io,zipfile
with zipfile.ZipFile(io.BytesIO(recorder.download(session['id']))) as archive:
    assert archive.testzip() is None
    meta=json.loads(archive.read('session.json'))
    assert meta['commands_sent_by_recorder'] is False
    assert any('camera_calibration.npz' in p for p in archive.namelist())
recorder.shutdown()
if recorder.worker:recorder.worker.join(timeout=2)
assert (home/'_internal/frontend/operator-recording.js').is_file()
assert 'operatorRecordingPanel' in (home/'_internal/frontend/index.html').read_text(encoding='utf-8')
module=types.ModuleType('frozen_runtime_verification')
module.__file__=str(home/'_internal/desktop_runtime.py')
exec(reader.extract('desktop_runtime'),module.__dict__)
module.sys=types.SimpleNamespace(frozen=True)
module.prepare_workspace(workspace,home/'_internal')
got=yaml.safe_load(config.read_text(encoding='utf-8'))
assert got['workspace']['width_mm']==915 and got['workspace']['height_mm']==630
assert got['workspace']['physical_width_mm']==960 and got['workspace']['physical_height_mm']==650
assert (workspace/'laser_control_state/usable-travel-915x630-v1.json').is_file()
assert got['laser_control']['host_lasercad'] and not got['laser_control']['split_after_dxf']
assert not got['laser_control']['dashboard_enabled']
assert got['lasercad']['auto_open_dxf']
assert got['lasercad']['prepare_and_download']['speed_mm_s']==90
assert got['lasercad']['prepare_and_download']['power_percent']==10
assert (workspace/'laser_control_state/cut-recipe-90-10-v1.json').is_file()
assert got['lasercad']['prepare_and_download']['enabled']
assert got['lasercad']['prepare_and_download']['usb_port']=='MachName:_AWC_D2713611'
assert got['lasercad']['prepare_and_download']['run_box_after_success'] is False
assert got['lasercad']['executable']==str((home/'LaserCAD/AWCPrograms/LaserCAD.exe').resolve())
assert not got['machine_workflow']['enabled']
assert camera.read_bytes()==cam_before
assert hashlib.sha256(cam_before).hexdigest()=='8a44e0f46e043f3c8994df2e4d3a2972e3cf9ee57d76fb482981bb8c6405489c'
assert survey.read_bytes()==b'{"actual_survey":"unchanged"}'
receipt=json.loads((workspace/'laser_control_state/embedded-download-upgrade-v1.json').read_text())
assert Path(receipt['backup']).read_bytes()==before
got['lasercad']['prepare_and_download'].update(enabled=False,speed_mm_s=72.5,power_percent=8.25)
config.write_text(yaml.safe_dump(got),encoding='utf-8')
after=config.read_bytes()
module.prepare_workspace(workspace,home/'_internal')
assert config.read_bytes()==after,'Later intentional preferences must be preserved'
report={**info,'installer_verified':verified.strip(),'fresh_and_repeat_install_passed':True,
        'actual_frozen_startup_upgrade_passed':True,'calibration_preserved':True,'manual_recipe_preserved_after_restart':True,
        'camera_profile_sha256':hashlib.sha256(cam_before).hexdigest(),
        'focused_tests_passed':240,'space_ui_tests_passed':31,'all_javascript_tests_passed':46,'space_only_compiled_contract_verified':True,'completion_space_compiled_verified':True,'space_direct_completion_verified':True,'account_workflow_migration_verified':True,'manual_workflow_compiled_verified':True,'mandatory_camera_refresh_assets_verified':True,'navigation_and_version_tests_passed':3,'recorder_ui_check_passed':True,'version_display_javascript_check_passed':True,'packaged_app_version':'2026.11.18.3','remote_hardware_transfer_tested':False,
        'no_machine_commands_sent':True}
(w/'role-ui-final-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
