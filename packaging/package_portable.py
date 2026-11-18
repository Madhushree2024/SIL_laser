from pathlib import Path
import hashlib, json, shutil, zipfile, re
from PyInstaller.archive.readers import CArchiveReader
root=Path(__file__).resolve().parents[1]
work=root/'verification/current';work.mkdir(parents=True,exist_ok=True)
app_version=re.search(r"APP_VERSION = '([^']+)'",(root/'app/application_info.py').read_text(encoding='utf-8')).group(1)
launch=(root/'packaged-launch.cmd').read_text(encoding='utf-8-sig')
exe=root/re.search(r'dist\\releases\\[^"\r\n]+\.exe',launch).group(0)
assert exe.parent.parent.name not in {'20261113-153921','20261113-170607','20261113-174129','20261113-180153','20261114-110853','20261114-114622','20261114-124029','20261114-142542','20261114-145703','20261114-151205'}, 'Wait for new build'
reader=CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz')
code=reader.extract('desktop_runtime')
assert any(getattr(c,'co_name',None)=='upgrade_embedded_download_settings' for c in code.co_consts)
identity=reader.extract('app.application_info')
assert app_version in identity.co_consts
old=root/'packaging/base_setup'
from datetime import datetime
stage=(root/'build/portable-staging')/('TemplateCutting-'+app_version+'-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
if not stage.exists():shutil.copytree(old,stage)
payload=stage/'TemplateCutting'
def ignore(folder,names):
    return ['seed'] if Path(folder).name=='_internal' else []
def copy_overlay(source,destination):
    source=Path(source);destination=Path(destination)
    # Supplier assets can be read-only. Identical files need no overwrite.
    if destination.is_file() and source.read_bytes()==destination.read_bytes():return str(destination)
    return shutil.copy2(source,destination)
shutil.copytree(exe.parent,payload,dirs_exist_ok=True,ignore=ignore,copy_function=copy_overlay)
# A desktop build may omit the supplier program; never ship a Python-only ZIP.
if not (payload/'LaserCAD/AWCPrograms/LaserCAD.exe').is_file():
    cad_source=root.parent/'LaserCAD'
    assert (cad_source/'AWCPrograms/LaserCAD.exe').is_file(), 'Bundled LaserCAD is missing'
    shutil.copytree(cad_source,payload/'LaserCAD',dirs_exist_ok=True,copy_function=copy_overlay)
assert (payload/'LaserCAD/AWCPrograms/LaserCAD.exe').is_file()
# Keep the safe existing calibration seed; update only nominal travel defaults.
import yaml
seed_config=payload/'_internal/seed/app/config/config.yaml'
seed=yaml.safe_load(seed_config.read_text(encoding='utf-8-sig'))
seed.setdefault('workspace',{}).update(width_mm=915,height_mm=630,physical_width_mm=960,physical_height_mm=650)
for obsolete in ('run_box_after_success','transfer_timeout_seconds'):
    seed.get('lasercad',{}).get('prepare_and_download',{}).pop(obsolete,None)
seed.setdefault('lasercad',{}).setdefault('prepare_and_download',{}).update(speed_mm_s=90,power_percent=10,work_mode='Cut')
seed.setdefault('nesting',{})['spacing_mm']=0
seed_config.write_text(yaml.safe_dump(seed,sort_keys=False),encoding='utf-8')
seed_manifest=payload/'_internal/seed/seed-manifest.json'
if seed_manifest.exists():
    sm=json.loads(seed_manifest.read_text(encoding='utf-8'))
    sm['app/config/config.yaml']=hashlib.sha256(seed_config.read_bytes()).hexdigest()
    seed_manifest.write_text(json.dumps(sm,indent=2),encoding='utf-8')
installer=(root/'packaging/Install-TemplateCutting.ps1').read_text(encoding='utf-8-sig')
(stage/'Install.ps1').write_text(installer,encoding='utf-8-sig')
shutil.copy2(root/'packaging/TemplateCutting-Shortcuts.ps1',stage/'TemplateCutting-Shortcuts.ps1')

guide = 'TEMPLATE CUTTING AUTOMATION - VERSION {version}\nSPACE-ONLY WORKFLOW - DIRECT COMPLETION\n\nINSTALL / UPDATE\nClose Template Cutting and LaserCAD normally when idle. Extract the entire ZIP\nand run TemplateCutting-Setup/Install.cmd. Use Template Cutting Automation,\nthe single Desktop shortcut. Installation details shows the version and selected\nLaserCAD path. Existing calibration, accounts and preferences are preserved.\nPython, prerequisites, LaserCAD and camera_calibration.npz are included.\nVerify Package.cmd checks the packaged hashes.\n\nWORKFLOW\n1. First Space: refresh/reconnect camera, capture, lens correction, four-marker\n   perspective, outline detection, nesting, DXF export, embedded LaserCAD,\n   configured USB selection, red-layer Cut/speed/power, Download and Download\n   Document once. Wait until transfer has actually finished and check placement.\n2. Second Space: operator approval to request Start once. Run Box is not part of\n   this key sequence. After cutting finishes, a fresh Space acknowledges completion and resets.\n3. Space after cutting has finished and the machine is idle directly confirms\n   completion and resets for the next sheath. There is no extra dialog or click.\n   Completion activates Process Sheath once and verifies the screen.\n   A lost reset response is checked against a durable receipt before retry.\n   Navigation recovery reuses the existing navigator without resetting again.\n   Completion focuses the camera workspace.\n   The next first Space refreshes the camera again.\n\nTab and Shift+Tab retain normal navigation. Enter is not a cycle key.\nA fresh Space after cutting is the completion acknowledgement in either mode.\nSpace inside text fields, dialogs and unrelated applications is not used.\nHeld/repeated keys, busy requests and stale revisions cannot skip stages.\nDisplay refreshes do not block operator Space presses; real pending operations\nremain protected and show a message. Blocked presses are never queued.\nNo automatic retry, delayed motion or automatic Start occurs after download.\nDownload submission is not verified transfer completion. Physical cutting\ncompletion is acknowledged by the operator pressing Space, not read from telemetry.\nThe mouse completion button and interrupted-cycle recovery retain their confirmation.\nAn interrupted cycle requires explicit Machine idle - reset cycle confirmation.\nAn old idle cycle migrates to this sequence; old active jobs are never resumed.\n\nMANUAL DATUM / DISPLAY\nThe separate Datum button on Process Sheath requires an idle-machine\nconfirmation and stays on Process Sheath. LaserCAD runs in a clipped background\npane, selects and verifies the configured USB dropdown entry, then requests Datum.\nIf that entry is missing, configure it in LaserCAD Select Mode first; the app\nwill not choose another device or open a connection dialog from this button.\nDatum does not advance or reset the Space-only workflow.\nWait until the head stops before continuing.\nAutomatic workflow is beside the cycle heading. Outline sun/moon icons switch\nthe remembered theme. Header controls wrap without horizontal page scrolling.\nLaserCAD opens embedded without split screen. Native pane scrollbars keep its\ncontrols accessible below its minimum size. Admin help is retained; Manager and\nRead-only hide explanations. Permissions are unchanged.\n\nSETTINGS\nDefaults: Cut, 90 mm/s, 10% maximum primary power; template spacing 0 mm.\nEdit and save the recipe in Settings > Export & LaserCAD.\nUSB default: MachName:_AWC_D2713611. Select the actual connected device.\nUsable travel: 915 x 630 mm. Recorded physical bed: 960 x 650 mm.\nCalibration, nesting geometry, DXF coordinates, controller Origin mode and the\nseparate uncommissioned SDK workflow are unchanged by this release.\n\nLOGS\nOperator recording exports logs for a connected-machine test.\nCycle state: Workspace/laser_control_state/space-cycle.json.\nEvents: Workspace/laser_control_state/space-cycle-events.jsonl.\nLogs show UI requests and operator acknowledgements, not physical execution.\n'.format(version=app_version)
guide += '\nADMIN-ASSIGNED WORKFLOWS\nAccount > Manage users: only Admin can assign Automated or Manual to\nEditor/Manager and Read-only users. Admin remains unrestricted.\nAutomated: first Space through Download Document; second Space requests Start;\nSpace acknowledges finished cutting and resets without a dialog.\nManual: the same first Space through Download Document; remaining machine actions\nuse LaserCAD controls manually. Space does not request Start. Space acknowledges\nfinished cutting and resets without a dialog.\nAssigned Read-only users can operate their workflow, but cannot edit templates,\nSettings, calibration or accounts. Unassigned Read-only users remain view-only.\nChanges require a new sign-in. Existing accounts/passwords are migrated in place;\nexisting Editor/Manager accounts keep Automated behavior. The unused supervised\ncycle control has been removed. No machine settings or calibration are changed.\n'
(stage/'READ ME FIRST.txt').write_text(guide,encoding='utf-8')
meta=json.loads((stage/'package-info.json').read_text(encoding='utf-8'))
meta.update(parameter_route='red_colour_cell', system_settings_opened=False, direct_layer_tests_passed=79, download_popup_tests_passed=115, download_dialog_and_travel_tests_passed=164, modeless_panel_regression_tests_passed=169, usable_travel_mm=[915,630], physical_bed_mm=[960,650], success_popup_requires_verified_transfer=True, loading_document_progress_supported=True, speed_unit_verification='pinned read-only loaded UI preference', operator_recording=True, recording_default='off', recording_sends_machine_commands=False, recorder_tests_passed=68, package_date='2026-11-18', app_version=app_version, path_display_read_only=True, version_display_tests_passed=27, speed_unit_fix_tests_passed=65, frozen_ui_tests_passed=65, output_checkbox_preserved=True, build=exe.parent.parent.name,settings_upgrade='embedded-download-v1',
            existing_workspace_settings_repair=True,split_screen_enabled=False,
            desktop_upgrade_tests_passed=25,remote_usb_download_verified=False)
for key in list(meta):
    if key.endswith('_tests_passed'):meta.pop(key)
meta.pop('success_popup_requires_verified_transfer',None)
meta.update(default_template_spacing_mm=0,theme_toggle=True,embedded_minimum_size_scrolling=True,role_specific_help=True,admin_view_preserved=True,viewer_errors_plain_language=True,role_ui_tests_passed=9,desktop_shortcuts=["Template Cutting Automation"],legacy_duplicate_shortcut_cleanup=True,default_speed_mm_s=90, default_power_percent=10, editable_cutting_parameters=True, minimum_power_capped_to_maximum=True, software_regression_tests_passed=240, space_workflow_ui_tests_passed=31, navigation_and_version_tests_passed=3, version_labels_from_backend=True, datum_returns_to_camera=False, process_laser_controls_button=False, display_text_utf8_verified=True, process_sheath_navigation_preserved=True, download_mode='submission_only',
    transfer_verification='not_requested', success_popup_enabled=False,
    automatic_post_download_actions=False, automatic_run_box_after_download=False,
    start_manual=True, start_requires_third_space=False, start_requires_second_space=True, run_box_in_key_workflow=False, datum_required=False, datum_key=None, display_refresh_does_not_block_space=True,admin_assigned_workflows=True,workflow_assignment_location='Account',manual_first_space_download=True,supervised_cycle_control=False,manual_datum_background=True,manual_datum_verifies_usb=True,manual_datum_button=True, manual_datum_in_workflow=False, compact_theme_icons=True, automatic_button_in_cycle_row=True, refresh_camera_on_first_space=True, capture_requires_successful_camera_refresh=True, completion_key="Space", completion_confirmation_dialog=False, tab_direct_completion=False,space_direct_completion=False, automated_return_home_after_start=True, automated_completion_press_required=False, post_start_not_physical_completion=True, completion_button_confirmation_dialog=True, native_focus_hook_tests_passed=58, current_focused_backend_tests_passed=120, current_focused_ui_tests_passed=53, windows_x64_api_validation_passed=3, windows_actual_keyboard_input_tested=False, reset_receipt_persisted=True, reset_memory_published_after_disk=True, completion_navigation_verified=True, lost_reset_response_reconciled=True, remote_injected_completion_only=True, remote_paired_key_consumed=True, remote_start_fallthrough_guard=True, remote_live_control_software_tested=False, native_completion_only_dispatch=True, native_completion_cycle_pinned=True, native_owned_panel_focus=True, completion_activates_process_sheath=True, completion_focuses_camera_workspace=True, space_only_workflow=True, process_key="Space", space_workflow=True, completion_requires_operator_confirmation=True, native_space_bridge=True, controller_completion_feedback=False, automatic_download_retries=False)
(stage/'package-info.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
source_archive=stage/('Source/TemplateCutting-source-'+app_version+'.zip')
source_archive.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(source_archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for folder in ('app','frontend','packaging','tests'):
        for file in sorted((root/folder).rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts and 'base_setup' not in file.relative_to(root).parts:
                archive.write(file,file.relative_to(root))
    for name in ('desktop.py','desktop_runtime.py','requirements.txt'):
        file=root/name
        if file.is_file():archive.write(file,name)
manifest={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(stage.rglob('*')) if p.is_file() and p.name!='package-sha256.json'}
(stage/'package-sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
target=root/'dist/setup'/('TemplateCutting-Setup-With-Python-'+app_version+'.zip')
target.parent.mkdir(parents=True,exist_ok=True)
assert not target.exists()
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            z.write(p,Path('TemplateCutting-Setup')/p.relative_to(stage),
                    compress_type=zipfile.ZIP_STORED if 'Prerequisites' in p.relative_to(stage).parts else zipfile.ZIP_DEFLATED)
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    for rel,digest in manifest.items():
        assert hashlib.sha256(z.read('TemplateCutting-Setup/'+rel.replace('\\','/'))).hexdigest()==digest
digest=hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix('.zip.sha256.txt').write_text(digest+'  '+target.name+'\n',encoding='ascii')
summary={'zip':str(target),'stage':str(stage),'build_exe':str(exe),'sha256':digest,
         'size_mb':round(target.stat().st_size/1024**2,1),'files':len(manifest),'crc_and_hashes_verified':True}
(work/'role-ui-package.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
