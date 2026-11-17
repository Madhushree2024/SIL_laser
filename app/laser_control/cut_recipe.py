"""Configurable primary cutting-layer recipe; no machine commands."""
import math

DEFAULT_SPEED_MM_S=90.0
DEFAULT_POWER_PERCENT=10.0


def cut_recipe(options):
    if not isinstance(options,dict):raise ValueError('LaserCAD cutting preferences must be an object.')
    raw_speed=options.get('speed_mm_s',DEFAULT_SPEED_MM_S)
    raw_power=options.get('power_percent',DEFAULT_POWER_PERCENT)
    try:
        if isinstance(raw_speed,bool) or isinstance(raw_power,bool):raise ValueError
        speed,power=float(raw_speed),float(raw_power)
    except (ValueError,TypeError):
        raise ValueError('Enter numeric LaserCAD speed and power values.') from None
    if not math.isfinite(speed) or speed<=0:
        raise ValueError('LaserCAD cutting speed must be a positive finite value in mm/s.')
    if not math.isfinite(power) or not 0<=power<=100:
        raise ValueError('LaserCAD maximum power must be between 0 and 100 percent.')
    if options.get('work_mode','Cut')!='Cut':
        raise ValueError('Automatic layer setup supports Cut mode only.')
    return speed,power


def upgrade_cut_recipe(root):
    """Apply the requested defaults once; preserve all subsequent operator edits."""
    from pathlib import Path
    import json,os,yaml
    from uuid import uuid4
    root=Path(root)
    state=root/'laser_control_state/cut-recipe-90-10-v1.json'
    if state.is_file():return {'status':'already_applied'}
    path=root/'app/config/config.yaml';original=path.read_bytes()
    config=yaml.safe_load(original.decode('utf-8-sig'))
    if not isinstance(config,dict):raise ValueError('Application settings must be an object.')
    options=config.setdefault('lasercad',{}).setdefault('prepare_and_download',{})
    options.update(speed_mm_s=90,power_percent=10,work_mode='Cut')
    cut_recipe(options)
    state.parent.mkdir(parents=True,exist_ok=True)
    backup=state.parent/('config-before-cut-recipe-'+uuid4().hex+'.yaml')
    backup.write_bytes(original)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8')
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    report={'status':'applied','speed_mm_s':90,'power_percent':10,'config_backup':str(backup),
            'calibration_changed':False,'later_manual_preferences_preserved':True}
    temporary=state.with_name(state.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        os.replace(temporary,state)
    finally:temporary.unlink(missing_ok=True)
    return report
