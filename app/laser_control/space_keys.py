"""App-scoped Space-only workflow bridge for a hosted foreign-process LaserCAD window.

Consumes a down/up pair only inside our active native pane. Other applications,
text editors, parameter dialogs and modified/injected keystrokes are untouched.
No machine command is sent from the keyboard hook.
"""
import ctypes
from ctypes import wintypes
import logging
import json
from queue import Queue, Full
from threading import Event, Thread
from time import monotonic

class SpaceEdge:
    def __init__(self):self.down=False;self.consumed=False
    def key(self, is_down, eligible):
        if not is_down:
            consumed=self.consumed;self.down=False;self.consumed=False
            return consumed,False
        if self.down:return self.consumed,False
        self.down=True;self.consumed=bool(eligible)
        return self.consumed,self.consumed



def native_space_focus(api, form, record, *, completion=False):
    """Inspect parent AND owner chains, including an owned LaserCAD Control Panel.

    api performs window-only queries. It never activates a window or sends input.
    Browser focus stays with DOM handlers so input fields keep normal Space.
    """
    target = record['hwnd']
    foreground = api.foreground()
    if not foreground or not ({form, target} & set(api.chain(foreground))):
        return False, 'outside_host'
    focus, flags = api.focus(foreground)
    if flags & (0x04 | 0x10 | 0x20):
        return False, 'menu_active'
    if not focus:
        return False, 'no_native_focus'
    chain = list(api.chain(focus))
    if not ({form, target} & set(chain)):
        return False, 'focus_outside_host'
    kinds = [api.kind(hwnd).casefold() for hwnd in chain]
    for hwnd, name in zip(chain, kinds):
        if any(edit in name for edit in ('edit','scintilla')):
            return False,'editable_control'
        if 'combo' in name and not (completion and api.closed_readonly_combo(hwnd)):
            return False,'editable_control'
    # WebView keyboard events must be inspected by the DOM, never intercepted
    # without knowing whether an HTML input/confirmation is being edited.
    if any(any(browser in name for browser in ('chrome_', 'webview', 'internet explorer_server')) for name in kinds):
        return False, 'browser_focus'
    pid = api.pid(focus)
    if pid != record['pid']:
        # Focus can land on our WinForms host panel after a native button click.
        # Such a press may acknowledge completion, but must never authorize Start.
        if not completion or pid != api.pid(form) or not kinds[0].startswith('windowsforms10.'):
            return False, 'focus_process_mismatch'
    for hwnd, name in zip(chain, kinds):
        if hwnd in {target, form}:
            continue
        if name == '#32770' and api.title(hwnd).replace(' ', '').casefold() not in {'controlpanel', 'downloaddocument'}:
            return False, 'modal_dialog'
    download_dialog=any(name=='#32770' and api.title(hwnd).replace(' ','').casefold()=='downloaddocument' for hwnd,name in zip(chain,kinds))
    if not api.enabled(target) and (completion or not download_dialog):
        return False, 'main_window_disabled'
    return True, 'completion' if completion else 'workflow'


class ComboBoxInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('item',wintypes.RECT),('button',wintypes.RECT),
              ('state',wintypes.DWORD),('combo',wintypes.HWND),('edit',wintypes.HWND),('list',wintypes.HWND)]


def declare_focus_api(user, gui_type):
    # HWND is pointer-sized; LONG (GWL_STYLE) remains 32-bit on 64-bit Windows.
    signatures = {
        'GetForegroundWindow': ([], wintypes.HWND),
        'GetParent': ([wintypes.HWND], wintypes.HWND),
        'GetWindow': ([wintypes.HWND,wintypes.UINT], wintypes.HWND),
        'GetWindowThreadProcessId': ([wintypes.HWND,ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        'GetGUIThreadInfo': ([wintypes.DWORD,ctypes.POINTER(gui_type)], wintypes.BOOL),
        'GetClassNameW': ([wintypes.HWND,wintypes.LPWSTR,ctypes.c_int], ctypes.c_int),
        'GetWindowTextW': ([wintypes.HWND,wintypes.LPWSTR,ctypes.c_int], ctypes.c_int),
        'IsWindowEnabled': ([wintypes.HWND], wintypes.BOOL),
        'GetWindowLongW': ([wintypes.HWND,ctypes.c_int], wintypes.LONG),
        'GetComboBoxInfo': ([wintypes.HWND,ctypes.POINTER(ComboBoxInfo)], wintypes.BOOL),
        'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
    }
    for name,(arguments,result) in signatures.items():
        function=getattr(user,name);function.argtypes=arguments;function.restype=result


class WindowsSpaceFocusAPI:
    def __init__(self, user, gui_type):
        self.user, self.gui_type = user, gui_type
        declare_focus_api(user,gui_type)
    def foreground(self): return self.user.GetForegroundWindow()
    def chain(self, hwnd):
        pending, seen = [hwnd], set()
        while pending and len(seen) < 64:
            current = pending.pop()
            if not current or current in seen: continue
            seen.add(current); yield current
            pending.extend([self.user.GetParent(current), self.user.GetWindow(current, 4)])  # GW_OWNER
    def focus(self, foreground):
        gui = self.gui_type(); gui.cbSize = ctypes.sizeof(gui)
        thread = self.user.GetWindowThreadProcessId(foreground, None)
        if not thread or not self.user.GetGUIThreadInfo(thread, ctypes.byref(gui)): return 0, 0
        return gui.focus, gui.flags
    def pid(self, hwnd):
        pid = wintypes.DWORD(); self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)); return pid.value
    def kind(self, hwnd):
        value = ctypes.create_unicode_buffer(128); self.user.GetClassNameW(hwnd, value, len(value)); return value.value
    def title(self, hwnd):
        value = ctypes.create_unicode_buffer(256); self.user.GetWindowTextW(hwnd, value, len(value)); return value.value
    def enabled(self, hwnd): return bool(self.user.IsWindowEnabled(hwnd))
    def closed_readonly_combo(self, hwnd):
        # A USB selection dropdown can retain focus after download. A closed
        # CBS_DROPDOWNLIST has no editable text; an open dropdown is still protected.
        if self.user.GetWindowLongW(hwnd,-16)&0x3 != 0x3:return False
        info=ComboBoxInfo();info.cbSize=ctypes.sizeof(info)
        return bool(self.user.GetComboBoxInfo(hwnd,ctypes.byref(info)) and info.list and not self.user.IsWindowVisible(info.list))


class DesktopSpaceKeys:
    def __init__(self,window,host,completion_ready=lambda:False):
        self.window,self.host=window,host
        self.completion_ready=completion_ready
        self.queue=Queue(maxsize=1);self.stop=Event();self.edges={0x20:SpaceEdge()};self.thread_id=0
        self.active=False
    def completion_key_ready(self):
        # The hook reads only a cached stage, never files, locks or a controller.
        try:
            value=self.completion_ready()
            return not self.active and (value.get('stage') in {'awaiting_completion','awaiting_manual_completion'} if isinstance(value,dict) else bool(value))
        except Exception:return False
    def completion_snapshot(self):
        try:
            value = self.completion_ready()
            if self.active or not isinstance(value, dict) or value.get('stage') not in {'awaiting_completion','awaiting_manual_completion'}:
                return None
            return {key:value[key] for key in ('cycle_id','revision','stage')}
        except (KeyError, TypeError): return None
        except Exception:
            logging.exception('Native completion context unavailable')
            return None
    def start(self):
        Thread(target=self._dispatch,daemon=True,name='space-ui-dispatch').start()
        Thread(target=self._listen,daemon=True,name='space-native-keyboard').start()
    def close(self):
        self.stop.set()
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x0012,0,0)
    def _dispatch(self):
        from queue import Empty
        while not self.stop.is_set():
            try:code=self.queue.get(timeout=.2)
            except Empty:continue
            try:
                if isinstance(code,dict):
                    if code.get('blocked'):
                        logging.info('Native Space not routed: %s',code['blocked'])
                        continue
                    logging.info('Native completion Space: cycle=%s revision=%s',code['cycle_id'],code['revision'])
                    result=self.window.evaluate_js('window.SpaceCycle && window.SpaceCycle.nativeComplete('+json.dumps(code)+')')
                    logging.info('Native completion handler result: %s',result)
                else:
                    logging.info('Native workflow Space routed to UI')
                    self.window.evaluate_js('window.SpaceCycle && window.SpaceCycle.nativePress('+repr(code)+')')
            except Exception:logging.exception('Could not route native Space to the operator workflow')
            finally:
                if not isinstance(code,dict) or not code.get('blocked'):self.active=False
    def _listen(self):
        user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        pointer=ctypes.c_ssize_t
        callback_type=ctypes.WINFUNCTYPE(pointer,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
        class Key(ctypes.Structure):
            _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        class Gui(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),('active',wintypes.HWND),('focus',wintypes.HWND),('capture',wintypes.HWND),('menu',wintypes.HWND),('move',wintypes.HWND),('caret',wintypes.HWND),('rect',wintypes.RECT)]
        user.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,wintypes.HINSTANCE,wintypes.DWORD];user.SetWindowsHookExW.restype=ctypes.c_void_p
        user.CallNextHookEx.argtypes=[ctypes.c_void_p,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM];user.CallNextHookEx.restype=pointer
        user.UnhookWindowsHookEx.argtypes=[ctypes.c_void_p]
        user.GetForegroundWindow.restype=wintypes.HWND
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
        user.GetParent.argtypes=[wintypes.HWND];user.GetParent.restype=wintypes.HWND
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        user.GetGUIThreadInfo.argtypes=[wintypes.DWORD,ctypes.POINTER(Gui)]
        user.GetAsyncKeyState.argtypes=[ctypes.c_int];user.GetAsyncKeyState.restype=ctypes.c_short
        kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel.GetModuleHandleW.restype=wintypes.HMODULE
        self.thread_id=kernel.GetCurrentThreadId()
        # Create the queue before close() can send WM_QUIT.
        msg=wintypes.MSG();user.PeekMessageW(ctypes.byref(msg),None,0,0,0)
        focus_api=WindowsSpaceFocusAPI(user,Gui)
        def eligible(completion):
            if self.host.last.get('status')!='hosted' or self.host.deadline<=monotonic():return False,'host_not_visible'
            records=list(self.host.records.values())
            if len(records)!=1:return False,'ambiguous_host'
            form=int(self.window.native.Handle.ToInt64())
            return native_space_focus(focus_api,form,records[0],completion=completion)
        @callback_type
        def hook(code,wparam,lparam):
            if code>=0:
                key=ctypes.cast(lparam,ctypes.POINTER(Key)).contents
                if key.vkCode in self.edges and not key.flags&0x10 and wparam in (0x0100,0x0101,0x0104,0x0105):
                    down=wparam in (0x0100,0x0104)
                    try:
                        modified=any(user.GetAsyncKeyState(v)&0x8000 for v in (0x10,0x11,0x12,0x5B,0x5C))
                        snapshot=self.completion_snapshot() if down else None
                        allowed,reason=eligible(snapshot is not None) if down and not modified else (False,'modified_or_keyup')
                        consume,fire=self.edges[key.vkCode].key(down,allowed)
                        if fire and not self.active:
                            self.active=True
                            try:self.queue.put_nowait(snapshot if snapshot is not None else 'Space')
                            except Full:self.active=False
                        elif down and not modified and not allowed and not self.active and reason not in {'outside_host','host_not_visible'}:
                            try:self.queue.put_nowait({'blocked':reason})
                            except Full:pass
                        if consume:return 1
                    except Exception:logging.exception('Native Space inspection failed; no workflow action requested')
            return user.CallNextHookEx(None,code,wparam,lparam)
        self.callback=hook
        handle=user.SetWindowsHookExW(13,hook,kernel.GetModuleHandleW(None),0)
        if not handle:
            logging.error('Native Space bridge unavailable: %s. Use the visible Next stage button.',ctypes.get_last_error());return
        try:
            while not self.stop.is_set() and user.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                user.TranslateMessage(ctypes.byref(msg));user.DispatchMessageW(ctypes.byref(msg))
        finally:
            user.UnhookWindowsHookEx(handle);self.thread_id=0
