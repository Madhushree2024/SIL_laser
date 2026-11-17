"""Show ArUco borders, corner ordering, dimensions and coplanar distances.

python aruco_visual_check.py --camera 1 --marker-size-mm 95
python aruco_visual_check.py --demo --marker-size-mm 100
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from app.calibration.aruco_bed import BedCalibrationRequest

from camera_aruco_coverage import DICTIONARIES, open_camera, save_report

from aruco_camera_calibration import run_calibration, calibrated_corrector, write_checkerboard, read_lens_profile

from aruco_green_workspace import green_workspace, crop_green_preview

EDGE_NAMES = ("top", "right", "bottom", "left")
COLORS = ((0, 200, 255), (255, 180, 0), (210, 100, 255), (80, 255, 80))


def map_points(points, homography):
    points = np.asarray(points, np.float64).reshape(-1, 2)
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ homography.T
    denominator = homogeneous[:, 2]
    if (not np.isfinite(homogeneous).all() or np.min(denominator) <= 0 <= np.max(denominator)
            or np.min(np.abs(denominator)) < 1e-9):
        raise ValueError("Perspective horizon crosses the markers; use a more overhead view")
    return homogeneous[:, :2] / denominator[:, None]


def closest_borders(a, b):
    """Closest points on two disjoint, convex marker borders, in plane mm."""
    best = None
    best_interior = -1.
    for i in range(4):
        for j in range(4):
            p, q = a[i], a[(i + 1) % 4]
            r, s = b[j], b[(j + 1) % 4]
            for point, start, end, reverse, origin_t in (
                    (p, r, s, False, 0.), (q, r, s, False, 1.),
                    (r, p, q, True, 0.), (s, p, q, True, 1.),
                    ((p + q) / 2, r, s, False, .5),
                    ((r + s) / 2, p, q, True, .5)):
                direction = end - start
                length2 = float(direction @ direction)
                t = np.clip(float((point - start) @ direction) / length2, 0, 1) if length2 else 0
                projection = start + t * direction
                distance = float(np.linalg.norm(point - projection))
                interior = min(t, 1 - t, origin_t, 1 - origin_t)
                # For equally close parallel edges prefer their interiors,
                # so the highlighted gap joins facing edges, not corner ties.
                if (best is None or distance < best[0] - 1e-7
                        or (abs(distance - best[0]) <= 1e-7 and interior > best_interior)):
                    best = (distance, projection if reverse else point,
                            point if reverse else projection, i, j)
                    best_interior = interior
    return best


def analyze_frame(frame, marker_size_mm=95., reference_id=10, dictionary="DICT_4X4_50"):
    if not np.isfinite(marker_size_mm) or marker_size_mm <= 0:
        raise ValueError("Marker size must be positive and finite")
    if dictionary not in DICTIONARIES:
        raise ValueError("Unsupported dictionary")
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = 101
    params.minMarkerPerimeterRate = 0.01
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary)), params)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    corners, ids, rejected = detector.detectMarkers(gray)
    ids = [] if ids is None else ids.flatten().tolist()
    polygons = [c.reshape(4, 2).astype(np.float64) for c in corners]
    homography = None
    warning = None
    if len(ids) != len(set(ids)):
        warning = "Duplicate IDs: use different IDs for each marker. Millimetres unavailable."
    elif reference_id not in ids:
        warning = f"Show reference ID {reference_id} to enable millimetres."
    else:
        target = np.array([[0, 0], [marker_size_mm, 0], [marker_size_mm, marker_size_mm], [0, marker_size_mm]], np.float32)
        homography = cv2.getPerspectiveTransform(polygons[ids.index(reference_id)].astype(np.float32), target)
        try:
            map_points(np.concatenate(polygons), homography)
        except ValueError as exc:
            homography = None
            warning = str(exc)
    markers = []
    for marker_id, polygon in zip(ids, polygons):
        # Diagonal intersection gives the projected physical centre under perspective.
        unit_h = cv2.getPerspectiveTransform(np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32), polygon.astype(np.float32))
        center = map_points([[.5, .5]], unit_h)[0]
        mapped = map_points(polygon, homography) if homography is not None else None
        markers.append({"id": marker_id, "corners_px": polygon.tolist(), "center_px": center.tolist(),
                        "edges_px": np.linalg.norm(np.roll(polygon, -1, axis=0) - polygon, axis=1).tolist(),
                        "corners_mm": None if mapped is None else mapped.tolist(),
                        "center_mm": None if mapped is None else map_points([center], homography)[0].tolist(),
                        "edges_mm": None if mapped is None else np.linalg.norm(np.roll(mapped, -1, axis=0) - mapped, axis=1).tolist()})
    pairs = []
    for a, b in combinations(markers, 2):
        pair = {"ids": [a["id"], b["id"]],
                "center_distance_px": float(np.linalg.norm(np.array(a["center_px"]) - b["center_px"]))}
        if homography is not None:
            distance, p, q, i, j = closest_borders(np.array(a["corners_mm"]), np.array(b["corners_mm"]))
            pair.update(center_distance_mm=float(np.linalg.norm(np.array(a["center_mm"]) - b["center_mm"])),
                        gap_mm=distance, gap_edges=[EDGE_NAMES[i], EDGE_NAMES[j]],
                        gap_endpoints_px=map_points([p, q], np.linalg.inv(homography)).tolist())
        pairs.append(pair)
    return {"reference_id": reference_id, "known_reference_side_mm": marker_size_mm,
            "dictionary": dictionary, "markers": markers, "pairs": pairs, "warning": warning,
            "rejected_candidates_px": [c.reshape(4, 2).tolist() for c in rejected],
            "note": "Physical size is supplied, not inferred. All mm measurements assume the reference plane. Reference edges are fixed by calibration; other marker sizes are independent estimates. Edge names follow decoded marker orientation, not screen orientation."}


def apply_bed_layout(report, layout):
    """Map using four surveyed centres; marker dimensions remain independent checks."""
    request = BedCalibrationRequest.model_validate(layout)
    if request.dictionary != report['dictionary']:
        raise ValueError('Bed layout and detector dictionaries must match')
    markers = report['markers']
    ids = [m['id'] for m in markers]
    missing = [m.marker_id for m in request.markers if ids.count(m.marker_id) != 1]
    homography = None
    warning = None
    if missing:
        warning = f'Show exactly one of each bed marker; missing or duplicate IDs: {missing}. Millimetres unavailable.'
    elif len(ids) != len(set(ids)):
        warning = 'Duplicate marker IDs; millimetres unavailable.'
    else:
        source = np.array([markers[ids.index(m.marker_id)]['center_px'] for m in request.markers], np.float32)
        target = np.array([[m.x_mm, m.y_mm] for m in request.markers], np.float32)
        order = cv2.convexHull(source, returnPoints=False).flatten()
        if len(order) != 4 or cv2.contourArea(source[order]) < 1 or not cv2.isContourConvex(target[order]):
            raise ValueError('Bed marker positions do not match the configured rectangle; check IDs')
        homography, _ = cv2.findHomography(source, target, method=0)
        if homography is None or not np.isfinite(homography).all() or abs(np.linalg.det(homography)) < 1e-12:
            raise ValueError('Cannot resolve bed coordinate mapping')
        map_points(np.concatenate([m['corners_px'] for m in markers]), homography)
    edge_errors = []
    for marker in markers:
        if homography is None:
            marker.update(corners_mm=None, center_mm=None, edges_mm=None)
            continue
        mapped = map_points(marker['corners_px'], homography)
        edges = np.linalg.norm(np.roll(mapped, -1, axis=0) - mapped, axis=1)
        marker.update(corners_mm=mapped.tolist(), center_mm=map_points([marker['center_px']], homography)[0].tolist(), edges_mm=edges.tolist())
        error = float(np.max(np.abs(edges / report['known_reference_side_mm'] - 1)))
        marker['max_edge_size_error_percent'] = error * 100
        if error > .10:
            edge_errors.append(marker['id'])
    pairs = []
    for a, b in combinations(markers, 2):
        pair = {'ids': [a['id'], b['id']], 'center_distance_px': float(np.linalg.norm(np.array(a['center_px']) - b['center_px']))}
        if homography is not None:
            distance, p, q, i, j = closest_borders(np.array(a['corners_mm']), np.array(b['corners_mm']))
            pair.update(center_distance_mm=float(np.linalg.norm(np.array(a['center_mm']) - b['center_mm'])), gap_mm=distance,
                        gap_edges=[EDGE_NAMES[i], EDGE_NAMES[j]], gap_endpoints_px=map_points([p, q], np.linalg.inv(homography)).tolist())
        pairs.append(pair)
    if edge_errors:
        warning = f'Marker edges differ from the known size by over 10%: IDs {edge_errors}. Check lens correction and surface flatness.'
    report.update(pairs=pairs, warning=warning, mapping_source='surveyed_bed_marker_centres',
                  bed_layout=layout, homography=None if homography is None else homography.tolist(),
                  note='Origin is ID11 centre; X toward ID12, Y toward ID10 for the configured rectangle. Centre spacings are supplied calibration constraints, not independent validation. Marker edges and interior dimensions remain estimates. Full corrected-image pixel coordinates are preserved. No laser-machine alignment is implied.')
    return report


def annotate_frame(frame, report, show_rejected=False):
    image = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    def point(p):
        return tuple(np.round(p).astype(int))
    def label(value, position, color=(255, 255, 255)):
        cv2.putText(image, value, point(position), cv2.FONT_HERSHEY_SIMPLEX, .48, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(image, value, point(position), cv2.FONT_HERSHEY_SIMPLEX, .48, color, 1, cv2.LINE_AA)
    if show_rejected:
        for polygon in report["rejected_candidates_px"]:
            cv2.polylines(image, [np.round(polygon).astype(np.int32)], True, (100, 100, 100), 1)
    for marker in report["markers"]:
        polygon = np.array(marker["corners_px"])
        for i in range(4):
            p, q = polygon[i], polygon[(i + 1) % 4]
            cv2.line(image, point(p), point(q), COLORS[i], 2, cv2.LINE_AA)
            cv2.circle(image, point(p), 5, COLORS[i], -1)
            label(f"C{i}", p + [6, -6], COLORS[i])
            text = f"{EDGE_NAMES[i]} {marker['edges_px'][i]:.1f}px"
            if marker["edges_mm"] is not None:
                text += f" / {marker['edges_mm'][i]:.1f}mm"
            label(text, (p + q) / 2 + [5, 16], COLORS[i])
        center = np.array(marker["center_px"])
        cv2.drawMarker(image, point(center), (255, 255, 255), cv2.MARKER_CROSS, 15, 2)
        label(f"ID {marker['id']}" + (" ORIGIN" if report.get("bed_layout") and marker["id"] == 11 else " REF" if not report.get("bed_layout") and marker["id"] == report["reference_id"] else ""), center + [8, -10])
    markers_by_id = {m["id"]: m for m in report["markers"]}
    lines = ["Outer BLACK border detected (white paper margin excluded)",
             "C0->C1 top | C1->C2 right | C2->C3 bottom | C3->C0 left (marker orientation)",
             f"Known reference ID {report['reference_id']}: {report['known_reference_side_mm']:g}mm; flat shared plane assumed"]
    if report.get("bed_layout"):
        lines[2] = "Surveyed marker centres define mm mapping; expected black marker side: " + f"{report['known_reference_side_mm']:g}mm"
        lines.insert(3, "Coordinates: " + " | ".join(f"ID{m['marker_id']} ({m['x_mm']:g},{m['y_mm']:g})" for m in report['bed_layout']['markers']))
    if report["warning"]:
        lines.append(report["warning"])
    if not report["markers"]:
        lines.append("No decoded marker. Show a full marker with a white margin.")
    for pair in report["pairs"]:
        a, b = [np.array(markers_by_id[i]["center_px"]) for i in pair["ids"]]
        sky_blue = (235, 206, 135)  # OpenCV BGR: RGB 135, 206, 235.
        cv2.line(image, point(a), point(b), sky_blue, 2, cv2.LINE_AA)
        for endpoint in (a, b):
            cv2.circle(image, point(endpoint), 3, sky_blue, -1, cv2.LINE_AA)
        delta = b - a
        diagonal = abs(delta[0]) > 100 and abs(delta[1]) > 100
        fraction = .38 if diagonal else .5
        position = a + fraction * delta
        offset = np.array([12., -30.])
        if abs(delta[1]) > 2 * abs(delta[0]):
            offset = np.array([18., 0.])
        center_value = (f"{pair['center_distance_mm']:.1f} mm" if 'center_distance_mm' in pair
                        else f"{pair['center_distance_px']:.1f} px")
        label(f"Centre {pair['ids'][0]}-{pair['ids'][1]}: {center_value}", position + offset, sky_blue)
        text = f"IDs {pair['ids'][0]}->{pair['ids'][1]} centres: {pair['center_distance_px']:.1f}px"
        if "gap_mm" in pair:
            text += f" / {pair['center_distance_mm']:.1f}mm; border gap {pair['gap_mm']:.1f}mm ({'/'.join(pair['gap_edges'])})"
            p, q = pair["gap_endpoints_px"]
            cv2.line(image, point(p), point(q), (0, 140, 255), 3, cv2.LINE_AA)
            label(f"gap {pair['gap_mm']:.1f}mm", (np.array(p) + q) / 2 + [0, -10], (0, 180, 255))
        lines.append(text)
    lines.append("Lens correction: " + ("ON (calibrated)" if report.get("lens_profile") else "OFF (synthetic demo)"))
    lines.append("S save | R rejected outlines | Q / Esc quit | distances assume the reference plane")
    # Separate panel keeps labels from hiding the detected marker borders.
    panel = np.full((28 * len(lines) + 10, max(image.shape[1], 1100), 3), 25, np.uint8)
    for i, text in enumerate(lines):
        cv2.putText(panel, text, (10, 26 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, .48, (220, 240, 220), 1, cv2.LINE_AA)
    canvas = np.zeros((image.shape[0] + panel.shape[0], panel.shape[1], 3), np.uint8)
    canvas[:image.shape[0], :image.shape[1]] = image
    canvas[image.shape[0]:] = panel
    return canvas


def demo_frame():
    image = np.full((700, 1100), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, x in ((10, 150), (0, 650)):
        image[250:450, x:x + 200] = cv2.aruco.generateImageMarker(dictionary, marker_id, 200)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=1)
    source.add_argument("--image", type=Path)
    source.add_argument("--demo", action="store_true", help="Synthetic two-marker example; no camera needed")
    parser.add_argument("--marker-size-mm", type=float, default=95, help="Measured reference outer black square side, excluding white margin")
    parser.add_argument("--reference-id", type=int, default=10)
    parser.add_argument("--dictionary", choices=DICTIONARIES, default="DICT_4X4_50")
    parser.add_argument("--lens-profile", type=Path, help="Saved JSON or NPZ camera lens profile; required for saved real photos")
    parser.add_argument("--recalibrate", action="store_true", help="Capture a new checkerboard calibration before measurement")
    parser.add_argument("--checkerboard", type=int, nargs=2, default=(9, 6), metavar=("COLS", "ROWS"), help="Inner corner counts")
    parser.add_argument("--square-size-mm", type=float, default=30.)
    parser.add_argument("--write-checkerboard", type=Path, help="Write the printable 9x6, 30mm SVG board and exit")
    parser.add_argument("--width", type=int, help="Capture width; defaults to saved calibration width")
    parser.add_argument("--height", type=int, help="Capture height; defaults to saved calibration height")
    parser.add_argument("--bed-layout", type=Path, help="JSON file containing surveyed marker-centre coordinates")
    parser.add_argument("--single-marker", action="store_true", help="Use the old single-marker scale instead of the surveyed working area")
    parser.add_argument("--no-crop-green", action="store_true", help="Show the full camera frame instead of only the green workspace")
    parser.add_argument("--headless", action="store_true", help="Save one annotated frame + JSON and exit")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "data" / "aruco_visual_check")
    args = parser.parse_args()
    capture = None
    try:
        if not np.isfinite(args.marker_size_mm) or args.marker_size_mm <= 0 or (args.width is not None and args.width <= 0) or (args.height is not None and args.height <= 0):
            raise ValueError("Size and resolution must be positive and finite")
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, args.dictionary))
        if not 0 <= args.reference_id < len(dictionary.bytesList):
            raise ValueError("Reference ID is outside the dictionary")
        if args.write_checkerboard:
            print(f"Printable board: {write_checkerboard(args.write_checkerboard).resolve()}")
            return 0
        if args.recalibrate and (args.image or args.demo or args.headless):
            raise ValueError("--recalibrate requires interactive live camera capture")
        if args.demo and args.lens_profile:
            raise ValueError("A synthetic demo does not use a physical camera lens profile")
        saved_json = Path(__file__).resolve().parent / "data" / "aruco_visual_check" / f"camera_{args.camera}_profile.json"
        saved_npz = Path(__file__).resolve().parent / "camera_calibration.npz"
        profile_path = args.lens_profile or (saved_json if saved_json.exists() else saved_npz if saved_npz.is_file() else saved_json)
        if args.recalibrate:
            if args.lens_profile and args.lens_profile.suffix.lower() == '.npz':
                raise ValueError("New checkerboard calibration saves JSON; omit --lens-profile to preserve the existing NPZ")
            profile_path = args.lens_profile or saved_json
        metadata = read_lens_profile(profile_path) if profile_path.exists() and not args.demo and not args.recalibrate else {}
        width = args.width if args.width is not None else int(metadata.get("image_width", 1280))
        height = args.height if args.height is not None else int(metadata.get("image_height", 720))
        correct = lambda image: image
        active_profile = None
        still = demo_frame() if args.demo else cv2.imread(str(args.image)) if args.image else None
        if args.image and still is None:
            raise ValueError(f"Cannot read {args.image}")
        if still is None and (args.recalibrate or not profile_path.exists()) and args.headless:
            raise ValueError("Calibrate interactively first; headless mode cannot capture checkerboards")
        if still is None:
            capture = open_camera(args.camera, width, height)
            for _ in range(10):
                capture.read()
        if capture is not None and (args.recalibrate or not profile_path.exists()):
            print("Calibration first: print the checkerboard at 100%, verify square size, then capture 12+ varied views.")
            result = run_calibration(capture, profile_path, tuple(args.checkerboard), args.square_size_mm, args.camera)
            if result is None:
                print("Calibration cancelled; measurement not started.")
                return 0
        if not args.demo:
            if not profile_path.exists():
                raise ValueError("A saved photo needs --lens-profile from this camera's checkerboard calibration")
            correct = calibrated_corrector(profile_path, args.camera if capture is not None else None)
            active_profile = str(profile_path.resolve())
            metadata = read_lens_profile(profile_path)
            print(f"Loaded camera calibration: {active_profile}")
        layout_path = args.bed_layout or Path(__file__).resolve().parent / "data" / "calibration" / "aruco_viewer_bed_layout.json"
        if args.bed_layout and args.single_marker:
            raise ValueError("Choose either --bed-layout or --single-marker")
        layout = None
        if not args.single_marker and not args.demo and (args.bed_layout or layout_path.exists()):
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            BedCalibrationRequest.model_validate(layout)
            print(f"Using surveyed bed marker coordinates: {layout_path.resolve()}")
        rejected = False
        print("Enter the measured BLACK square size. Place different marker IDs on one flat surface.")
        while True:
            if capture is not None:
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise ValueError("Camera frame capture failed")
            else:
                frame = still
            frame = correct(frame)
            report = analyze_frame(frame, args.marker_size_mm, args.reference_id, args.dictionary)
            if layout is not None:
                report = apply_bed_layout(report, layout)
            report["lens_profile"] = active_profile
            if active_profile:
                report["calibration_rms_px"] = metadata.get("rms_error")
            workspace = None
            crop_enabled = not args.no_crop_green and not args.demo
            if crop_enabled:
                workspace = green_workspace(frame)
                report["preview_crop"] = {"enabled": True, "green_found": workspace is not None,
                                          "xywh": list(workspace[1]) if workspace is not None else None,
                                          "measurement_coordinates": "full lens-corrected frame; crop changes presentation only"}
            annotated = annotate_frame(frame, report, rejected)
            if crop_enabled:
                annotated = crop_green_preview(annotated, frame, workspace)
            if args.headless:
                save_report(args.output_dir, annotated, report)
                return 0
            scale = min(1., 1500 / annotated.shape[1], 900 / annotated.shape[0])
            cv2.imshow("ArUco Visual Check", cv2.resize(annotated, None, fx=scale, fy=scale))
            key = cv2.waitKey(30) & 0xff
            if key in (ord("q"), 27) or cv2.getWindowProperty("ArUco Visual Check", cv2.WND_PROP_VISIBLE) < 1:
                return 0
            if key == ord("r"):
                rejected = not rejected
            if key == ord("s"):
                save_report(args.output_dir, annotated, report)
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
