"""Persist surveyed marker coordinates independently of a camera photo."""
from dataclasses import asdict,is_dataclass
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
from .aruco_bed import BedCalibrationRequest


def profile_uses_layout(profile,layout: BedCalibrationRequest) -> bool:
    if profile is None:return False
    data=asdict(profile) if is_dataclass(profile) else profile
    if data.get('dictionary')!=layout.dictionary:return False
    try:
        markers={m['marker_id']:[m['x_mm'],m['y_mm']] for m in data.get('bed_markers') or []}
        requested={m.marker_id:[m.x_mm,m.y_mm] for m in layout.markers}
        return len(markers)==4 and markers.keys()==requested.keys() and all(
            np.allclose(markers[key],requested[key],rtol=0,atol=1e-7) for key in markers)
    except (KeyError,TypeError,ValueError):return False


def save_workspace_layout(path: Path,layout: BedCalibrationRequest) -> bool:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        old=BedCalibrationRequest.model_validate_json(path.read_text(encoding='utf-8'))
        if profile_uses_layout({'dictionary':old.dictionary,'bed_markers':[m.model_dump() for m in old.markers]},layout):return False
        backup=path.with_name('workspace_layout_before_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
        backup.write_bytes(path.read_bytes())
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(layout.model_dump(),indent=2),encoding='utf-8')
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
    return True
