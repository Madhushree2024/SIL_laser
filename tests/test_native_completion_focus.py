"""Exercise the production native focus policy without sending Windows input."""
from types import SimpleNamespace
import json
import pytest
from app.laser_control.space_keys import native_space_focus, DesktopSpaceKeys, WindowsSpaceFocusAPI

class FocusAPI:
    def __init__(self, *, foreground=30, focus=31, owned=False, panel=False, kind='TButton', flags=0, enabled=True, closed_list=False):
        self.fg,self.fc,self.flags,self.is_enabled=foreground,focus,flags,enabled
        self.owned,self.panel,self.focus_kind,self.closed_list=owned,panel,kind,closed_list
    def foreground(self):return self.fg
    def chain(self,hwnd):
        return {1:[1],10:[10,20,1],20:[20,1],30:[30,10,20,1] if self.owned else [30,20,1],31:[31,30,10,20,1],99:[99]}.get(hwnd,[hwnd])
    def focus(self,foreground):assert foreground==self.fg;return self.fc,self.flags
    def kind(self,hwnd):return self.focus_kind if hwnd==self.fc else ('#32770' if hwnd==30 else 'TMainForm')
    def pid(self,hwnd):return 100 if hwnd==1 or (self.panel and hwnd==self.fc) else 200
    def title(self,hwnd):return 'Control Panel'
    def enabled(self,hwnd):return self.is_enabled
    def closed_readonly_combo(self,hwnd):return self.closed_list

record={'hwnd':10,'pid':200}

def test_owned_lasercad_panel_routes_completion_even_when_its_root_is_not_the_app_form():
    assert native_space_focus(FocusAPI(owned=True),1,record,completion=True)==(True,'completion')

def test_native_host_panel_focus_is_completion_only_never_start_authorization():
    api=FocusAPI(panel=True,kind='WindowsForms10.Window.8.app.0')
    assert native_space_focus(api,1,record,completion=True)==(True,'completion')
    assert native_space_focus(api,1,record,completion=False)==(False,'focus_process_mismatch')

@pytest.mark.parametrize('kind',['TEdit','TComboBox','ComboLBox','Chrome_RenderWidgetHostHWND'])
def test_editing_open_or_editable_dropdowns_and_browser_inputs_are_protected(kind):
    assert native_space_focus(FocusAPI(kind=kind),1,record,completion=True)[0] is False

def test_closed_readonly_usb_dropdown_can_confirm_only_and_open_dropdown_stays_protected():
    api=FocusAPI(kind='TComboBox',closed_list=True)
    assert native_space_focus(api,1,record,completion=True)==(True,'completion')
    assert native_space_focus(api,1,record,completion=False)[0] is False
    api.closed_list=False
    assert native_space_focus(api,1,record,completion=True)[0] is False

@pytest.mark.parametrize('flags',[4,16,32])
def test_menu_navigation_does_not_confirm(flags):
    assert native_space_focus(FocusAPI(flags=flags),1,record,completion=True)==(False,'menu_active')

def test_other_apps_unknown_dialogs_and_disabled_lasercad_are_not_routed():
    assert native_space_focus(FocusAPI(foreground=99),1,record,completion=True)[0] is False
    api=FocusAPI();api.title=lambda hwnd:'Layer Parameters'
    assert native_space_focus(api,1,record,completion=True)==(False,'modal_dialog')
    assert native_space_focus(FocusAPI(enabled=False),1,record,completion=True)==(False,'main_window_disabled')

def test_download_dialog_keeps_existing_start_handoff_but_cannot_confirm_completion():
    api=FocusAPI(enabled=False);api.title=lambda hwnd:'Download Document'
    assert native_space_focus(api,1,record,completion=False)[0] is True
    assert native_space_focus(api,1,record,completion=True)[0] is False

def test_foreground_gui_thread_is_queried_explicitly_instead_of_thread_zero():
    import ctypes
    from ctypes import wintypes
    class Gui(ctypes.Structure):_fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),('focus',wintypes.HWND)]
    calls=[]
    def inspect(thread,pointer):
        calls.append(thread);value=ctypes.cast(pointer,ctypes.POINTER(Gui)).contents;value.focus=31;return True
    api=WindowsSpaceFocusAPI(SimpleNamespace(GetWindowThreadProcessId=lambda hwnd,p:77,GetGUIThreadInfo=inspect),Gui)
    assert api.focus(30)==(31,0);assert calls==[77]

def test_native_completion_dispatch_is_pinned_and_never_calls_generic_start_handler():
    snapshot={'cycle_id':'one','revision':5,'stage':'awaiting_completion'}
    scripts=[];bridge=None
    def evaluate(script):scripts.append(script);bridge.stop.set();return {'status':'reset'}
    bridge=DesktopSpaceKeys(SimpleNamespace(evaluate_js=evaluate),None,completion_ready=lambda:snapshot)
    assert bridge.completion_snapshot()==snapshot
    bridge.queue.put(bridge.completion_snapshot());bridge._dispatch()
    assert len(scripts)==1 and '.nativeComplete(' in scripts[0] and 'nativePress' not in scripts[0]
    assert json.loads(scripts[0].split('.nativeComplete(',1)[1][:-1])==snapshot
    assert not bridge.active

@pytest.mark.parametrize('stage',['ready_process','ready_start','start_pending','processing','interrupted'])
def test_noncompletion_stages_never_produce_completion_context(stage):
    bridge=DesktopSpaceKeys(None,None,completion_ready=lambda:{'stage':stage,'cycle_id':'one','revision':1})
    assert bridge.completion_snapshot() is None and not bridge.completion_key_ready()


@pytest.mark.parametrize('stage',['awaiting_completion','awaiting_manual_completion'])
@pytest.mark.parametrize('focus_case',['owned-panel','host-panel','closed-usb'])
def test_production_hook_consumes_down_repeat_and_up_and_queues_one_completion(monkeypatch,stage,focus_case):
    """Run the real hook callback with simulated Win32 events, not SendInput."""
    import ctypes
    from ctypes import wintypes
    from time import monotonic
    import app.laser_control.space_keys as keys
    snapshot={'cycle_id':'native-cycle','revision':8,'stage':stage}
    captured={};consumed=[]
    class Function:
        def __init__(self,call=lambda *args:0):self.call=call
        def __call__(self,*args):return self.call(*args)
    class API:
        def __init__(self):self.functions={}
        def __getattr__(self,name):
            if name not in self.functions:self.functions[name]=Function()
            return self.functions[name]
    user,kernel=API(),API()
    def install(kind,callback,*args):captured['hook']=callback;return 1
    class Key(ctypes.Structure):
        _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
    def events(*args):
        key=Key();key.vkCode=0x20
        for message in [0x0100,0x0100,0x0101]:consumed.append(captured['hook'](0,message,ctypes.addressof(key)))
        return 0
    user.functions.update(SetWindowsHookExW=Function(install),GetMessageW=Function(events))
    kernel.functions.update(GetCurrentThreadId=Function(lambda:7),GetModuleHandleW=Function(lambda _:1))
    monkeypatch.setattr(keys.ctypes,'WinDLL',lambda name,**kwargs:user if name=='user32' else kernel)
    focus=FocusAPI(owned=True)
    if focus_case=='host-panel':focus=FocusAPI(panel=True,kind='WindowsForms10.Window.8.app.0')
    if focus_case=='closed-usb':focus=FocusAPI(kind='TComboBox',closed_list=True)
    monkeypatch.setattr(keys,'WindowsSpaceFocusAPI',lambda *args:focus)
    host=SimpleNamespace(last={'status':'hosted'},deadline=monotonic()+30,records={10:record})
    window=SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda:1)))
    bridge=DesktopSpaceKeys(window,host,completion_ready=lambda:snapshot)
    bridge._listen()
    assert consumed==[1,1,1]
    assert bridge.queue.get_nowait()==snapshot and bridge.queue.empty()
