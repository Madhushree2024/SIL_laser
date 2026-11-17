from dataclasses import asdict
from pathlib import Path
import json
import time
import cv2
import numpy as np
from camera_aruco_coverage import open_camera
from aruco_camera_calibration import calibrate_views

capture = None
folder = Path('data/aruco_visual_check/calibration_capture') / time.strftime('session_%Y%m%d_%H%M%S')
folder.mkdir(parents=True, exist_ok=True)
views, poses = [], []
last_saved = -100.
try:
    capture = open_camera(1, 1280, 720)
    start = time.monotonic()
    last_notice = start
    size = None
    print('AUTO CAPTURE STARTED: 8x5 inner corners, 30mm squares; target 15 distinct views', flush=True)
    while time.monotonic() - start < 180:
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError('Camera capture failed')
        current_size = (frame.shape[1], frame.shape[0])
        if size is not None and current_size != size:
            raise ValueError('Camera resolution changed')
        size = current_size
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCornersSB(gray, (8, 5), cv2.CALIB_CB_NORMALIZE_IMAGE)
        now = time.monotonic()
        if found and now - last_saved >= 2:
            pose = corners.reshape(-1, 2) / np.array(size)
            distinct = all(min(float(np.sqrt(np.mean((pose - previous) ** 2))), float(np.sqrt(np.mean((pose[::-1] - previous) ** 2)))) >= .018 for previous in poses)
            if distinct:
                views.append(corners.astype(np.float32))
                poses.append(pose)
                last_saved = now
                cv2.imwrite(str(folder / f'view_{len(views):02d}.png'), frame)
                np.savez(folder / 'detected_corners.npz', corners=np.array(views), image_size=np.array(size))
                print(f'Captured {len(views)}/15 | elapsed {now - start:.0f}s', flush=True)
                if len(views) >= 15:
                    break
        if now - last_notice >= 15:
            print(f'Waiting for distinct board views: {len(views)}/15; checkerboard currently ' + ('found' if found else 'not found'), flush=True)
            last_notice = now
    if len(views) < 12:
        raise ValueError(f'Only {len(views)} distinct views captured. Need at least 12; calibration not saved.')
    profile, errors = calibrate_views(views, size, (8, 5), 30.)
    data = asdict(profile)
    data.update(checkerboard_inner_corners=[8, 5], square_size_mm=30., per_view_rms_px=errors, captured_views=len(views), camera_index=1,
                note='Actual board: 9x6 squares, 8x5 inner corners. Keep camera focus, zoom and resolution unchanged. RMS measures fitting error, not guaranteed accuracy.')
    path = Path('data/aruco_visual_check/camera_1_profile.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)
    print('CALIBRATION COMPLETE ' + json.dumps({'profile': str(path.resolve()), 'rms_px': profile.rms_error, 'views': len(views), 'resolution': list(size), 'per_view_rms_px': errors}), flush=True)
finally:
    if capture is not None:
        capture.release()
