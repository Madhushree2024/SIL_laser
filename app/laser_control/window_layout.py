"""Reversible native side-by-side view. Window placement only, never machine input."""
import ctypes
from ctypes import wintypes
import os
import sys
from threading import Lock


class NativeWindowLayout:
    def __init__(self):
        if sys.platform != 'win32':
            raise ValueError('Native split view requires the Windows desktop app.')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        u = self.user
        u.IsWindow.argtypes = [wintypes.HWND]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.MonitorFromWindow.restype = wintypes.HANDLE
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]

    def snapshot(self, hwnd):
        if not self.user.IsWindow(hwnd):
            raise ValueError('A split-view window has closed. Refresh before arranging windows.')
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise OSError(ctypes.get_last_error(), 'Cannot read window position')
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        return {'hwnd': hwnd, 'pid': pid.value, 'class': name.value,
                'rect': [rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top],
                'show': 3 if self.user.IsZoomed(hwnd) else 2 if self.user.IsIconic(hwnd) else 1}

    def work_area(self, hwnd):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT), ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        info = MonitorInfo(); info.size = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(hwnd, 2)
        if not monitor or not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), 'Cannot read monitor working area')
        return [info.work.left, info.work.top, info.work.right-info.work.left, info.work.bottom-info.work.top]

    def place(self, hwnd, rect, show=1):
        self.user.ShowWindow(hwnd, 9)
        if not self.user.SetWindowPos(hwnd, None, *rect, 0x0014):
            raise OSError(ctypes.get_last_error(), 'Cannot arrange window')
        if show in (2,3):
            self.user.ShowWindow(hwnd, show)


class DesktopSplitLayout:
    def __init__(self, app_window, laser_window, api=None, process_id=None):
        self.app_window, self.laser_window, self.api = app_window, laser_window, api
        self.process_id = process_id if process_id is not None else os.getpid()
        self.originals = {}
        self.lock = Lock()

    def request(self, action):
        if action not in {'split','restore'}:
            raise ValueError('Unsupported window layout.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Window layout is already changing.')
        try:
            self.api = self.api or NativeWindowLayout()
            if action == 'restore':
                count = 0
                for hwnd, saved in list(self.originals.items()):
                    try:
                        current = self.api.snapshot(hwnd)
                        if current['pid'] == saved['pid'] and current['class'] == saved['class']:
                            self.api.place(hwnd, saved['rect'], saved['show']); count += 1
                    except (ValueError, OSError):
                        continue
                self.originals.clear()
                return {'status':'restored','windows':count,'message':'Previous window positions restored.'}
            app = self.api.snapshot(self.app_window())
            try:
                laser = self.laser_window()
            except RuntimeError as exc:
                raise ValueError(str(exc)) from exc
            cad = self.api.snapshot(laser['hwnd'])
            if app['pid'] != self.process_id or cad['pid'] != laser['pid'] or app['hwnd'] == cad['hwnd']:
                raise ValueError('Split-view window identity changed. No windows were arranged.')
            x,y,width,height = self.api.work_area(app['hwnd'])
            if width < 1120 or height < 600:
                raise ValueError('Side-by-side view needs a working area of at least 1120 x 600 pixels.')
            left_width = width//2
            for window in (app,cad):
                self.originals.setdefault(window['hwnd'], window)
            try:
                self.api.place(app['hwnd'], [x,y,left_width,height])
                self.api.place(cad['hwnd'], [x+left_width,y,width-left_width,height])
            except OSError:
                for window in (app,cad):
                    try:
                        self.api.place(window['hwnd'], window['rect'], window['show'])
                    except OSError:
                        pass
                raise
            return {'status':'split','message':'Laser Control is on the left; the matching LaserCAD drawing is on the right.'}
        finally:
            self.lock.release()
