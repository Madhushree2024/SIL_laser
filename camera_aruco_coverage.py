"""Standalone camera coverage diagnostic; does not change app calibration.

Examples:
  python camera_aruco_coverage.py --camera 1 --marker-id 10 --marker-size-mm 95
  python camera_aruco_coverage.py --image marker_photo.jpg --marker-size-mm 95
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


DICTIONARIES = ("DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_50", "DICT_6X6_50")


def analyze_frame(frame, marker_size_mm=95.0, marker_id=10, dictionary="DICT_4X4_50"):
    if not math.isfinite(marker_size_mm) or marker_size_mm <= 0:
        raise ValueError("Marker size must be a positive finite number")
    if dictionary not in DICTIONARIES:
        raise ValueError("Unsupported dictionary")
    aruco_dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = 101
    params.minMarkerPerimeterRate = 0.01
    detector = cv2.aruco.ArucoDetector(aruco_dictionary, params)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    corners, ids, _ = detector.detectMarkers(gray)
    detected_ids = ids.flatten().tolist() if ids is not None else []
    if detected_ids.count(marker_id) != 1:
        raise ValueError(f"Need exactly one full marker ID {marker_id}; detected IDs: {detected_ids}")
    marker = corners[detected_ids.index(marker_id)].reshape(4, 2)
    destination = np.array([[0, 0], [marker_size_mm, 0], [marker_size_mm, marker_size_mm], [0, marker_size_mm]], np.float32)
    homography = cv2.getPerspectiveTransform(marker.astype(np.float32), destination)
    height, width = frame.shape[:2]
    image_corners = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float64)
    denominators = np.column_stack([image_corners, np.ones(4)]) @ homography[2]
    if not np.isfinite(homography).all() or np.min(denominators) <= 0 <= np.max(denominators) or np.min(np.abs(denominators)) < 1e-9:
        raise ValueError("Cannot estimate the full image plane: perspective horizon crosses the frame. Use a more overhead view.")
    footprint = cv2.perspectiveTransform(image_corners.reshape(-1, 1, 2), homography).reshape(4, 2)
    sides = [float(np.linalg.norm(marker[(i + 1) % 4] - marker[i])) for i in range(4)]
    mean_side = float(np.mean(sides))
    edges = [float(np.linalg.norm(footprint[(i + 1) % 4] - footprint[i])) for i in range(4)]
    signed_double_area = np.dot(footprint[:, 0], np.roll(footprint[:, 1], -1)) - np.dot(footprint[:, 1], np.roll(footprint[:, 0], -1))
    area_mm2 = float(abs(signed_double_area) / 2)
    return {
        "resolution_px": [width, height], "marker_id": marker_id, "detected_ids": detected_ids,
        "dictionary": dictionary, "marker_size_mm": marker_size_mm,
        "marker_mean_side_px": mean_side, "local_mm_per_pixel": marker_size_mm / mean_side,
        "footprint_corners_mm": footprint.tolist(),
        "coverage_bounds_mm": {"width": float(np.ptp(footprint[:, 0])), "height": float(np.ptp(footprint[:, 1]))},
        "frame_edges_mm": dict(zip(["top", "right", "bottom", "left"], edges)),
        "visible_plane_area_mm2": area_mm2, "homography": homography.tolist(),
        "marker_corners_px": marker.tolist(),
        "note": "Estimate on the marker plane, relative to its oriented top-left corner. Bounds enclose the projected quadrilateral. Local mm/pixel is measured near the marker, not uniform across a tilted image. This is not laser-origin calibration.",
    }


def annotate_frame(frame, report=None, error=None):
    output = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    height, width = output.shape[:2]
    if report:
        cv2.polylines(output, [np.round(report["marker_corners_px"]).astype(np.int32)], True, (0, 255, 0), 2)
        bounds = report["coverage_bounds_mm"]
        edges = report["frame_edges_mm"]
        lines = [
            f"Camera: {width} x {height} px | ArUco ID {report['marker_id']} | {report['marker_size_mm']:g} mm",
            f"Plane bounds: {bounds['width']:.1f} x {bounds['height']:.1f} mm (estimate)",
            f"Frame top/bottom: {edges['top']:.1f} / {edges['bottom']:.1f} mm",
            f"Frame left/right: {edges['left']:.1f} / {edges['right']:.1f} mm",
            f"Local scale: {report['local_mm_per_pixel']:.4f} mm/px | Marker: {report['marker_mean_side_px']:.1f} px",
        ]
    else:
        lines = [f"Camera: {width} x {height} px", error or "Marker not found - measurement unavailable"]
    font_scale = max(0.35, min(0.7, width / 1100))
    line_height = max(18, int(font_scale * 36))
    cv2.rectangle(output, (0, 0), (width - 1, min(height - 1, line_height * (len(lines) + 1))), (20, 20, 20), -1)
    for i, line in enumerate(lines):
        cv2.putText(output, line, (8, line_height * (i + 1)), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (100, 255, 160) if report else (100, 180, 255), 1, cv2.LINE_AA)
    cv2.putText(output, "S: save measurement   Q / Esc: quit", (8, height - 12), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)
    return output


def save_report(output_dir, annotated, report):
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / f"coverage_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    image_path, json_path = stem.with_suffix(".png"), stem.with_suffix(".json")
    if not cv2.imwrite(str(image_path), annotated):
        raise ValueError(f"Unable to save {image_path}")
    json_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Saved image: {image_path.resolve()}\nSaved measurements: {json_path.resolve()}")


def print_report(report):
    bounds = report["coverage_bounds_mm"]
    print(f"ID {report['marker_id']} | Frame {report['resolution_px'][0]} x {report['resolution_px'][1]} px | Estimated plane bounds {bounds['width']:.1f} x {bounds['height']:.1f} mm | Local scale {report['local_mm_per_pixel']:.4f} mm/px")


def lens_corrector(path):
    if path is None:
        return lambda image: image
    profile = json.loads(path.read_text(encoding="utf-8"))
    def correct(image):
        height, width = image.shape[:2]
        expected_width, expected_height = profile["image_width"], profile["image_height"]
        if abs(width / height - expected_width / expected_height) / (expected_width / expected_height) >= 0.01:
            raise ValueError("Lens profile aspect ratio differs from the camera frame")
        matrix = np.array(profile["camera_matrix"], np.float64)
        matrix[0] *= width / expected_width
        matrix[1] *= height / expected_height
        return cv2.undistort(image, matrix, np.array(profile["distortion_coefficients"], np.float64))
    return correct


def open_camera(index, width, height):
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if platform.system() == "Windows" else [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY] if platform.system() == "Darwin" else [cv2.CAP_V4L2, cv2.CAP_ANY]
    for backend in backends:
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            ok, _ = capture.read()
            if ok:
                return capture
        capture.release()
    raise ValueError(f"Cannot open camera {index}. Close the app camera stream and other camera programs, or try another --camera index.")


def main():
    parser = argparse.ArgumentParser(description="Estimate camera-visible width, height, and plane area from a known-size ArUco marker.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=1, help="Camera index (default: 1)")
    source.add_argument("--image", type=Path, help="Analyze a saved marker photo without opening a camera")
    parser.add_argument("--marker-id", type=int, default=10)
    parser.add_argument("--marker-size-mm", type=float, default=95, help="Physical outer black square side, excluding white margin")
    parser.add_argument("--dictionary", choices=DICTIONARIES, default="DICT_4X4_50")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--lens-profile", type=Path, help="Optional checkerboard camera_profile.json; omit for already-corrected photos")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "data" / "camera_coverage")
    parser.add_argument("--snapshot", action="store_true", help="Save one camera measurement and exit without a preview window")
    args = parser.parse_args()
    capture = None
    try:
        if not math.isfinite(args.marker_size_mm) or args.marker_size_mm <= 0 or args.width <= 0 or args.height <= 0:
            raise ValueError("Marker size and requested resolution must be positive")
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, args.dictionary))
        if not 0 <= args.marker_id < len(dictionary.bytesList):
            raise ValueError("Marker ID is outside the selected dictionary")
        correct = lens_corrector(args.lens_profile)
        print("Place the full marker flat on the surface being measured. The size is the outer black square, excluding white margins.")
        print("Lens correction: " + (str(args.lens_profile) if args.lens_profile else "none; results are approximate, especially near image edges"))
        if args.image:
            frame = cv2.imread(str(args.image))
            if frame is None:
                raise ValueError(f"Unable to read image: {args.image}")
            frame = correct(frame)
            report = analyze_frame(frame, args.marker_size_mm, args.marker_id, args.dictionary)
            report["lens_profile"] = str(args.lens_profile) if args.lens_profile else None
            print_report(report)
            save_report(args.output_dir, annotate_frame(frame, report), report)
            return 0
        capture = open_camera(args.camera, args.width, args.height)
        if args.snapshot:
            for _ in range(10):
                capture.read()
        last_print = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError("Camera frame capture failed")
            frame = correct(frame)
            report, error = None, None
            try:
                report = analyze_frame(frame, args.marker_size_mm, args.marker_id, args.dictionary)
                report["lens_profile"] = str(args.lens_profile) if args.lens_profile else None
            except ValueError as exc:
                error = str(exc)
            annotated = annotate_frame(frame, report, error)
            if args.snapshot:
                if report is None:
                    raise ValueError(error)
                print_report(report)
                save_report(args.output_dir, annotated, report)
                return 0
            if time.monotonic() - last_print >= 2:
                print_report(report) if report else print(error)
                last_print = time.monotonic()
            preview = cv2.resize(annotated, (min(1280, annotated.shape[1]), round(annotated.shape[0] * min(1, 1280 / annotated.shape[1]))))
            cv2.imshow("ArUco Camera Coverage", preview)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key == ord("s"):
                if report:
                    save_report(args.output_dir, annotated, report)
                else:
                    print("No current measurement to save. Show the full marker first.")
    except (ValueError, OSError, KeyError, cv2.error) as exc:
        print(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if capture is not None:
            capture.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
