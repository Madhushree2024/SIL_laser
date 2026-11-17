"""Run the checkout with separate, persistent data; never launch on git pull."""
from pathlib import Path
import argparse, os, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ['app/config/config.yaml','camera_calibration.npz','data/templates/templates.json',
         'data/calibration/aruco_viewer_bed_layout.json','data/calibration/camera_distortion_data.json',
         'data/calibration/laser_alignment.json','data/calibration/machine_placement.json',
         'data/calibration/scale_profile.json','data/calibration/dxf_origin_id12.json']

def initialize(workspace):
    workspace = Path(workspace).expanduser().resolve()
    if workspace.is_relative_to(ROOT):
        raise ValueError('Keep the workspace outside the Git checkout so pulls cannot replace saved data.')
    for name in SEEDS:
        src, dst = ROOT/name, workspace/name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            try:
                with dst.open('xb') as target, src.open('rb') as original:
                    shutil.copyfileobj(original, target)
            except FileExistsError:
                pass
    return workspace

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    local = Path(os.environ.get('LOCALAPPDATA', Path.home()/'.local/share'))
    parser.add_argument('--workspace', type=Path, default=local/'SheathTemplateCuttingAutomation-Source/Workspace')
    parser.add_argument('--initialize-only', action='store_true')
    args = parser.parse_args()
    workspace = initialize(args.workspace)
    if args.initialize_only:
        print(workspace); return 0
    return subprocess.call([sys.executable,str(ROOT/'desktop.py'),'--workspace',str(workspace)],cwd=ROOT)

if __name__ == '__main__':
    raise SystemExit(main())
