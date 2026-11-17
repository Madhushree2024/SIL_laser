"""Activate and repaint the imported LaserCAD document without mouse input."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import time
from threading import RLock

_HOSTED = {}
_HOSTED_LOCK = RLock()

def register_hosted_window(window):
    with _HOSTED_LOCK: _HOSTED[window["hwnd"]] = dict(window)

def unregister_hosted_window(hwnd):
    with _HOSTED_LOCK: _HOSTED.pop(hwnd, None)

def is_hosted_window(hwnd):
    with _HOSTED_LOCK: return hwnd in _HOSTED


class WindowsPreviewAPI:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.presented = {}
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u, k = self.user, self.kernel
        u.EnumWindows.argtypes = [self.callback, wintypes.LPARAM]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        u.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetMenu.argtypes = [wintypes.HWND]
        u.GetMenu.restype = wintypes.HMENU
        u.GetMenuState.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        u.GetMenuState.restype = wintypes.UINT
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        u.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

    def window_metadata(self, hwnd):
        title, kind = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(128)
        self.user.GetWindowTextW(hwnd, title, len(title))
        self.user.GetClassNameW(hwnd, kind, len(kind))
        if not title.value or kind.value == '#32770': return None
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle: return None
        try:
            name = ctypes.create_unicode_buffer(32768); length = wintypes.DWORD(len(name))
            if self.kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                return {'hwnd':int(hwnd),'pid':pid.value,'executable':name.value,'title':title.value}
        finally: self.kernel.CloseHandle(handle)
        return None

    def windows(self):
        result = []
        @self.callback
        def visit(hwnd, _):
            item = self.window_metadata(hwnd)
            if item: result.append(item)
            return True
        self.user.EnumWindows(visit, 0)
        with _HOSTED_LOCK: hosted = list(_HOSTED.values())
        seen = {item['hwnd'] for item in result}
        for saved in hosted:
            item = self.window_metadata(saved['hwnd'])
            if not item or any(item[k] != saved[k] for k in ('pid','executable')):
                unregister_hosted_window(saved['hwnd'])
            elif item['hwnd'] not in seen: result.append(item)
        return result

    def show(self, window):
        hwnd = window['hwnd']
        if is_hosted_window(hwnd):
            self.presented[hwnd] = True
            return False
        self.user.AllowSetForegroundWindow(window['pid'])
        self.user.ShowWindowAsync(hwnd, 9 if self.user.IsIconic(hwnd) else 5)
        time.sleep(.1)
        self.user.SetForegroundWindow(hwnd)
        activated = self.user.GetForegroundWindow() == hwnd
        # Windows may deny focus after an earlier export took foreground. Show
        # this document at the top without input focus or permanent topmost state.
        shown = activated or bool(self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0053))
        self.presented[hwnd] = shown
        return activated

    def visible(self, window):
        hwnd = window['hwnd']
        return bool(self.presented.get(hwnd) and self.user.IsWindowVisible(hwnd) and not self.user.IsIconic(hwnd))

    def ready(self, window):
        result = ctypes.c_size_t()
        return bool(self.user.SendMessageTimeoutW(window['hwnd'], 0, 0, 0, 2, 500, ctypes.byref(result)))

    def zoom_all(self, window):
        menu = self.user.GetMenu(window['hwnd'])
        state = self.user.GetMenuState(menu, 32796, 0) if menu else 0xffffffff
        if state != 0xffffffff:
            if state & 3:
                return False
        else:
            # LaserCAD's custom command bar may expose no Win32 menu. Validate
            # this read-only viewport command against its installed resources.
            resource = Path(window['executable']).parent.parent / 'AWCLanguage' / 'lang_Enu.ini'
            try:
                content = resource.read_bytes()
                encoding = 'utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
                labels = content.decode(encoding, errors='replace').splitlines()
            except OSError:
                return False
            if not any(line.strip().partition('=')[0] == '32796' and
                       line.partition('=')[2].strip().replace('&','').casefold() == 'zoom to all objects'
                       for line in labels):
                return False
        result = ctypes.c_size_t()
        return bool(self.user.SendMessageTimeoutW(window['hwnd'], 0x0111, 32796, 0, 2, 750, ctypes.byref(result)))

    def redraw(self, window):
        # Invalidate the client and all children. Queue painting without waiting
        # on a potentially blocked third-party GUI thread.
        return bool(self.user.RedrawWindow(window['hwnd'], None, None, 0x0087))


def prepare_lasercad_preview(pid, executable, vector, *, timeout=30, api=None, present=True):
    if sys.platform != 'win32' and api is None:
        return {'status':'unavailable', 'message':'Automatic preview requires Windows.'}
    api = api or WindowsPreviewAPI()
    expected = os.path.normcase(str(Path(executable).resolve()))
    filename = Path(vector).name.casefold()
    deadline = time.monotonic() + timeout
    while True:
        candidates = [w for w in api.windows()
                      if os.path.normcase(str(Path(w['executable']).resolve())) == expected
                      and filename in w['title'].casefold()]
        launched = [w for w in candidates if w['pid'] == pid]
        matches = launched or candidates
        if len(matches) == 1:
            window = matches[0]
            # A document title can precede construction of its drawing view.
            # Recheck the exact document before every delayed refresh.
            attempts = []
            for delay in (.5, .5, 1.0, 1.5):
                time.sleep(delay)
                live = [w for w in api.windows() if w['hwnd'] == window['hwnd']
                        and w['pid'] == window['pid']
                        and os.path.normcase(str(Path(w['executable']).resolve())) == expected
                        and filename in w['title'].casefold()]
                if not live:
                    return {'status':'warning', 'message':'LaserCAD changed or closed the target document during preview setup.', 'attempts':attempts}
                ready = api.ready(window)
                activated = api.show(window) if ready and present else False
                visible = api.visible(window) if ready else False
                zoomed = api.zoom_all(window) if ready else False
                painted = api.redraw(window) if ready else False
                attempts.append({'ready':ready,'activated':activated,'visible':visible,'zoom_requested':zoomed,'redraw_requested':painted})
                if time.monotonic() >= deadline:
                    break
            last = attempts[-1]
            return {'status':'refresh_requested' if last['ready'] and last['redraw_requested'] and (not present or last['activated'] or (last['visible'] and last['zoom_requested'])) else 'warning',
                    **last, 'attempts':attempts, 'window_title':window['title'], 'pid':window['pid']}
        if time.monotonic() >= deadline:
            return {'status':'warning', 'message':'Could not identify one LaserCAD window showing the exported DXF.'}
        time.sleep(.15)
