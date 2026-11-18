"""Real Windows ABI / hidden-control checks. No laser application or input is used."""
import ctypes
from ctypes import wintypes
import os,sys
import pytest
from app.laser_control.space_keys import WindowsSpaceFocusAPI,ComboBoxInfo

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Requires actual Windows APIs')

class GuiInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),('active',wintypes.HWND),('focus',wintypes.HWND),('capture',wintypes.HWND),('menu',wintypes.HWND),('move',wintypes.HWND),('caret',wintypes.HWND),('rect',wintypes.RECT)]


def native_api():
    user=ctypes.WinDLL('user32',use_last_error=True)
    return user,WindowsSpaceFocusAPI(user,GuiInfo)


def test_real_windows_64bit_handle_and_structure_declarations():
    user,api=native_api()
    assert ctypes.sizeof(ctypes.c_void_p)==8,'This validation requires 64-bit Python'
    for name in ['GetForegroundWindow','GetParent','GetWindow']:
        assert getattr(user,name).restype is wintypes.HWND
        assert ctypes.sizeof(getattr(user,name).restype)==8
    assert user.GetWindow.argtypes==[wintypes.HWND,wintypes.UINT]
    assert user.GetWindowLongW.restype is wintypes.LONG and ctypes.sizeof(wintypes.LONG)==4
    assert user.GetComboBoxInfo.argtypes==[wintypes.HWND,ctypes.POINTER(ComboBoxInfo)]
    assert ctypes.sizeof(ComboBoxInfo)==64 and ctypes.sizeof(GuiInfo)==72
    foreground=api.foreground()
    if foreground:
        assert api.kind(foreground)
        api.title(foreground);api.pid(foreground);api.focus(foreground);list(api.chain(foreground))


def test_real_hidden_windows_owner_chain_and_readonly_combo():
    user,api=native_api();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel.GetModuleHandleW.restype=wintypes.HMODULE
    instance=kernel.GetModuleHandleW(None)
    user.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,wintypes.LPVOID]
    user.CreateWindowExW.restype=wintypes.HWND
    user.DestroyWindow.argtypes=[wintypes.HWND];user.DestroyWindow.restype=wintypes.BOOL
    windows=[]
    def create(kind,style,parent=None):
        hwnd=user.CreateWindowExW(0,kind,'Completion ABI test (hidden)',style,0,0,120,60,parent,None,instance,None)
        assert hwnd,ctypes.get_last_error();windows.append(hwnd);return hwnd
    try:
        main=create('STATIC',0x80000000)
        owned=create('STATIC',0x80000000,main)
        combo=create('COMBOBOX',0x40000003,main)
        editable=create('COMBOBOX',0x40000002,main)
        assert main in list(api.chain(owned)) and main in list(api.chain(combo))
        assert api.pid(main)==os.getpid() and api.enabled(main)
        assert not user.IsWindowVisible(main)
        assert api.closed_readonly_combo(combo)
        assert not api.closed_readonly_combo(editable)
    finally:
        for hwnd in reversed(windows):assert user.DestroyWindow(hwnd)


def test_real_windows_low_level_hook_installs_and_unhooks_without_injecting_keys():
    user,api=native_api();kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    result_type=ctypes.c_ssize_t
    callback_type=ctypes.WINFUNCTYPE(result_type,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
    user.CallNextHookEx.argtypes=[wintypes.HHOOK,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM];user.CallNextHookEx.restype=result_type
    user.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,wintypes.HINSTANCE,wintypes.DWORD];user.SetWindowsHookExW.restype=wintypes.HHOOK
    user.UnhookWindowsHookEx.argtypes=[wintypes.HHOOK];user.UnhookWindowsHookEx.restype=wintypes.BOOL
    kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel.GetModuleHandleW.restype=wintypes.HMODULE
    @callback_type
    def passthrough(code,wparam,lparam):return user.CallNextHookEx(None,code,wparam,lparam)
    handle=user.SetWindowsHookExW(13,passthrough,kernel.GetModuleHandleW(None),0)
    assert handle,ctypes.get_last_error()
    assert user.UnhookWindowsHookEx(handle)
