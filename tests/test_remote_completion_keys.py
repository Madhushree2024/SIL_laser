"""Remote-key flags exercise the production hook without sending OS input."""
import ctypes
from ctypes import wintypes
from time import monotonic
from types import SimpleNamespace
import pytest
import app.laser_control.space_keys as keys
from test_native_completion_focus import FocusAPI,record


def run_hook(monkeypatch, *, stage='awaiting_completion',focus=None,expired=False,active=False,modified=False,steps=None):
    state={'cycle_id':'remote-cycle','revision':8,'stage':stage,'job':{'path':'job.dxf'}}
    callbacks={};consumed=[];delivered=[];bridge=None
    class Function:
        def __init__(self,fn=lambda *args:0):self.fn=fn
        def __call__(self,*args):return self.fn(*args)
    class API:
        def __init__(self):self.functions={}
        def __getattr__(self,name):
            if name not in self.functions:self.functions[name]=Function()
            return self.functions[name]
    user,kernel=API(),API()
    class Key(ctypes.Structure):
        _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
    def install(kind,callback,*args):callbacks['hook']=callback;return 1
    def events(*args):
        sequence=steps or [(0x0100,0x10),(0x0100,0x10),(0x0101,0x10)]
        for item in sequence:
            if callable(item):item(state,bridge,delivered);continue
            message,flags=item;key=Key();key.vkCode=0x20;key.flags=flags
            consumed.append(callbacks['hook'](0,message,ctypes.addressof(key)))
        return 0
    user.functions.update(SetWindowsHookExW=Function(install),GetMessageW=Function(events),GetAsyncKeyState=Function(lambda *args:0x8000 if modified else 0))
    kernel.functions.update(GetCurrentThreadId=Function(lambda:7),GetModuleHandleW=Function(lambda _:1))
    monkeypatch.setattr(keys.ctypes,'WinDLL',lambda name,**kwargs:user if name=='user32' else kernel)
    api=focus or FocusAPI(owned=True)
    monkeypatch.setattr(keys,'WindowsSpaceFocusAPI',lambda *args:api)
    host=SimpleNamespace(last={'status':'hosted'},deadline=monotonic()+(-1 if expired else 30),records={10:record})
    window=SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda:1)))
    bridge=keys.DesktopSpaceKeys(window,host,completion_ready=lambda:dict(state));bridge.active=active
    bridge._listen()
    while not bridge.queue.empty():delivered.append(bridge.queue.get_nowait())
    return consumed,delivered,bridge


@pytest.mark.parametrize('stage',['ready_process','ready_start','start_pending','processing','interrupted'])
def test_injected_space_cannot_authorize_first_or_second_stage(monkeypatch,stage):
    consumed,requests,_=run_hook(monkeypatch,stage=stage)
    assert consumed==[0,0,0] and requests==[]


def test_injected_completion_outside_our_app_is_untouched(monkeypatch):
    consumed,requests,_=run_hook(monkeypatch,focus=FocusAPI(foreground=99))
    assert consumed==[0,0,0] and requests==[]


@pytest.mark.parametrize('kind',['TEdit','TComboBox','ComboLBox','Chrome_RenderWidgetHostHWND'])
def test_injected_completion_preserves_editors_dropdowns_and_dom_routing(monkeypatch,kind):
    consumed,requests,_=run_hook(monkeypatch,focus=FocusAPI(kind=kind))
    assert consumed==[0,0,0] and all('blocked' in request for request in requests)


def test_modified_remote_space_is_not_a_completion_confirmation(monkeypatch):
    consumed,requests,_=run_hook(monkeypatch,modified=True)
    assert consumed==[0,0,0] and requests==[]


def test_remote_completion_is_consumed_while_another_dispatch_is_pending(monkeypatch):
    consumed,requests,_=run_hook(monkeypatch,active=True)
    assert consumed==[1,1,1] and requests==[]


def test_remote_key_release_and_repeats_stay_consumed_after_reset_changes_state(monkeypatch):
    def reset(state,bridge,delivered):
        state.update(stage='ready_process',cycle_id='next-cycle',revision=0,job=None,reset_from={'cycle_id':'remote-cycle','revision':8,'completed':True,'operator_confirmed_idle':True})
    consumed,requests,_=run_hook(monkeypatch,steps=[(0x0100,0x10),reset,(0x0100,0x10),(0x0101,0x10)])
    assert consumed==[1,1,1] and len(requests)==1 and requests[0]['stage']=='awaiting_completion'


def test_pending_navigation_receipt_routes_next_remote_press_only_to_completion(monkeypatch):
    def reset_and_dispatch(state,bridge,delivered):
        delivered.append(bridge.queue.get_nowait());bridge.active=False
        state.update(stage='ready_process',cycle_id='next-cycle',revision=0,job=None,reset_from={'cycle_id':'remote-cycle','revision':8,'completed':True,'operator_confirmed_idle':True})
    consumed,requests,_=run_hook(monkeypatch,steps=[(0x0100,0x10),(0x0101,0x10),reset_and_dispatch,(0x0100,0x10),(0x0101,0x10)])
    assert consumed==[1,1,1,1] and len(requests)==2
    assert all(request['cycle_id']=='remote-cycle' and request['key_source']=='remote_injected' for request in requests)
    assert all(request!='Space' for request in requests)


def start_focus():
    api=FocusAPI(owned=True)
    api.title=lambda hwnd:'Start' if hwnd==api.fc else 'Control Panel'
    return api


def test_remote_start_focus_cannot_fall_through_when_the_host_lease_is_stale(monkeypatch):
    consumed,requests,_=run_hook(monkeypatch,focus=start_focus(),expired=True)
    assert consumed==[1,1,1] and len(requests)==1 and requests[0]['key_source']=='remote_injected'


def test_remote_start_focus_guard_cannot_claim_a_detached_window(monkeypatch):
    api=start_focus();chain=api.chain;api.chain=lambda hwnd:[10] if hwnd==10 else chain(hwnd)
    consumed,requests,_=run_hook(monkeypatch,focus=api,expired=True)
    assert consumed==[0,0,0] and all('blocked' in request for request in requests)


def test_remote_payload_dispatches_only_native_complete_never_generic_press():
    scripts=[];bridge=None
    def evaluate(script):scripts.append(script);bridge.stop.set();return {'status':'reset'}
    bridge=keys.DesktopSpaceKeys(SimpleNamespace(evaluate_js=evaluate),None)
    bridge.queue.put({'cycle_id':'remote-cycle','revision':8,'stage':'awaiting_completion','key_source':'remote_injected'})
    bridge._dispatch()
    assert len(scripts)==1 and '.nativeComplete(' in scripts[0] and '.nativePress(' not in scripts[0]



def test_held_remote_second_space_cannot_become_completion_after_start(monkeypatch):
    def started(state,bridge,delivered):state.update(stage='awaiting_completion',revision=9)
    consumed,requests,_=run_hook(monkeypatch,stage='ready_start',steps=[(0x0100,0x10),started,(0x0100,0x10),(0x0101,0x10),(0x0100,0x10),(0x0101,0x10)])
    assert consumed==[0,1,1,1,1]
    assert len(requests)==1 and requests[0]['stage']=='awaiting_completion' and requests[0]['revision']==9



@pytest.mark.parametrize('case',['menu','modal','disabled'])
def test_start_focus_fallback_preserves_menus_and_modal_dialogs(monkeypatch,case):
    api=start_focus()
    if case=='menu':api.flags=4
    if case=='modal':api.title=lambda hwnd:'Start' if hwnd==api.fc else 'Layer Parameters'
    if case=='disabled':api.is_enabled=False
    consumed,requests,_=run_hook(monkeypatch,focus=api,expired=True)
    assert consumed==[0,0,0] and all('blocked' in request for request in requests)
