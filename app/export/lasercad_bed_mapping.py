"""Preserve photographed machine placement; canonical DXF job origin stays local."""
from pathlib import Path
import configparser,hashlib,json,math
from uuid import uuid4
import numpy as np
import ezdxf
from ezdxf import bbox
from ezdxf.math import Matrix44
from app.calibration.machine_placement import load_machine_placement,profile_path
from app.calibration.aruco_bed import BedCalibrationRequest
from app.calibration.workspace_layout_store import profile_uses_layout


def enabled(config):
    # The old preserve_top_right_on_bed switch no longer enables a bed-corner fallback.
    return config.get('workspace',{}).get('coordinate_frame')=='id12_negative_xy'


def _hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ini(path):
    data=Path(path).read_bytes()
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'latin-1'
    result=configparser.ConfigParser(interpolation=None,strict=True);result.read_string(data.decode(encoding));return result


def _workspace(source):
    for parent in source.parents:
        if (parent/'app/config/config.yaml').is_file():return parent
    raise ValueError('This DXF has no saved photo/workspace reference. Capture, nest and export a new job from the app.')


def prepare_bed_copy(source, config, executable):
    source=Path(source).resolve();root=_workspace(source)
    width=float(config['workspace']['width_mm']);height=float(config['workspace']['height_mm'])
    if not all(math.isfinite(v) and v>0 for v in (width,height)):raise ValueError('Enter a valid laser bed width and height.')
    placement=load_machine_placement(root,config)
    pp=profile_path(root);lp=root/'data/calibration/aruco_viewer_bed_layout.json';ap=source.with_suffix('.alignment.json')
    snapshots={str(p):_hash(p) for p in (pp,lp)}
    if not ap.is_file():raise ValueError('The photo-position reference for this DXF is missing. Capture, detect, nest and export again.')
    alignment=json.loads(ap.read_text(encoding='utf-8'));snapshots[str(ap)]=_hash(ap)
    reference=np.asarray(alignment.get('design_reference_mm'),dtype=float)
    if reference.shape!=(2,) or not np.isfinite(reference).all():raise ValueError('The saved job origin is invalid.')
    expected=np.eye(3);expected[:2,2]=-reference
    if (alignment.get('alignment',{}).get('reference')!='templates_top_right' or
            not np.allclose(alignment.get('matrix'),expected,rtol=0,atol=1e-7)):
        raise ValueError('This DXF uses an older/custom origin. Re-export it with local template-box job origin (0,0).')
    layout=BedCalibrationRequest(markers=placement['working_markers'],dictionary=placement['dictionary'])
    measured=alignment.get('measurement_calibration') or {}
    if measured.get('coordinate_frame')!='id12_negative_xy' or not profile_uses_layout(measured,layout):
        raise ValueError('This photo/DXF uses different marker measurements. Capture and nest again using the current saved layout.')
    native=_ini(Path(executable).parent.parent/'AWCCfg/SysCfg.ini')
    nw,nh=native.getfloat('X','Range'),native.getfloat('Y','Range')
    if not (math.isclose(nw,width,abs_tol=1e-6) and math.isclose(nh,height,abs_tol=1e-6)):
        raise ValueError(f'App bed {width:g} x {height:g} mm differs from LaserCAD {nw:g} x {nh:g} mm. Match the selected software working areas; machine parameters were not changed.')
    preferences=_ini(Path(executable).parent.parent/'AWCCfg/SoftCfg.ini')
    if preferences.getint('ImportFile','DxfUnit')!=0 or preferences.getint('ImportFile','If90Rotate')!=0:
        raise ValueError('LaserCAD import must use millimetres with automatic rotation disabled.')
    original=source.read_bytes();doc=ezdxf.readfile(source);entities=list(doc.modelspace())
    if doc.units!=ezdxf.units.MM or not entities or any(e.dxftype() not in {'LINE','LWPOLYLINE'} for e in entities) or doc.audit().has_errors:
        raise ValueError('Measured placement accepts only valid app-generated millimetre line/polyline DXFs.')
    for e in entities:
        points=list(e.get_points()) if e.dxftype()=='LWPOLYLINE' else [e.dxf.start,e.dxf.end]
        if any(not math.isfinite(float(c)) for p in points for c in p):raise ValueError('DXF contains non-finite geometry.')
    cuts=bbox.extents([e for e in entities if e.dxf.layer=='CUT'])
    if not cuts.has_data or abs(cuts.extmax.x)>1e-6 or abs(cuts.extmax.y)>1e-6:
        raise ValueError('The canonical DXF must have local job origin (0,0) at its template-box top-right.')
    # Restore the photographed working position, then map into measured machine XY.
    restore=np.eye(3);restore[:2,2]=reference
    world=np.asarray(placement['working_to_machine'])@restore
    # LaserCAD's Right_Up PAGE readout is (W-fileX,H-fileY), not a work-origin command.
    page=np.array([[-1,0,width],[0,-1,height],[0,0,1]],dtype=float)
    relative=config.get('machine_workflow',{}).get('enabled',False) is True
    transform=page@world
    if relative:
        # Native top-right bounding anchor is supported only for aligned marker axes.
        # A rotated frame requires a separately verified custom-anchor controller contract.
        if not np.allclose(world[:2,:2],-np.eye(2),rtol=0,atol=1e-7):
            raise ValueError('Automatic Key Origin requires marker axes aligned to the machine. Rotated/custom anchors are not commissioned.')
        # Validate the REAL photographed bounds before removing the job offset.
        for entity in entities:
            points=entity.get_points() if entity.dxftype()=='LWPOLYLINE' else [entity.dxf.start,entity.dxf.end]
            for point in points:
                physical=world@np.array([point[0],point[1],1])
                if physical[0]<-1e-6 or physical[1]<-1e-6 or physical[0]>width+1e-6 or physical[1]>height+1e-6:
                    raise ValueError('The photographed layout lies outside the machine bed. No automatic job-origin move or download requested.')
        relative_world=world.copy();relative_world[:2,2]=0
        transform=page@relative_world
    if not np.allclose(transform[:2,:2].T@transform[:2,:2],np.eye(2),atol=1e-7) or np.linalg.det(transform[:2,:2])<=0:
        raise ValueError('Placement would distort or mirror the design; no import requested.')
    matrix=Matrix44([transform[0,0],transform[1,0],0,0, transform[0,1],transform[1,1],0,0,
                     0,0,1,0, transform[0,2],transform[1,2],0,1])
    for e in entities:e.transform(matrix)
    extent=bbox.extents(entities)
    if (not extent.has_data or extent.extmin.x < -1e-6 or extent.extmin.y < -1e-6 or
            extent.extmax.x>width+1e-6 or extent.extmax.y>height+1e-6):
        raise ValueError('The photographed layout lies outside the machine bed. Its position was preserved; no shift to the bed corner, scaling or download was requested.')
    digest=hashlib.sha256(original).hexdigest()
    context=hashlib.sha256(json.dumps({'snapshots':snapshots,'transform':transform.tolist()},sort_keys=True).encode()).hexdigest()
    target=source.parent/'lasercad-import'/('measured-'+context[:16])/source.name
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.'+uuid4().hex+'.tmp')
    def signature(d):
        return [(e.dxftype(),e.dxf.layer,e.dxf.get('color',256),
                 (bool(e.closed),list(e.get_points())) if e.dxftype()=='LWPOLYLINE' else (tuple(e.dxf.start),tuple(e.dxf.end))) for e in d.modelspace()]
    try:
        doc.saveas(temporary)
        if source.read_bytes()!=original or any(_hash(p)!=h for p,h in snapshots.items()):raise ValueError('Job placement changed during mapping; retry from the latest saved data.')
        if target.exists():
            if signature(ezdxf.readfile(target))!=signature(doc):raise ValueError('The existing import copy changed; export a fresh job.')
        else:temporary.replace(target)
    finally:
        if temporary.exists():temporary.unlink()
    job=world@np.array([0,0,1]);raw=transform@np.array([0,0,1])
    return {'profile':'measured_machine','positioning_mode':'key_origin_relative' if relative else 'absolute_measured','source_path':str(source),'import_path':str(target),
            'source_sha256':digest,'import_sha256':_hash(target),'placement_snapshots':snapshots,
            'bed_width_mm':width,'bed_height_mm':height,'canonical_dxf_reference_mm':[0,0],
            'working_job_origin_mm':reference.tolist(),'machine_job_origin_mm':job[:2].tolist(),
            'lasercad_page_reference_mm':[0,0] if relative else job[:2].tolist(),'import_dxf_reference_mm':raw[:2].tolist(),
            'matrix':transform.tolist(),'mirrored':False,'scaled':False,'photographed_position_preserved':True,
            'inside_bed':True,'machine_origin_changed':False,'physical_job_origin_automatically_set':False,
            'controller_placement_mode_verified':False,'live_import_verified':False}


def verify_import_snapshot(source,mapping):
    if not mapping:return
    if Path(mapping['source_path']).resolve()!=Path(source).resolve():raise ValueError('Import belongs to a different DXF.')
    for key in ('source','import'):
        if _hash(mapping[key+'_path'])!=mapping[key+'_sha256']:raise ValueError('The '+key+' DXF changed after handoff. No automatic download requested.')
    for path,digest in mapping.get('placement_snapshots',{}).items():
        if _hash(path)!=digest:raise ValueError('Marker/machine/job placement changed after handoff. No automatic download or Run Box requested.')
