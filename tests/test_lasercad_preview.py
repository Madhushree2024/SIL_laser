from pathlib import Path
import pytest
from app.export import lasercad_preview as preview


class PreviewAPI:
    def __init__(self, windows, foreground=True):
        self.items=windows
        self.calls=[]
        self.foreground=foreground
    def windows(self):return self.items
    def ready(self,w):self.calls.append(('ready',w['hwnd']));return True
    def show(self,w):self.calls.append(('show',w['hwnd']));return self.foreground
    def visible(self,w):return self.foreground
    def zoom_all(self,w):self.calls.append(('zoom',w['hwnd']));return True
    def redraw(self,w):self.calls.append(('redraw',w['hwnd']));return True


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(preview.time,'sleep',lambda _:None)


def window(exe, title, pid=12, hwnd=123):
    return dict(executable=str(exe),title=title,pid=pid,hwnd=hwnd)


def test_only_matching_executable_and_document_are_activated(tmp_path):
    exe=tmp_path/'LaserCAD.exe';dxf=tmp_path/'cutting.dxf'
    api=PreviewAPI([window(tmp_path/'Other.exe','cutting.dxf',hwnd=1),window(exe,'other.dxf',hwnd=2),window(exe,'LaserCAD - cutting.dxf')])
    r=preview.prepare_lasercad_preview(12,exe,dxf,api=api,timeout=0)
    assert r['status']=='refresh_requested'
    assert api.calls==[('ready',123),('show',123),('zoom',123),('redraw',123)]


def test_existing_single_instance_can_receive_new_document(tmp_path):
    exe=tmp_path/'LaserCAD.exe';api=PreviewAPI([window(exe,'LaserCAD - cutting.dxf',pid=99)])
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=0)
    assert r['pid']==99 and r['status']=='refresh_requested'


def test_ambiguous_existing_windows_are_not_touched(tmp_path):
    exe=tmp_path/'LaserCAD.exe';api=PreviewAPI([window(exe,'cutting.dxf',pid=21),window(exe,'cutting.dxf',pid=22)])
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=0)
    assert r['status']=='warning' and not api.calls


def test_new_process_wins_over_same_filename_in_an_older_instance(tmp_path):
    exe=tmp_path/'LaserCAD.exe';api=PreviewAPI([window(exe,'cutting.dxf',pid=21,hwnd=1),window(exe,'cutting.dxf')])
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=0)
    assert r['pid']==12 and all(hwnd==123 for _,hwnd in api.calls)


def test_windows_foreground_denial_is_reported_without_editing_document(tmp_path):
    exe=tmp_path/'LaserCAD.exe';api=PreviewAPI([window(exe,'cutting.dxf')],foreground=False)
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=0)
    assert r['status']=='warning' and r['redraw_requested']


def test_missing_document_is_bounded_and_does_not_activate_another_file(tmp_path):
    exe=tmp_path/'LaserCAD.exe';api=PreviewAPI([window(exe,'other.dxf')])
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=0)
    assert r['status']=='warning' and not api.calls


def test_delayed_drawing_view_is_retried_until_ready(tmp_path):
    exe=tmp_path/'LaserCAD.exe'
    class Delayed(PreviewAPI):
        def __init__(self):super().__init__([window(exe,'cutting.dxf')]);self.probes=0
        def ready(self,w):self.probes+=1;return self.probes>=3
    api=Delayed()
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=10)
    assert len(r['attempts'])==4
    assert [a['ready'] for a in r['attempts']]==[False,False,True,True]
    assert r['status']=='refresh_requested'
    assert len([a for a in api.calls if a[0]=='redraw'])==2


def test_focus_can_recover_on_later_refresh(tmp_path):
    exe=tmp_path/'LaserCAD.exe'
    class FocusLater(PreviewAPI):
        def show(self,w):self.calls.append(('show',w['hwnd']));return len(self.calls)>=7
    api=FocusLater([window(exe,'cutting.dxf')])
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=10)
    assert len(r['attempts'])==4 and r['status']=='refresh_requested'
    assert not r['attempts'][0]['activated'] and r['attempts'][-1]['activated']


def test_document_change_during_delayed_refresh_aborts(tmp_path):
    exe=tmp_path/'LaserCAD.exe'
    class Changes(PreviewAPI):
        def __init__(self):super().__init__([]);self.reads=0
        def windows(self):
            self.reads+=1
            return [window(exe,'cutting.dxf' if self.reads<=2 else 'other.dxf')]
    api=Changes()
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=10)
    assert r['status']=='warning' and len(r['attempts'])==1
    assert len([a for a in api.calls if a[0]=='show'])==1


@pytest.mark.parametrize('encoding',['utf-8','utf-16'])
@pytest.mark.parametrize('label,allowed',[('Zoom to all objects',True),('Start',False)])
def test_custom_command_bar_uses_only_verified_zoom_resource(tmp_path,label,allowed,encoding):
    from types import SimpleNamespace
    exe=tmp_path/'AWCPrograms'/'LaserCAD.exe'
    language=tmp_path/'AWCLanguage'/'lang_Enu.ini';language.parent.mkdir()
    language.write_text('32796='+label,encoding=encoding)
    sent=[]
    api=preview.WindowsPreviewAPI.__new__(preview.WindowsPreviewAPI)
    api.user=SimpleNamespace(GetMenu=lambda h:None,SendMessageTimeoutW=lambda *a:sent.append(a) or 1)
    assert api.zoom_all(window(exe,'cutting.dxf')) is allowed
    assert bool(sent) is allowed
    if allowed:assert sent[0][1:3]==(0x0111,32796)


def test_preview_can_be_presented_without_stealing_keyboard_focus(tmp_path):
    exe=tmp_path/'LaserCAD.exe'
    api=PreviewAPI([window(exe,'cutting.dxf')],foreground=False)
    api.visible=lambda _:True
    r=preview.prepare_lasercad_preview(12,exe,tmp_path/'cutting.dxf',api=api,timeout=10)
    assert r['status']=='refresh_requested'
    assert not r['activated'] and r['visible'] and r['zoom_requested']
