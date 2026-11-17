from io import StringIO
import ezdxf
import pytest
from app.laser_control.dxf_validator import validate_dxf
from test_laser_control_api import drawing


@pytest.mark.parametrize('content,valid',[
    (drawing(),True),
    (drawing(units=0),False),
    (drawing(units=1),False),
    (drawing(points=[(-1,10),(40,10),(40,40),(-1,40)]),True),
    (drawing(points=[(10,10),(910,10),(910,40),(10,40)]),True),
    (drawing(points=[(10,10),(40,40),(10,40),(40,10)]),False),
])
def test_units_bounds_and_self_intersections(tmp_path,content,valid):
    path=tmp_path/'sample.dxf';path.write_bytes(content)
    result=validate_dxf(path,{'workspace':{'width_mm':900,'height_mm':680}})
    assert result['valid'] is valid,result
    assert path.read_bytes()==content


def test_unsupported_text_is_not_silently_cut(tmp_path):
    doc=ezdxf.new('R2010');doc.units=4;doc.modelspace().add_text('must convert')
    path=tmp_path/'text.dxf';doc.saveas(path)
    result=validate_dxf(path,{})
    assert not result['valid'] and any('TEXT' in error for error in result['errors'])


def test_open_shared_cut_paths_are_explicit(tmp_path):
    doc=ezdxf.new('R2010');doc.units=4;doc.modelspace().add_line((10,10),(50,50))
    path=tmp_path/'open.dxf';doc.saveas(path)
    result=validate_dxf(path,{})
    assert result['valid'] and result['warnings'] and result['layers'][0]['open_paths']==1
