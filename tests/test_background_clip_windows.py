"""Verify the Windows visibility rule used by the background pane; owns all HWNDs."""
import ctypes
from ctypes import wintypes
import sys
import pytest

@pytest.mark.skipif(sys.platform!='win32',reason='Windows native visibility contract')
def test_clipped_native_controls_remain_discoverable_without_appearing_on_screen():
 u=ctypes.WinDLL('user32',use_last_error=True)
 u.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
 u.CreateWindowExW.restype=wintypes.HWND
 u.DestroyWindow.argtypes=[wintypes.HWND];u.IsWindowVisible.argtypes=[wintypes.HWND]
 u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
 # NOACTIVATE + TOOLWINDOW; both windows belong to this test, never LaserCAD.
 parent=u.CreateWindowExW(0x08000080,'STATIC','background-clip-probe',0x90000000,-32000,-32000,200,150,None,None,None,None)
 assert parent
 try:
  child=u.CreateWindowExW(0,'STATIC','background-child',0x50000000,232,182,50,30,parent,None,None,None)
  assert child and u.IsWindowVisible(child)
  outer,inner=wintypes.RECT(),wintypes.RECT()
  assert u.GetWindowRect(parent,ctypes.byref(outer)) and u.GetWindowRect(child,ctypes.byref(inner))
  assert inner.left>=outer.right and inner.top>=outer.bottom
 finally:u.DestroyWindow(parent)
