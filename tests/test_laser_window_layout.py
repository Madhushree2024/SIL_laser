from copy import deepcopy
import pytest
from app.laser_control.window_layout import DesktopSplitLayout


class Windows:
    def __init__(self):
        self.windows={1:{'hwnd':1,'pid':10,'class':'app','rect':[10,20,1000,700],'show':3},
                      2:{'hwnd':2,'pid':20,'class':'cad','rect':[40,50,900,650],'show':1}}
        self.area=[-1600,0,1600,860]
        self.moves=[]
    def snapshot(self,hwnd):
        if hwnd not in self.windows: raise ValueError('closed')
        return deepcopy(self.windows[hwnd])
    def work_area(self,hwnd):return self.area
    def place(self,hwnd,rect,show=1):self.moves.append((hwnd,rect,show))


def layout(api):return DesktopSplitLayout(lambda:1,lambda:{'hwnd':2,'pid':20},api,10)


def test_split_and_restore_work_on_negative_monitor_coordinates():
    api=Windows();item=layout(api)
    assert item.request('split')['status']=='split'
    assert api.moves==[(1,[-1600,0,800,860],1),(2,[-800,0,800,860],1)]
    item.request('split')
    assert item.request('restore')['windows']==2
    assert api.moves[-2:]==[(1,[10,20,1000,700],3),(2,[40,50,900,650],1)]
    assert item.request('restore')['windows']==0


@pytest.mark.parametrize('bad',['app_pid','cad_pid','small_screen'])
def test_invalid_identity_or_screen_never_moves_windows(bad):
    api=Windows();item=layout(api)
    if bad=='app_pid':api.windows[1]['pid']=99
    elif bad=='cad_pid':api.windows[2]['pid']=99
    else:api.area=[0,0,1000,500]
    with pytest.raises(ValueError):item.request('split')
    assert not api.moves


def test_restore_skips_closed_or_reused_window():
    api=Windows();item=layout(api);item.request('split')
    del api.windows[1];api.windows[2]['class']='unrelated'
    assert item.request('restore')['windows']==0
    assert len(api.moves)==2


def test_lasercad_unavailable_is_recoverable_layout_error():
    def unavailable():raise RuntimeError('No matching window')
    api=Windows();item=DesktopSplitLayout(lambda:1,unavailable,api,10)
    with pytest.raises(ValueError,match='No matching'):item.request('split')
    assert not api.moves
