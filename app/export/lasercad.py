"""Launch a DXF in LaserCAD, preserving the canonical export and using a bed import copy."""
from __future__ import annotations
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock
from .lasercad_preview import prepare_lasercad_preview
from .lasercad_import_coordinates import prepare_import_coordinates
from .lasercad_bed_mapping import enabled as bed_mapping_enabled, prepare_bed_copy, verify_import_snapshot

logger = logging.getLogger(__name__)
_HANDOFF_LOCK = Lock()

def _registered_executables():
    import winreg
    key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\LaserCAD.exe'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,key,0,winreg.KEY_READ | view) as handle:
                    yield winreg.QueryValueEx(handle,None)[0]
            except OSError:
                continue

def _valid_executable(value):
    path=Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    return path.resolve() if path.is_absolute() and path.name.lower()=='lasercad.exe' and path.is_file() else None

def find_lasercad(configured_path=''):
    if configured_path:
        path=_valid_executable(configured_path)
        if not path:raise ValueError('The configured LaserCAD path is invalid. Set lasercad.executable to the full LaserCAD.exe path in Settings.')
        return path
    for value in _registered_executables():
        path=_valid_executable(value)
        if path:return path
    system_drive = Path(os.environ.get('SystemDrive', 'C:') + '/')
    roots = [system_drive / 'LaserCAD']
    roots += [Path(os.environ[v]) / 'LaserCAD' for v in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(v)]
    for root in roots:
        for relative in ('AWCPrograms/LaserCAD.exe', 'LaserCAD.exe'):
            path = _valid_executable(root / relative)
            if path:
                return path
    found=shutil.which('LaserCAD.exe')
    return _valid_executable(found) if found else None

def open_dxf_in_lasercad(path,config):
    """Return launch acknowledgement or a warning; process start is not import confirmation."""
    def result(status,message,**extra):return dict(status=status,message=message,**extra)
    try:
        options=config.get('lasercad',{})
        if not isinstance(options,dict):raise ValueError('lasercad settings must be an object.')
        enabled=options.get('auto_open_dxf',True)
        if not isinstance(enabled,bool):raise ValueError('lasercad.auto_open_dxf must be true or false.')
        if not enabled:return result('disabled','Automatic LaserCAD opening is disabled.')
        vector=Path(path).resolve()
        if vector.suffix.lower()!='.dxf':return result('skipped','Only DXF exports are sent to LaserCAD.')
        if not vector.is_file() or vector.stat().st_size==0:
            raise ValueError('The saved DXF file is missing or empty. LaserCAD was not started.')
        if sys.platform!='win32':return result('unavailable','DXF saved. Automatic LaserCAD opening requires Windows.')
        executable=find_lasercad(options.get('executable',''))
        if executable is None:
            return result('not_installed','DXF saved. LaserCAD is not installed or was not found. Install LaserCAD; for a custom location, set lasercad.executable in Settings.')
        hosted = config.get("laser_control", {}).get("host_lasercad", False) is True
        startup = {}
        if hosted:
            info = subprocess.STARTUPINFO(); info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow = 0
            startup["startupinfo"] = info
        with _HANDOFF_LOCK:
            mapping = prepare_bed_copy(vector, config, executable) if bed_mapping_enabled(config) else None
            import_coordinates = prepare_import_coordinates(executable, page_top_right=True) if mapping else prepare_import_coordinates(executable)
            if mapping:
                verify_import_snapshot(vector, mapping)
                vector = Path(mapping['import_path'])
            process=subprocess.Popen([str(executable),str(vector)],cwd=str(executable.parent),
                                     stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     close_fds=True,shell=False,**startup)
            logger.info('LaserCAD launch requested for exported DXF %s (PID %s)',vector,process.pid)
            try:
                preview = prepare_lasercad_preview(process.pid, executable, vector, **({"present":False} if hosted else {}))
            except Exception as exc:
                logger.exception('LaserCAD preview refresh failed; DXF remains saved')
                preview = {'status':'warning', 'message':str(exc)}
            logger.info('LaserCAD preview result for %s: %s', vector, preview)
            if preview.get('status') == 'warning':
                return result('warning','DXF sent to LaserCAD, but automatic preview refresh was not confirmed. ' + preview.get('message','Windows did not activate or refresh the drawing window.'),pid=process.pid,preview=preview,import_coordinates=import_coordinates,import_mapping=mapping)
            return result('launch_requested',('Job-relative import prepared; physical placement awaits the verified move and Key Origin sequence. ' if mapping and mapping.get('positioning_mode')=='key_origin_relative' else ('LaserCAD import preserves the photographed machine position. DXF (0,0) is local job origin; no machine/work-origin command was sent. ' if mapping else '')) + ('DXF ready for the LaserCAD tab. Import docking: None (automatic centring disabled).' if hosted else 'DXF sent to LaserCAD. Import docking: None (automatic centring disabled); preview refresh requested.'),pid=process.pid,preview=preview,import_coordinates=import_coordinates,import_mapping=mapping)
    except Exception as exc:
        logger.exception('LaserCAD handoff failed; exported DXF remains saved')
        return result('warning',f'DXF saved, but LaserCAD could not be started: {exc}')
