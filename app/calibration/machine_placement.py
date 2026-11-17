"""Surveyed placement between the working marker frame and machine coordinates."""
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import json
import numpy as np
from .aruco_bed import BedCalibrationRequest
from .workspace import read_workspace_layout
from .common_origin import working_layout,canonical_layout
from .workspace_layout_store import profile_uses_layout,save_workspace_layout

FIT_TOLERANCE_MM = 1.0


def profile_path(root):
    return Path(root)/'data/calibration/machine_placement.json'


def make_profile(layout, measured, config):
    width=float(config['workspace']['width_mm']);height=float(config['workspace']['height_mm'])
    if config.get('workspace',{}).get('coordinate_frame')!='id12_negative_xy':
        raise ValueError('Save the shared ID 12 working marker layout first.')
    working={m.marker_id:[m.x_mm,m.y_mm] for m in layout.markers}
    machine={m.marker_id:[m.x_mm,m.y_mm] for m in measured.markers}
    if measured.dictionary!=layout.dictionary or working.keys()!=machine.keys():
        raise ValueError('Machine positions must use the same four saved marker IDs and dictionary.')
    ids=sorted(working);x=np.array([working[i] for i in ids],dtype=float);y=np.array([machine[i] for i in ids],dtype=float)
    if not np.isfinite([width,height]).all() or min(width,height)<=0:
        raise ValueError('Save a valid laser bed width and height first.')
    if np.any(y < -1e-6) or np.any(y > np.array([width,height])+1e-6):
        raise ValueError('Every surveyed marker centre must lie inside the machine bed (X left, Y down from its top-right).')
    if np.linalg.matrix_rank(x-x.mean(axis=0))<2:
        raise ValueError('Working marker centres do not define a plane.')
    u,_,vt=np.linalg.svd((x-x.mean(axis=0)).T@(y-y.mean(axis=0)))
    rotation=vt.T@u.T
    if np.linalg.det(rotation)<0:
        raise ValueError('Marker IDs or axis directions imply a mirrored survey. Check the actual machine coordinates; no reflection was applied.')
    translation=y.mean(axis=0)-rotation@x.mean(axis=0)
    residuals=np.linalg.norm(x@rotation.T+translation-y,axis=1)
    maximum=float(residuals.max())
    if maximum>FIT_TOLERANCE_MM:
        raise ValueError(f'Machine survey disagrees with the saved marker distances by up to {maximum:.3f} mm. Remeasure or correct the working layout; no scaling or stretching was applied.')
    matrix=np.eye(3);matrix[:2,:2]=rotation;matrix[:2,2]=translation
    return {'version':1,'coordinate_frame':'id12_negative_xy','dictionary':layout.dictionary,
            'working_markers':[m.model_dump() for m in layout.markers],
            'machine_markers':[m.model_dump() for m in measured.markers],
            'working_to_machine':matrix.tolist(),'bed_mm':[width,height],
            'max_fit_error_mm':maximum,'fit_tolerance_mm':FIT_TOLERANCE_MM,
            'saved_at':datetime.now(timezone.utc).isoformat()}


def save_machine_placement(root, measured, config):
    layout=working_layout(read_workspace_layout(Path(root)/'data/calibration/aruco_viewer_bed_layout.json'),config)
    result=make_profile(layout,measured,config)
    path=profile_path(root);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():path.with_name('machine_placement_before_'+uuid4().hex+'.json').write_bytes(path.read_bytes())
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:temporary.write_text(json.dumps(result,indent=2),encoding='utf-8');temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
    return result


def save_layout_from_machine_positions(root,measured,config):
    """Explicitly replace both surveys with the same measured physical geometry.

    Validate before writing. An older perspective profile no longer matches this
    layout and is excluded by calibration_status until a new four-marker capture.
    """
    layout,_=canonical_layout(measured)
    make_profile(layout,measured,config)
    path=Path(root)/'data/calibration/aruco_viewer_bed_layout.json'
    machine_path=profile_path(root)
    originals={p:p.read_bytes() if p.exists() else None for p in (path,machine_path)}
    try:
        changed=save_workspace_layout(path,layout)
        profile=save_machine_placement(root,measured,config)
    except Exception:
        for p,content in originals.items():
            if content is None:
                if p.exists():p.unlink()
            else:
                temporary=p.with_name(p.name+'.rollback.'+uuid4().hex)
                temporary.write_bytes(content);temporary.replace(p)
        raise
    return {'changed':changed,'layout':layout.model_dump(),'machine_placement':profile}


def load_machine_placement(root, config):
    path=profile_path(root)
    if not path.is_file():
        raise ValueError('Machine placement is not measured. In Calibration, save the actual machine X/Y of all four marker centres. The design was not moved to the machine origin.')
    saved=json.loads(path.read_text(encoding='utf-8'))
    layout=working_layout(read_workspace_layout(Path(root)/'data/calibration/aruco_viewer_bed_layout.json'),config)
    if (saved.get('version')!=1 or saved.get('coordinate_frame')!='id12_negative_xy' or
            not profile_uses_layout({'dictionary':saved.get('dictionary'),'bed_markers':saved.get('working_markers')},layout)):
        raise ValueError('Working marker positions changed after machine placement was saved. Survey and save machine placement again.')
    if not np.allclose(saved.get('bed_mm',[]),[config['workspace']['width_mm'],config['workspace']['height_mm']],rtol=0,atol=1e-7):
        raise ValueError('Bed dimensions changed after machine placement was saved. Verify and save the machine survey again.')
    measured=BedCalibrationRequest(markers=saved['machine_markers'],dictionary=saved['dictionary'])
    verified=make_profile(layout,measured,config)
    if not np.allclose(saved.get('working_to_machine'),verified['working_to_machine'],rtol=0,atol=1e-7):
        raise ValueError('The saved placement transform is inconsistent. Save the survey again.')
    return saved


def machine_placement_status(root, config):
    try:
        p=load_machine_placement(root,config)
        return {'ready':True,'message':'Measured machine placement active. Physical controller/job-origin mode must still be verified before cutting.',
                'machine_markers':p['machine_markers'],'max_fit_error_mm':p['max_fit_error_mm'],
                'bed_mm':p['bed_mm'],'saved_at':p['saved_at']}
    except (ValueError,OSError,KeyError,TypeError) as exc:
        return {'ready':False,'message':str(exc),'machine_markers':[]}
