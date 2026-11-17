from pathlib import Path
import hashlib
import json,yaml
from types import SimpleNamespace
import pytest
import ezdxf
from app.export.lasercad_bed_mapping import prepare_bed_copy,verify_import_snapshot
from app.export import lasercad_import_coordinates as prefs


@pytest.fixture
def layout(tmp_path):
    exe=tmp_path/'LaserCAD/AWCPrograms/LaserCAD.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'test, never executed')
    settings=exe.parent.parent/'AWCCfg';settings.mkdir()
    (settings/'SysCfg.ini').write_text('[X]\nRange=950\n[Y]\nRange=650\n')
    (settings/'SoftCfg.ini').write_text('[BaseCfg]\nMachZeroPos=2\n[ImportFile]\nImportDockLoction=0\nDxfUnit=0\nIf90Rotate=0\n[UserCfg]\nPageZeroPos=2\n')
    path=tmp_path/'data/projects/job/exports/nest.dxf';path.parent.mkdir(parents=True)
    doc=ezdxf.new('R2010');doc.units=ezdxf.units.MM;doc.layers.add('CUT',color=1)
    doc.modelspace().add_lwpolyline([(-80,0),(0,0),(0,-20),(-25,-20),(-25,-50),(-80,-50)],close=True,dxfattribs={'layer':'CUT'})
    doc.modelspace().add_lwpolyline([(-120,-15),(-95,-15),(-95,-40),(-120,-40)],close=True,dxfattribs={'layer':'CUT'})
    doc.saveas(path)
    config={'workspace':{'width_mm':950,'height_mm':650,'coordinate_frame':'id12_negative_xy'},'lasercad':{}}
    (tmp_path/'app/config').mkdir(parents=True)
    (tmp_path/'app/config/config.yaml').write_text(yaml.safe_dump(config))
    (tmp_path/'data/calibration').mkdir(parents=True)
    working=[{'marker_id':11,'x_mm':-755,'y_mm':0},{'marker_id':12,'x_mm':0,'y_mm':0},
             {'marker_id':13,'x_mm':0,'y_mm':-400},{'marker_id':10,'x_mm':-755,'y_mm':-400}]
    layout={'dictionary':'DICT_4X4_50','markers':working}
    (tmp_path/'data/calibration/aruco_viewer_bed_layout.json').write_text(json.dumps(layout))
    from app.calibration.machine_placement import save_machine_placement
    from app.calibration.aruco_bed import BedCalibrationRequest
    measured=BedCalibrationRequest(markers=[{'marker_id':m['marker_id'],'x_mm':100-m['x_mm'],'y_mm':80-m['y_mm']} for m in working])
    save_machine_placement(tmp_path,measured,config)
    path.with_suffix('.alignment.json').write_text(json.dumps({'alignment':{'reference':'templates_top_right'},
       'design_reference_mm':[-50,-60],'matrix':[[1,0,50],[0,1,60],[0,0,1]],
       'measurement_calibration':{'coordinate_frame':'id12_negative_xy','dictionary':'DICT_4X4_50','bed_markers':working}}))
    return exe,path,config


def test_canonical_zero_preserved_and_import_is_inside_bed_without_reflection(layout):
    exe,path,config=layout;original=path.read_bytes()
    mapping=prepare_bed_copy(path,config,exe)
    assert path.read_bytes()==original
    source=list(ezdxf.readfile(path).modelspace());target=list(ezdxf.readfile(mapping['import_path']).modelspace())
    for a,b in zip(source,target):
        old=list(a.get_points());new=list(b.get_points())
        assert len(old)==len(new)
        for p,q in zip(old,new):
            assert tuple(q[:2])==pytest.approx((p[0]+800,p[1]+510))
            assert 0<=q[0]<=950 and 0<=q[1]<=650
            # Page Right_Up readout: positive left and down, same visual shape.
            assert (950-q[0],650-q[1])==pytest.approx((150-p[0],140-p[1]))
    assert mapping['import_dxf_reference_mm']==pytest.approx([800,510])
    assert mapping['lasercad_page_reference_mm']==pytest.approx([150,140])
    verify_import_snapshot(path,mapping)
    assert prepare_bed_copy(path,config,exe)['import_sha256']==mapping['import_sha256']


def test_oversize_does_not_scale_or_open(layout):
    exe,path,config=layout;doc=ezdxf.readfile(path)
    next(iter(doc.modelspace())).scale(20,1,1);doc.saveas(path)
    with pytest.raises(ValueError,match='outside the machine bed'):prepare_bed_copy(path,config,exe)
    assert not (path.parent/'lasercad-import').exists()


def test_bed_mismatch_blocks_without_touching_machine_parameters(layout):
    exe,path,config=layout
    settings=exe.parent.parent/'AWCCfg/SysCfg.ini';settings.write_text('[X]\nRange=900\n[Y]\nRange=650\n');before=settings.read_bytes()
    with pytest.raises(ValueError,match='differs from LaserCAD'):prepare_bed_copy(path,config,exe)
    assert settings.read_bytes()==before


def test_import_copy_tampering_is_detected(layout):
    exe,path,config=layout;mapping=prepare_bed_copy(path,config,exe)
    copy=Path(mapping['import_path']);copy.write_bytes(copy.read_bytes()+b'\nchanged')
    with pytest.raises(ValueError,match='import DXF changed'):verify_import_snapshot(path,mapping)


@pytest.mark.parametrize('encoding',['latin-1','utf-8-sig','utf-16le','utf-16be'])
def test_only_page_zero_changes_not_machine_zero(encoding):
    text='[BaseCfg]\r\nMachZeroPos=2\r\n[UserCfg]\r\nPageZeroPos = 3 ; page\r\nCom=4\r\n'
    prefix=b'\xff\xfe' if encoding=='utf-16le' else b'\xfe\xff' if encoding=='utf-16be' else b''
    data=prefix+text.encode(encoding)
    updated=prefs.top_right_page_bytes(data)
    assert updated==prefix+text.replace('= 3 ; page','= 2 ; page').encode(encoding)
    assert prefs.top_right_page_bytes(updated)==updated


def test_live_process_requires_close_for_changed_page_origin(layout,monkeypatch):
    exe,path,config=layout;settings=exe.parent.parent/'AWCCfg/SoftCfg.ini'
    settings.write_text(settings.read_text().replace('PageZeroPos=2','PageZeroPos=3'))
    before=settings.read_bytes()
    monkeypatch.setattr(prefs,'SUPPORTED_EXECUTABLE_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest())
    monkeypatch.setattr(prefs,'running_lasercad',lambda:True)
    with pytest.raises(ValueError,match='Close LaserCAD'):prefs.prepare_import_coordinates(exe,page_top_right=True)
    assert settings.read_bytes()==before


def test_handoff_opens_import_copy_with_same_document_name(layout,monkeypatch):
    import app.export.lasercad as cad
    exe,path,config=layout;calls=[]
    monkeypatch.setattr(cad.sys,'platform','win32')
    monkeypatch.setattr(cad,'find_lasercad',lambda _:exe)
    monkeypatch.setattr(cad,'prepare_import_coordinates',lambda *a,**k: {'page_zero':'Right_Up'})
    monkeypatch.setattr(cad.subprocess,'Popen',lambda args,**kw: calls.append(args) or SimpleNamespace(pid=123))
    monkeypatch.setattr(cad,'prepare_lasercad_preview',lambda *a,**k:{'status':'refresh_requested','ready':True})
    result=cad.open_dxf_in_lasercad(path,config)
    assert result['status']=='launch_requested'
    assert Path(calls[0][1]).name==path.name and Path(calls[0][1])!=path
    assert result['import_mapping']['source_path']==str(path.resolve())
    assert 'photographed machine position' in result['message']
