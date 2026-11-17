"""Disable LaserCAD's file-import docking; never alter drawing or machine origin.

The supported installation's More Settings dialog maps None=0, Center=1.
The exact executable hash pins that verified mapping. Preferences are changed
only while LaserCAD is closed, because a running instance caches/writes them.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

SUPPORTED_EXECUTABLE_SHA256 = 'da9f264568cbfe8653463decc1849169d08fd9f06a8dae21e00eff2e00c2ae3b'


def no_docking_bytes(data: bytes) -> tuple[bytes, int]:
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'latin-1'
    text=data.decode(encoding)
    lines=text.splitlines(keepends=True)
    section='';sections=0;found=[]
    pattern=re.compile(r'^([ \t]*ImportDockLoction[ \t]*=[ \t]*)([0-9]+)([^\r\n]*)(\r\n|\n|\r)?$',re.I)
    for index,line in enumerate(lines):
        header=re.fullmatch(r'\s*\[([^\]]+)\]\s*',line)
        if header:
            section=header[1].casefold()
            if section=='importfile':sections+=1
        if section=='importfile':
            match=pattern.match(line)
            if match:found.append((index,match))
    if sections!=1 or len(found)!=1:
        raise ValueError('LaserCAD ImportFile/ImportDockLoction must occur exactly once; no settings were changed.')
    index,match=found[0];old=int(match[2])
    if not 0<=old<=5:raise ValueError('Unknown LaserCAD docking mode; no settings were changed.')
    if old==0:return data,old
    lines[index]=match[1]+'0'+match[3]+(match[4] or '')
    # UTF-16 LE/BE BOM is preserved, rather than using native byte order.
    updated=''.join(lines)
    if encoding=='utf-16':
        result=data[:2]+updated.encode('utf-16le' if data[:2]==b'\xff\xfe' else 'utf-16be')
    else:result=updated.encode(encoding)
    return result,old


def running_lasercad() -> bool:
    """Read process names, including instances with no visible window."""
    if sys.platform!='win32':raise RuntimeError('LaserCAD coordinate-import setup requires Windows.')
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
                  ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
                  ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
                  ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry=Entry();entry.dwSize=ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle,ctypes.byref(entry)):raise ctypes.WinError(ctypes.get_last_error())
        while True:
            if entry.szExeFile.casefold()=='lasercad.exe':return True
            if not kernel.Process32NextW(handle,ctypes.byref(entry)):
                if ctypes.get_last_error()!=18:raise ctypes.WinError(ctypes.get_last_error())
                return False
    finally:kernel.CloseHandle(handle)


def top_right_page_bytes(data: bytes) -> bytes:
    # Right_Up=2 was verified against the saved V8.28.3 Work Space setting.
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'latin-1'
    text=data.decode(encoding);lines=text.splitlines(keepends=True)
    section='';sections=0;matches=[]
    pattern=re.compile(r'^([ \t]*PageZeroPos[ \t]*=[ \t]*)([0-9]+)([^\r\n]*)(\r\n|\n|\r)?$',re.I)
    for i,line in enumerate(lines):
        header=re.fullmatch(r'\s*\[([^\]]+)\]\s*',line)
        if header:
            section=header[1].casefold()
            if section=='usercfg':sections+=1
        if section=='usercfg':
            match=pattern.match(line)
            if match:matches.append((i,match))
    if sections!=1 or len(matches)!=1 or not 0<=int(matches[0][1][2])<=3:
        raise ValueError('LaserCAD UserCfg/PageZeroPos is missing or ambiguous; no preferences changed.')
    i,match=matches[0]
    if int(match[2])==2:return data
    lines[i]=match[1]+'2'+match[3]+(match[4] or '')
    updated=''.join(lines)
    if encoding=='utf-16':return data[:2]+updated.encode('utf-16le' if data[:2]==b'\xff\xfe' else 'utf-16be')
    return updated.encode(encoding)


def prepare_import_coordinates(executable: Path, *, page_top_right: bool = False) -> dict:
    executable=Path(executable).resolve()
    if hashlib.sha256(executable.read_bytes()).hexdigest()!=SUPPORTED_EXECUTABLE_SHA256:
        raise ValueError('This LaserCAD executable has not been verified for preserving DXF coordinates. Import was not requested; do not assume the drawing stays at (0, 0).')
    settings=executable.parent.parent/'AWCCfg'/'SoftCfg.ini'
    original=settings.read_bytes()
    updated,old=no_docking_bytes(original)
    if page_top_right:updated=top_right_page_bytes(updated)
    changed=updated!=original
    backup=None
    if changed:
        if running_lasercad():
            raise ValueError('Close LaserCAD once, then retry the DXF export/open. Its cached page-origin/import settings need refreshing. No import or download was requested.')
        backup=settings.parent/'coordinate_import_backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/'SoftCfg.ini'
        backup.parent.mkdir(parents=True,exist_ok=False);shutil.copy2(settings,backup)
        if settings.read_bytes()!=original or running_lasercad():
            raise ValueError('LaserCAD or its settings changed during coordinate setup. No import was requested; retry after closing LaserCAD.')
        temporary=settings.with_name(settings.name+'.'+uuid4().hex+'.tmp')
        try:
            temporary.write_bytes(updated);temporary.replace(settings)
        finally:
            if temporary.exists():temporary.unlink()
    final=settings.read_bytes()
    if page_top_right and top_right_page_bytes(final)!=final:
        raise ValueError("LaserCAD top-right page-origin verification failed. No import requested.")
    if no_docking_bytes(final)[1]!=0:
        raise ValueError('LaserCAD import docking verification failed. No import was requested.')
    return {'status':'configured','setting':'ImportFile.ImportDockLoction','mode':'None','value':0,
            'changed':changed,'previous_value':old,'backup':str(backup) if backup else None,
            'page_zero':'Right_Up' if page_top_right else 'unchanged',
            'page_zero_value':2 if page_top_right else None,
            'coordinates_verified_in_lasercad':False,
            'message':'LaserCAD import docking is None; automatic centring is disabled. The imported drawing coordinates still require live verification.'}
