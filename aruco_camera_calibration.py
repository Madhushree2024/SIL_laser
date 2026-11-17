"""Interactive checkerboard calibration used before ArUco measurement."""
from dataclasses import asdict
from pathlib import Path
import json
import cv2
import numpy as np
from app.calibration.camera_calibration import CameraCalibrationProfile


def calibrate_views(corner_views, image_size, checkerboard=(9, 6), square_size_mm=30.):
    if min(checkerboard) < 2 or not np.isfinite(square_size_mm) or square_size_mm <= 0:
        raise ValueError('Use at least two inner corners per axis and a positive finite square size')
    if len(corner_views) < 12:
        raise ValueError('Capture at least 12 varied checkerboard views')
    obj = np.zeros((checkerboard[0] * checkerboard[1], 3), np.float32)
    obj[:, :2] = np.mgrid[0:checkerboard[0], 0:checkerboard[1]].T.reshape(-1, 2) * square_size_mm
    rms, matrix, distortion, rotations, translations = cv2.calibrateCamera(
        [obj.copy() for _ in corner_views], corner_views, image_size, None, None)
    errors = []
    for corners, rotation, translation in zip(corner_views, rotations, translations):
        projected, _ = cv2.projectPoints(obj, rotation, translation, matrix, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((corners.reshape(-1, 2) - projected.reshape(-1, 2)) ** 2, axis=1)))))
    if not np.isfinite(matrix).all() or not np.isfinite(distortion).all() or not np.isfinite(rms):
        raise ValueError('Calibration produced invalid values; collect clearer and more varied views')
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or rms > 1.0:
        raise ValueError(f'Calibration RMS {rms:.3f}px failed the 1px quality limit. Remove blurry views or collect better views.')
    validate_lens_mapping(matrix, distortion, image_size)
    profile = CameraCalibrationProfile(matrix.tolist(), distortion.ravel().tolist(), image_size[0], image_size[1], float(rms))
    return profile, errors


def validate_lens_mapping(matrix, distortion, image_size):
    # A small RMS on central corners can conceal an invalid extrapolation.
    xs = (np.linspace(0, image_size[0] - 1, 25) - matrix[0, 2]) / matrix[0, 0]
    ys = (np.linspace(0, image_size[1] - 1, 19) - matrix[1, 2]) / matrix[1, 1]
    x, y = np.meshgrid(xs, ys)
    points = np.column_stack((x.ravel(), y.ravel(), np.ones(x.size)))
    identity = np.eye(3)
    def project(p):
        return cv2.projectPoints(p, np.zeros(3), np.zeros(3), identity, distortion)[0].reshape(-1, 2)
    base = project(points)
    step = 1e-5
    dx = points.copy(); dx[:, 0] += step
    dy = points.copy(); dy[:, 1] += step
    jx, jy = (project(dx) - base) / step, (project(dy) - base) / step
    determinant = jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]
    if (not np.isfinite(base).all() or not np.isfinite(determinant).all()
            or np.min(determinant) <= 0 or np.min(jx[:, 0]) <= 0 or np.min(jy[:, 1]) <= 0):
        raise ValueError('Lens correction folds at image edges. Capture stronger tilts and views across the full image; calibration rejected.')


def run_calibration(capture, profile_path, checkerboard=(9, 6), square_size_mm=30., camera_index=1):
    if min(checkerboard) < 2 or not np.isfinite(square_size_mm) or square_size_mm <= 0:
        raise ValueError('Invalid checkerboard or square size')
    views, poses = [], []
    image_size = None
    message = 'Move the board across the frame and tilt it; SPACE captures, C calibrates, U undoes, Q quits'
    window = 'Camera calibration - capture at least 12 varied views'
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError('Camera capture failed during calibration')
            size = (frame.shape[1], frame.shape[0])
            if image_size is not None and size != image_size:
                raise ValueError('Camera resolution changed during calibration')
            image_size = size
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCornersSB(gray, checkerboard, cv2.CALIB_CB_NORMALIZE_IMAGE)
            preview = frame.copy()
            if found:
                cv2.drawChessboardCorners(preview, checkerboard, corners, True)
            lines = [f'{checkerboard[0]} x {checkerboard[1]} INNER corners | square {square_size_mm:g}mm | {size[0]} x {size[1]}px',
                     f'Captured {len(views)}/12 minimum | Board: ' + ('FOUND' if found else 'not found'),
                     'SPACE capture | C calibrate and measure | U undo last | Q cancel', message]
            panel = np.full((135, max(1100, size[0]), 3), 25, np.uint8)
            for i, line in enumerate(lines):
                cv2.putText(panel, line, (10, 27 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, .48, (160, 255, 180), 1, cv2.LINE_AA)
            canvas = np.zeros((size[1] + 135, panel.shape[1], 3), np.uint8)
            canvas[:size[1], :size[0]] = preview
            canvas[size[1]:] = panel
            scale = min(1., 1500 / canvas.shape[1], 900 / canvas.shape[0])
            cv2.imshow(window, cv2.resize(canvas, None, fx=scale, fy=scale))
            key = cv2.waitKey(30) & 0xff
            if key in (ord('q'), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                return None
            if key == ord('u') and views:
                views.pop()
                poses.pop()
                message = 'Last view removed'
            if key == ord(' '):
                if not found:
                    message = 'Full checkerboard required; improve lighting and show all corners'
                    continue
                pose = corners.reshape(-1, 2) / np.array(size)
                if any(float(np.sqrt(np.mean((pose - previous) ** 2))) < .025 for previous in poses):
                    message = 'Too similar to a saved view; move, tilt or change board distance'
                    continue
                views.append(corners.copy())
                poses.append(pose.copy())
                message = f'Saved view {len(views)}. Cover image corners and centre, with several tilts.'
            if key == ord('c'):
                try:
                    profile, errors = calibrate_views(views, image_size, checkerboard, square_size_mm)
                except ValueError as exc:
                    message = str(exc)
                    continue
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                data = asdict(profile)
                data.update(checkerboard_inner_corners=list(checkerboard), square_size_mm=square_size_mm,
                            per_view_rms_px=errors, captured_views=len(views), camera_index=camera_index,
                            note='Use the same camera, focus, zoom and resolution. RMS checks fit, not absolute measurement accuracy.')
                # Avoid partially written profiles and retain an older calibration on failure.
                temporary = profile_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
                temporary.replace(profile_path)
                print(f'Calibration saved: {profile_path.resolve()} | RMS {profile.rms_error:.3f}px | {len(views)} views')
                return profile
    finally:
        cv2.destroyWindow(window)


def read_lens_profile(path):
    path = Path(path)
    if path.suffix.lower() == '.npz':
        with np.load(path, allow_pickle=False) as saved:
            data = {name: saved[name].tolist() for name in saved.files}
        if 'distortion_coefficients' not in data:
            data['distortion_coefficients'] = data['distortion']
    else:
        data = json.loads(path.read_text(encoding='utf-8'))
    return data


def calibrated_corrector(path, camera_index=None):
    data = read_lens_profile(path)
    matrix = np.array(data['camera_matrix'], np.float64)
    distortion = np.array(data['distortion_coefficients'], np.float64)
    expected = (data['image_width'], data['image_height'])
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.isfinite(distortion).all():
        raise ValueError('Invalid lens profile')
    if min(expected) <= 0 or min(matrix[0, 0], matrix[1, 1]) <= 0:
        raise ValueError('Invalid calibration resolution or focal length')
    validate_lens_mapping(matrix, distortion, expected)
    output_matrix = np.array(data.get('new_camera_matrix', matrix), np.float64)
    if output_matrix.shape != (3, 3) or not np.isfinite(output_matrix).all():
        raise ValueError('Invalid corrected camera matrix')
    if camera_index is not None and 'camera_index' in data and camera_index != data['camera_index']:
        raise ValueError('Lens profile belongs to another camera index; use --recalibrate')
    maps = None
    def correct(image):
        nonlocal maps
        size = (image.shape[1], image.shape[0])
        if size != expected:
            raise ValueError(f'Calibration resolution is {expected}, received {size}; use matching --width/--height or --recalibrate')
        if maps is None:
            maps = cv2.initUndistortRectifyMap(matrix, distortion, None, output_matrix, size, cv2.CV_32FC1)
        return cv2.remap(image, maps[0], maps[1], cv2.INTER_LINEAR)
    return correct


def write_checkerboard(path):
    # 9x6 INNER corners require 10x7 squares. A3 landscape, millimetre units (300x210mm pattern plus margins).
    rectangles = []
    for row in range(7):
        for col in range(10):
            if (row + col) % 2 == 0:
                rectangles.append(f'<rect x="{60 + col * 30}" y="{43.5 + row * 30}" width="30" height="30" fill="black"/>')
    content = '<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 420 297">'
    content += '<rect width="420" height="297" fill="white"/>' + ''.join(rectangles)
    content += '<text x="60" y="25" font-size="3">9 x 6 inner corners | 30 mm squares | Print A3 landscape, actual size / 100%</text>'
    content += '<text x="60" y="280" font-size="3">Verify one square = 30 mm with a ruler. Mount flat; disable fit-to-page.</text></svg>'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path
