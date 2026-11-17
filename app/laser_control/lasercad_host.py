"""Reversible native LaserCAD hosting. No drawing, job or machine commands."""
import ctypes
from ctypes import wintypes
from threading import Lock
from time import monotonic

from app.export.lasercad_preview import WindowsPreviewAPI, register_hosted_window, unregister_hosted_window


def embedded_style(style):
    # A former maximized top-level window must become an ordinary child window.
    return (style | 0x40000000) & ~(0x80000000 | 0x00C00000 | 0x00040000 | 0x00080000 | 0x21000000)


def canvas_size(width, height, minimum_width, minimum_height, scrollbar_width, scrollbar_height):
    horizontal = vertical = False
    for _ in range(3):
        available_width = max(1, width - (scrollbar_width if vertical else 0))
        available_height = max(1, height - (scrollbar_height if horizontal else 0))
        horizontal = minimum_width > available_width
        vertical = minimum_height > available_height
    return max(minimum_width, available_width), max(minimum_height, available_height)


class NativeEmbedAPI(WindowsPreviewAPI):
    def __init__(self):
        super().__init__()
        u = self.user
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindow.restype = wintypes.BOOL
        u.IsWindow.argtypes = [wintypes.HWND]
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
        u.SetParent.restype = wintypes.HWND
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.get_style = u.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.GetWindowLongW
        self.set_style = u.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.SetWindowLongW
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        u.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
        u.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        u.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL

    def minimum_size(self, snapshot):
        class MinMaxInfo(ctypes.Structure):
            _fields_ = [(name, wintypes.POINT) for name in ('reserved', 'max_size', 'max_position', 'min_track', 'max_track')]
        info = MinMaxInfo()
        result = ctypes.c_size_t()
        send = self.user.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = wintypes.LPARAM
        # Read the supplier's minimum dimensions with a bounded window-only query.
        ok = send(snapshot['hwnd'], 0x0024, 0, ctypes.addressof(info), 0x0002, 300, ctypes.byref(result))
        width, height = (info.min_track.x, info.min_track.y) if ok else (0, 0)
        return max(1024, min(12000, width)), max(720, min(12000, height))

    def snapshot(self, window):
        live = self.window_metadata(window['hwnd'])
        if not live or any(live[k] != window[k] for k in ('pid', 'executable')):
            raise ValueError('LaserCAD window identity changed. Retry display.')
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(window['hwnd'], ctypes.byref(rect)):
            raise OSError('Could not read the LaserCAD window position.')
        return {**live, 'parent': self.user.GetParent(window['hwnd']) or 0,
                'style': self.get_style(window['hwnd'], -16), 'exstyle': self.get_style(window['hwnd'], -20),
                'rect': [rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top]}

    def compatible(self, host, window):
        a = self.user.GetWindowDpiAwarenessContext(host)
        b = self.user.GetWindowDpiAwarenessContext(window['hwnd'])
        return bool(a and b and self.user.AreDpiAwarenessContextsEqual(a, b))

    def attach(self, host, snapshot, *, activate=True):
        hwnd = snapshot['hwnd']
        if not self.compatible(host, snapshot):
            raise ValueError('This LaserCAD window uses an incompatible display-scaling mode. It was not reparented. Use Show separately.')
        # Save every changed attribute before converting the top-level window.
        style = embedded_style(snapshot['style'])
        try:
            if snapshot['style'] & 0x21000000:
                self.user.ShowWindow(hwnd, 9 if activate else 4)  # Restore without activation for background Datum.
            self.set_style(hwnd, -16, style)
            self.set_style(hwnd, -20, snapshot['exstyle'] & ~0x00040000)
            ctypes.set_last_error(0)
            previous = self.user.SetParent(hwnd, host)
            if not previous and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            if self.user.GetParent(hwnd) != host:
                raise ValueError('LaserCAD could not be attached to the desktop tab.')
            register_hosted_window(snapshot)
        except Exception:
            self.restore(snapshot)
            raise

    def fit(self, snapshot, width, height, visible=True, *, activate=True):
        self.snapshot(snapshot)
        if not self.user.SetWindowPos(snapshot['hwnd'], None, 0, 0, width, height, 0x0034):
            raise OSError('Could not resize the hosted LaserCAD window.')
        self.user.ShowWindowAsync(snapshot['hwnd'], (5 if activate else 4) if visible else 0)

    def restore(self, snapshot):
        live = self.window_metadata(snapshot['hwnd'])
        if not live or any(live[k] != snapshot[k] for k in ('pid', 'executable')):
            unregister_hosted_window(snapshot['hwnd'])
            return
        hwnd = snapshot['hwnd']
        ctypes.set_last_error(0)
        previous = self.user.SetParent(hwnd, snapshot['parent'])
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        self.set_style(hwnd, -16, snapshot['style'])
        self.set_style(hwnd, -20, snapshot['exstyle'])
        self.user.SetWindowPos(hwnd, None, *snapshot['rect'], 0x0034)
        self.user.ShowWindowAsync(hwnd, 3 if snapshot['style'] & 0x01000000 else 2 if snapshot['style'] & 0x20000000 else 5)
        unregister_hosted_window(hwnd)


class WinFormsHostSurface:
    def __init__(self, form_getter):
        self.form_getter = form_getter
        self.panel = None
        self.canvas = None
        self.timer = None

    def invoke(self, work):
        from System import Action
        form = self.form_getter()
        result, errors = [], []
        def execute():
            try: result.append(work())
            except Exception as exc: errors.append(exc)
        if form.InvokeRequired: form.Invoke(Action(execute))
        else: execute()
        if errors: raise errors[0]
        return result[0] if result else None

    def prepare(self, area, expire):
        from System.Drawing import Color, Rectangle, Size
        from System.Windows.Forms import Panel, Timer, SystemInformation
        form = self.form_getter()
        if self.panel is None:
            self.panel = Panel()
            self.panel.BackColor = Color.Black
            self.panel.AutoScroll = True
            self.canvas = Panel()
            self.canvas.BackColor = Color.Black
            self.panel.Controls.Add(self.canvas)
            self.panel.Visible = False
            form.Controls.Add(self.panel)
            self.timer = Timer()
            self.timer.Interval = 250
            self.timer.Tick += lambda *_: expire()
            self.timer.Start()
        # CSS viewport ratios account for WebView zoom and Windows scaling.
        width, height = form.ClientSize.Width, form.ClientSize.Height
        x = max(0, min(width, round(area['x'] * width / area['viewport_width'])))
        y = max(0, min(height, round(area['y'] * height / area['viewport_height'])))
        w = max(1, min(width-x, round(area['width'] * width / area['viewport_width'])))
        h = max(1, min(height-y, round(area['height'] * height / area['viewport_height'])))
        self.panel.Bounds = Rectangle(x, y, w, h)
        minimum_width, minimum_height = area.get('minimum_size', (1, 1))
        cw, ch = canvas_size(w, h, minimum_width, minimum_height,
                             SystemInformation.VerticalScrollBarWidth, SystemInformation.HorizontalScrollBarHeight)
        self.canvas.Size = Size(cw, ch)
        self.panel.AutoScrollMinSize = Size(cw, ch)
        # Do not reset Location/AutoScrollPosition on each heartbeat: keep user scrolling.
        return int(self.canvas.Handle.ToInt64()), cw, ch

    def show(self):
        self.panel.Visible = True
        self.panel.BringToFront()

    def background(self):
        # Keep WS_VISIBLE for native control discovery, but clip the entire pane
        # outside the form's client rectangle. No screen/tab navigation or focus.
        from System.Drawing import Point
        form = self.form_getter()
        self.panel.Location = Point(form.ClientSize.Width + 32, form.ClientSize.Height + 32)
        self.panel.Visible = True

    def hide(self):
        if self.panel is not None: self.panel.Visible = False

    def dispose(self):
        if self.timer is not None: self.timer.Stop(); self.timer.Dispose()
        if self.panel is not None: self.panel.Dispose()
        self.panel = self.canvas = self.timer = None


class DesktopLaserCADHost:
    def __init__(self, form_getter, target_window, api=None, surface=None, clock=monotonic):
        self.target_window = target_window
        self.api = api
        self.surface = surface or WinFormsHostSurface(form_getter)
        self.clock = clock
        self.records = {}
        self.deadline = 0
        self.lock = Lock()
        self.last = {'supported': True, 'status': 'idle', 'message': 'The current LaserCAD drawing will be displayed here.'}

    def expire(self):
        # The native pane must not cover another tab or an expired sign-in page.
        if self.deadline and self.clock() >= self.deadline:
            self.surface.hide()
            self.deadline = 0
            self.last.update(status='hidden')

    def request(self, payload):
        action = payload.get('action', 'status')
        if action == 'status': return dict(self.last)
        if not self.lock.acquire(blocking=False):
            raise ValueError('LaserCAD display is updating. Retry shortly.')
        try:
            def work():
                if action == 'hide':
                    self.deadline = 0; self.surface.hide()
                    self.last.update(status='hidden')
                    return dict(self.last)
                if action == 'detach':
                    self.deadline = 0; self.surface.hide()
                    if not self.records:
                        self.api = self.api or NativeEmbedAPI()
                        self.api.show(self.target_window())
                    for hwnd, record in list(self.records.items()):
                        self.api.restore(record); self.records.pop(hwnd)
                    self.last.update(status='detached', message='LaserCAD is displayed separately. Its document and machine state were not changed.')
                    return dict(self.last)
                background = action == 'background'
                if action not in {'show','background'}: raise ValueError('Unsupported LaserCAD display action.')
                if background:
                    payload.update(x=0,y=0,width=1280,height=900,viewport_width=1280,viewport_height=900)
                for key in ('width', 'height', 'viewport_width', 'viewport_height'):
                    if not 0 < payload.get(key, 0) <= 30000: raise ValueError('Invalid LaserCAD tab size.')
                self.api = self.api or NativeEmbedAPI()
                window = payload.get('_background_window') if background else self.target_window()
                if background:
                    if not window: raise ValueError('A verified LaserCAD window is required for background Datum.')
                    live = self.api.window_metadata(window['hwnd'])
                    if not live or any(live[k] != window[k] for k in ('pid','executable','title')):
                        raise ValueError('LaserCAD changed before background preparation.')
                record = self.records.get(window['hwnd'])
                if record and any(record[k] != window[k] for k in ('pid', 'executable')):
                    self.records.pop(window['hwnd']); record = None
                fresh = record is None
                if fresh:
                    record = self.api.snapshot(window)
                    if hasattr(self.api, 'minimum_size'):
                        record['minimum_size'] = self.api.minimum_size(record)
                area = {**payload, 'minimum_size': record.get('minimum_size', (1, 1))}
                host, width, height = self.surface.prepare(area, self.expire)
                if fresh:
                    if background: self.api.attach(host, record, activate=False)
                    else: self.api.attach(host, record)
                    self.records[window['hwnd']] = record
                for hwnd, other in list(self.records.items()):
                    try:
                        if background: self.api.fit(other, width, height, visible=hwnd == window['hwnd'], activate=False)
                        else: self.api.fit(other, width, height, visible=hwnd == window['hwnd'])
                    except (ValueError, OSError):
                        if hwnd == window['hwnd']: raise
                        self.records.pop(hwnd); unregister_hosted_window(hwnd)
                if background: self.surface.background()
                else: self.surface.show()
                self.deadline = self.clock() + (30 if background else 2.5)
                self.last.update(status='background' if background else 'hosted', title=window['title'], message='Background Datum preparation.' if background else 'Live LaserCAD window. Use its own drawing and machine controls.')
                return dict(self.last)
            try: return self.surface.invoke(work)
            except Exception as exc:
                self.deadline = 0
                try: self.surface.invoke(self.surface.hide)
                except Exception: pass
                self.last.update(status='unavailable', message=str(exc))
                raise ValueError(str(exc)) from exc
        finally: self.lock.release()

    def close(self):
        if self.records: self.request({'action': 'detach'})
        else: self.surface.invoke(self.surface.hide)
        self.surface.invoke(self.surface.dispose)
