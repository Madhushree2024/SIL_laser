"""One-time requested usable-travel update; measured geometry is never changed."""
from pathlib import Path
import json,logging,os
from uuid import uuid4
import yaml


def _atomic(path,data):
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def upgrade_usable_travel(root,bundle):
    root=Path(root);bundle=Path(bundle)
    state=root/'laser_control_state/usable-travel-915x630-v1.json'
    if state.is_file():return {'status':'already_applied'}
    path=root/'app/config/config.yaml'
    original=path.read_bytes();config=yaml.safe_load(original.decode('utf-8-sig'))
    if not isinstance(config,dict):raise ValueError('Application settings must be a mapping.')
    previous=dict(config.get('workspace',{}))
    config.setdefault('workspace',{}).update(width_mm=915,height_mm=630,physical_width_mm=960,physical_height_mm=650)
    bundled=bundle.parent/'LaserCAD/AWCPrograms/LaserCAD.exe'
    # Use the updated bundled software page, not an older extracted LaserCAD path.
    if bundled.is_file():config.setdefault('lasercad',{})['executable']=str(bundled.resolve())
    state.parent.mkdir(parents=True,exist_ok=True)
    tag=uuid4().hex
    backup=state.parent/('config-before-usable-travel-'+tag+'.yaml')
    backup.write_bytes(original)
    survey=root/'data/calibration/machine_placement.json'
    survey_result='not_present'
    survey_backup=None
    if survey.is_file():
        # Revalidate the SAME surveyed positions and SAME rigid transform against
        # the smaller envelope. Never shift, scale, fabricate or overwrite readings.
        try:
            import numpy as np
            from .machine_placement import make_profile
            from .aruco_bed import BedCalibrationRequest
            from .common_origin import working_layout
            from .workspace import read_workspace_layout
            old=survey.read_bytes();saved=json.loads(old)
            layout=working_layout(read_workspace_layout(root/'data/calibration/aruco_viewer_bed_layout.json'),config)
            measured=BedCalibrationRequest(markers=saved['machine_markers'],dictionary=saved['dictionary'])
            checked=make_profile(layout,measured,config)
            if (saved.get('coordinate_frame')!=checked['coordinate_frame'] or
                    not np.allclose(saved['working_to_machine'],checked['working_to_machine'],rtol=0,atol=1e-7) or
                    saved.get('working_markers')!=checked['working_markers']):
                raise ValueError('Saved survey no longer matches the working marker layout.')
            saved['bed_mm']=[915.0,630.0]
            survey_backup=survey.with_name('machine_placement_before_usable_travel_'+tag+'.json')
            survey_backup.write_bytes(old)
            _atomic(survey,json.dumps(saved,indent=2).encode('utf-8'))
            survey_result='same_measured_positions_revalidated'
        except (ValueError,KeyError,TypeError,OSError) as exc:
            survey_result='preserved_requires_review: '+str(exc)
            logging.warning('Usable travel changed; existing machine survey requires review: %s',exc)
    _atomic(path,yaml.safe_dump(config,sort_keys=False).encode('utf-8'))
    report={'status':'applied','previous_workspace':previous,'usable_travel_mm':[915,630],
            'physical_bed_mm':[960,650],'config_backup':str(backup),
            'machine_survey':survey_result,'survey_backup':str(survey_backup) if survey_backup else None,
            'marker_positions_changed':False,'camera_calibration_changed':False,
            'selected_updated_bundled_lasercad':bundled.is_file()}
    _atomic(state,json.dumps(report,indent=2).encode('utf-8'))
    logging.info('Usable travel update applied: %s',report)
    return report
