"""Read the loaded speed-unit preference of the pinned LaserCAD build.

Static verification: SpeedUnit maps to global RVA 0x1F8A54. Value 1 selects
language string 100096 (mm/s), value 2 selects 100097 (m/min). This is a UI
preference only, never controller telemetry. No process memory is written.
"""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
from app.export.lasercad_import_coordinates import SUPPORTED_EXECUTABLE_SHA256
from app.laser.lasercad_control import ControlError

SPEED_UNIT_RVA=0x1F8A54

class _ModuleEntry(ctypes.Structure):
    _fields_=[('dwSize',ctypes.c_uint32),('th32ModuleID',ctypes.c_uint32),
              ('th32ProcessID',ctypes.c_uint32),('GlblcntUsage',ctypes.c_uint32),
              ('ProccntUsage',ctypes.c_uint32),('modBaseAddr',ctypes.c_void_p),
              ('modBaseSize',ctypes.c_uint32),('hModule',ctypes.c_void_p),
              ('szModule',ctypes.c_wchar*256),('szExePath',ctypes.c_wchar*260)]

def _module_base(kernel,pid,executable):
    kernel.CreateToolhelp32Snapshot.argtypes=[ctypes.c_uint32,ctypes.c_uint32]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    for name in ('Module32FirstW','Module32NextW'):
        fn=getattr(kernel,name)
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(_ModuleEntry)]
        fn.restype=wintypes.BOOL
    snapshot=kernel.CreateToolhelp32Snapshot(0x18,pid)  # modules + WOW64 modules
    if not snapshot or snapshot==ctypes.c_void_p(-1).value:
        raise ControlError('Cannot inspect the loaded LaserCAD speed unit. No settings dialog or download was requested.')
    try:
        entry=_ModuleEntry();entry.dwSize=ctypes.sizeof(entry)
        valid=kernel.Module32FirstW(snapshot,ctypes.byref(entry))
        for _ in range(512):
            if not valid:break
            if os.path.normcase(str(Path(entry.szExePath).resolve()))==os.path.normcase(str(executable)):
                if entry.modBaseSize < SPEED_UNIT_RVA+4:
                    raise ControlError('LaserCAD module is incompatible with speed-unit verification.')
                return int(entry.modBaseAddr)
            valid=kernel.Module32NextW(snapshot,ctypes.byref(entry))
        raise ControlError('The verified LaserCAD module was not found in its process.')
    finally:
        kernel.CloseHandle(snapshot)

def read_speed_unit(kernel,process,pid,executable):
    executable=Path(executable).resolve()
    if hashlib.sha256(executable.read_bytes()).hexdigest()!=SUPPORTED_EXECUTABLE_SHA256:
        raise ControlError('This LaserCAD build is not supported for dialog-free speed-unit verification.')
    base=_module_base(kernel,pid,executable)
    result=ctypes.c_uint32();count=ctypes.c_size_t()
    ok=kernel.ReadProcessMemory(process,base+SPEED_UNIT_RVA,ctypes.byref(result),4,ctypes.byref(count))
    if not ok or count.value!=4:
        raise ControlError('The loaded LaserCAD speed unit could not be read. Download was not requested.')
    if result.value not in (1,2):
        raise ControlError('LaserCAD returned an unknown speed unit. Download was not requested.')
    return result.value
