from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.laser_control.lasercad_host import DesktopLaserCADHost


class Surface:
    def __init__(self): self.visible=False; self.disposed=False
    def invoke(self, work): return work()
    def prepare(self, area, expire): self.expire=expire; return 100, int(area['width']), int(area['height'])
    def show(self): self.visible=True
    def hide(self): self.visible=False
    def dispose(self): self.disposed=True


class API:
    def __init__(self): self.calls=[];self.compatible=True;self.activations=[]
    def snapshot(self, window): return dict(window)
    def attach(self, host, window, *, activate=True):
        self.activations.append(activate)
        if not self.compatible: raise ValueError('incompatible display-scaling mode')
        self.calls.append(('attach',window['hwnd']))
    def fit(self, window, width, height, visible, *, activate=True):
        self.activations.append(activate);self.calls.append(('fit',window['hwnd'],width,height,visible))
    def restore(self, window): self.calls.append(('restore',window['hwnd']))
    def show(self, window): self.calls.append(('show',window['hwnd']))


@pytest.fixture
def host():
    selected={'hwnd':1,'pid':20,'executable':'LaserCAD.exe','title':'LaserCAD - first.dxf'}
    surface=Surface();api=API();clock=[0]
    manager=DesktopLaserCADHost(lambda:None,lambda:dict(selected),api,surface,lambda:clock[0])
    return manager,api,surface,selected,clock


def area(**kwargs): return {'action':'show','x':0,'y':100,'width':800,'height':600,'viewport_width':1000,'viewport_height':800,**kwargs}


def test_host_attach_resize_hide_and_close_are_only_window_operations(host):
    manager,api,surface,selected,clock=host
    assert manager.request(area())['status']=='hosted' and surface.visible
    manager.request(area(width=700))
    assert [c[0] for c in api.calls].count('attach')==1
    assert api.calls[-1]==('fit',1,700,600,True)
    manager.request({'action':'hide'});assert not surface.visible
    manager.close();assert surface.disposed and api.calls[-1]==('restore',1)


def test_host_lease_hides_stale_native_pane(host):
    manager,api,surface,selected,clock=host
    manager.request(area());clock[0]=3;manager.expire()
    assert not surface.visible and manager.request({'action':'status'})['status']=='hidden'
    assert not any(c[0]=='restore' for c in api.calls)


def test_switching_jobs_keeps_previous_native_document_hidden_and_restores_both(host):
    manager,api,surface,selected,clock=host
    manager.request(area());selected.update(hwnd=2,title='LaserCAD - second.dxf')
    manager.request(area())
    assert ('fit',1,800,600,False) in api.calls and api.calls[-1]==('fit',2,800,600,True)
    manager.request({'action':'detach'})
    assert {c[1] for c in api.calls if c[0]=='restore'}=={1,2}


def test_incompatible_dpi_never_attaches_and_separate_view_remains_available(host):
    manager,api,surface,selected,clock=host;api.compatible=False
    with pytest.raises(ValueError,match='scaling'):manager.request(area())
    assert not api.calls and not surface.visible
    assert manager.request({'action':'status'})['status']=='unavailable'
    manager.request({'action':'detach'});assert api.calls==[('show',1)]


def test_close_without_a_lasercad_window_never_launches_or_discovers_an_app(host):
    manager,api,surface,selected,clock=host
    manager.target_window=lambda:pytest.fail('Must not discover an app during unused-host close')
    manager.close();assert not api.calls and surface.disposed


def test_invalid_size_or_busy_host_does_not_change_window(host):
    manager,api,surface,selected,clock=host
    with pytest.raises(ValueError):manager.request(area(width=0))
    assert not api.calls
    manager.lock.acquire()
    try:
        with pytest.raises(ValueError,match='updating'):manager.request(area())
    finally:manager.lock.release()
    assert not api.calls


def test_host_endpoint_works_during_cutting_but_never_invokes_machine_bridge(monkeypatch):
    import app.main as main
    from test_laser_control_api import laser_workspace
    # All storage/state is replaced for this route-only test.
    calls=[]
    cycles=SimpleNamespace(status=lambda:{'active':{'state':'awaiting_completion'}})
    monkeypatch.setattr(main.laser_control_service,'cycles',lambda:cycles)
    monkeypatch.setattr(main.laser_control_service,'host_callback',lambda payload:calls.append(payload) or {'status':'hosted','supported':True})
    monkeypatch.setattr(main.lasercad_controller,'request',lambda *a,**k:pytest.fail('No machine commands from host'))
    client=TestClient(main.app)
    response=client.post('/api/laser-control/lasercad-host',json=area())
    assert response.status_code==200,response.text
    assert calls[0]['action']=='show'
    assert client.post('/api/laser-control/lasercad-host',json=area(width=-1)).status_code==422


def test_host_route_reports_browser_limitation(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main.laser_control_service,'cycles',lambda:SimpleNamespace(status=lambda:{'active':None}))
    monkeypatch.setattr(main.laser_control_service,'host_callback',None)
    client=TestClient(main.app)
    assert client.get('/api/laser-control/lasercad-host').json()['supported'] is False
    result=client.post('/api/laser-control/lasercad-host',json=area())
    assert result.status_code==409 and 'desktop' in result.json()['detail']



def test_background_host_is_clipped_not_presented_and_lease_hides_it(host):
 manager,api,surface,selected,clock=host
 surface.background=lambda:setattr(surface,'background_prepared',True)
 api.window_metadata=lambda hwnd:dict(selected)
 result=manager.request({'action':'background','_background_window':dict(selected)})
 assert result['status']=='background' and surface.background_prepared and surface.visible is False
 assert api.activations==[False,False]
 assert ('fit',1,1280,900,True) in api.calls and not any(c[0]=='show' for c in api.calls)
 clock[0]=31;manager.expire();assert manager.last['status']=='hidden'
 manager.request(area());assert manager.last['status']=='hosted' and surface.visible


def test_background_host_rejects_changed_window_identity(host):
 manager,api,surface,selected,clock=host
 api.window_metadata=lambda hwnd:{**selected,'pid':999}
 with pytest.raises(ValueError,match='changed'):manager.request({'action':'background','_background_window':dict(selected)})
 assert not api.calls and not surface.visible
