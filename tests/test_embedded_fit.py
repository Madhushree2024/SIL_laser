import pytest
from app.laser_control.lasercad_host import canvas_size, embedded_style

@pytest.mark.parametrize('area,minimum,expected',[
    ((1920,1000),(1024,720),(1920,1000)),
    ((1000,600),(1024,720),(1024,720)),
    ((1100,600),(1024,720),(1083,720)),
    ((1000,800),(1024,720),(1024,783)),
    ((1030,700),(1024,720),(1024,720)),
])
def test_canvas_never_clips_required_controls_and_accounts_for_both_scrollbars(area,minimum,expected):
    assert canvas_size(*area,*minimum,17,17)==expected

@pytest.mark.parametrize('state',[0,0x01000000,0x20000000])
def test_host_style_removes_top_level_and_maximized_state(state):
    result=embedded_style(0x80000000|0x00C00000|0x00040000|state)
    assert result & 0x40000000
    assert not result & (0x80000000|0x00C00000|0x00040000|0x21000000)


def test_supplier_minimum_reaches_surface_without_machine_commands():
    from test_lasercad_host import API, Surface, area
    from app.laser_control.lasercad_host import DesktopLaserCADHost
    api=API();api.minimum_size=lambda record:(1100,760)
    surface=Surface();seen=[]
    original=surface.prepare
    def prepare(payload,expire):
        seen.append(payload['minimum_size'])
        host,w,h=original(payload,expire)
        return host,max(w,payload['minimum_size'][0]),max(h,payload['minimum_size'][1])
    surface.prepare=prepare
    manager=DesktopLaserCADHost(lambda:None,lambda:{'hwnd':1,'pid':20,'executable':'LaserCAD.exe','title':'LaserCAD'},api,surface)
    manager.request(area())
    assert seen==[(1100,760)]
    assert api.calls==[('attach',1),('fit',1,1100,760,True)]
    manager.close()
