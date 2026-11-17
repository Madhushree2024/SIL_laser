"""ID 12 measurement frame and a separate template-box DXF origin."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import shutil
from uuid import uuid4
import numpy as np
import yaml
from .aruco_bed import BedCalibrationRequest
from .scale_calibration import ScaleCalibrationProfile

FRAME = 'id12_negative_xy'


def enabled(config):
    return config.get('workspace',{}).get('coordinate_frame') == FRAME


def canonical_layout(request: BedCalibrationRequest):
    points={m.marker_id:np.array([m.x_mm,m.y_mm],dtype=float) for m in request.markers}
    if not {11,12,13}.issubset(points):
        raise ValueError('The shared origin requires ID 12, with ID 11 along -X and ID 13 along -Y.')
    origin=points[12]
    arm_x=points[11]-origin;arm_y=points[13]-origin
    lengths=np.linalg.norm(arm_x),np.linalg.norm(arm_y)
    if min(lengths)<1e-6:raise ValueError('Origin and axis marker centres must be distinct.')
    # Define perpendicular coordinate axes, not a rectangular marker layout.
    # -Y follows ID 12 -> ID 13; -X faces the side containing ID 11.
    # A skewed top edge retains its measured Y component. No shear or scale.
    y=-arm_y/lengths[1]
    x=np.array([-y[1],y[0]],dtype=float)
    projection=float(x@arm_x)
    if abs(projection)<1e-6:
        raise ValueError('ID 11 must lie to one side of the ID 12 / ID 13 axis.')
    if projection>0:x=-x
    transform=np.array([[x[0],x[1],-float(x@origin)],
                        [y[0],y[1],-float(y@origin)],[0,0,1]],dtype=float)
    markers=[]
    for marker in request.markers:
        point=transform@np.array([marker.x_mm,marker.y_mm,1])
        point[np.abs(point)<1e-8]=0
        markers.append({'marker_id':marker.marker_id,'x_mm':float(point[0]),'y_mm':float(point[1])})
    return BedCalibrationRequest(markers=markers,dictionary=request.dictionary),transform


def working_layout(request,config):
    return canonical_layout(request)[0] if enabled(config) else request


def profile_in_frame(profile: ScaleCalibrationProfile,config):
    if not enabled(config):return profile
    if not profile.homography or not profile.bed_markers:
        raise ValueError('Save four-marker working-area calibration for the shared ID 12 origin. A single-marker scale cannot establish this origin.')
    request=BedCalibrationRequest(markers=profile.bed_markers,dictionary=profile.dictionary or 'DICT_4X4_50')
    layout,transform=canonical_layout(request)
    matrix=transform@np.asarray(profile.homography,dtype=float)
    if abs(matrix[2,2])>1e-12:matrix=matrix/matrix[2,2]
    if not np.isfinite(matrix).all():raise ValueError('The saved perspective matrix cannot be converted to the ID 12 frame.')
    points=np.array([[m.x_mm,m.y_mm] for m in layout.markers])
    return replace(profile,homography=matrix.tolist(),bed_markers=[m.model_dump() for m in layout.markers],
                   working_width_mm=float(np.ptp(points[:,0])),working_height_mm=float(np.ptp(points[:,1])),
                   source='aruco_workspace',coordinate_frame=FRAME,origin_marker_id=12)


def identity_alignment():
    from app.laser.coordinate_alignment import CoordinateAlignment
    return CoordinateAlignment(enabled=True,reference='design_origin',design_x_mm=0,design_y_mm=0,
                               laser_x_mm=0,laser_y_mm=0,rotation_deg=0,flip_x=False,flip_y=False)


def export_alignment():
    from app.laser.coordinate_alignment import CoordinateAlignment
    return CoordinateAlignment(enabled=True,reference='templates_top_right')


def require_geometry(scale,config):
    if enabled(config) and (not scale or scale.get('coordinate_frame')!=FRAME or scale.get('origin_marker_id')!=12):
        raise ValueError('Capture, detect and nest again using the shared ID 12 origin before exporting. An older coordinate frame cannot be reused.')


def migrate_workspace(root: Path):
    """Called before the desktop backend starts, after its single-instance lock.

    Change the plane-coordinate basis of a saved homography, not camera intrinsics
    or the photograph. Historical captures, nestings, DXFs and jobs remain records.
    """
    root=Path(root)
    config_path=root/'app/config/config.yaml'
    layout_path=root/'data/calibration/aruco_viewer_bed_layout.json'
    if not config_path.is_file() or not layout_path.is_file():return {'migrated':False,'reason':'No surveyed four-marker layout yet.'}
    config=yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
    original_layout=BedCalibrationRequest.model_validate_json(layout_path.read_text(encoding='utf-8'))
    layout,transform=canonical_layout(original_layout)
    config=deepcopy(config)
    config.setdefault('workspace',{}).update(coordinate_frame=FRAME,origin='id12_centre')
    updates={config_path:('yaml',config),layout_path:('json',layout.model_dump()),
             root/'data/calibration/laser_alignment.json':('json',export_alignment().model_dump())}
    profile_path=root/'data/calibration/scale_profile.json'
    needs_calibration=False
    if profile_path.is_file():
        profile=ScaleCalibrationProfile(**json.loads(profile_path.read_text(encoding='utf-8')))
        try:rebased=profile_in_frame(profile,config)
        except ValueError:needs_calibration=True
        else:updates[profile_path]=('json',asdict(rebased))
    changed={}
    for path,(kind,value) in updates.items():
        if path.exists():
            current=yaml.safe_load(path.read_text(encoding='utf-8')) if kind=='yaml' else json.loads(path.read_text(encoding='utf-8'))
            if current==value:continue
        changed[path]=(kind,value)
    if not changed:return {'migrated':False,'coordinate_frame':FRAME,'needs_calibration':needs_calibration}
    backup=root/'data/calibration/coordinate_frame_backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    for path in changed:
        if path.is_file():
            saved=backup/path.relative_to(root);saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,saved)
    for path,(kind,value) in changed.items():
        text=yaml.safe_dump(value,sort_keys=False) if kind=='yaml' else json.dumps(value,indent=2)
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        temporary.write_text(text,encoding='utf-8');temporary.replace(path)
    report={'migrated':True,'coordinate_frame':FRAME,'origin_marker_id':12,'negative_x_marker_id':11,
            'negative_y_marker_id':13,'layout_transform':transform.tolist(),'needs_calibration':needs_calibration,
            'backup':str(backup),'lens_calibration_changed':False}
    (backup/'migration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
