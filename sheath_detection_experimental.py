"""Independent green-bed sheath detector. Not imported by the installed application.

Quick start from the project directory:
  .venv-windows\\Scripts\\python.exe sheath_detection_experimental.py IMAGE --compare-current
  .venv-windows\\Scripts\\python.exe sheath_detection_experimental.py --live --compare-current

Inputs should be saved corrected images with the green bed visible around material.
Optional --workspace-mask is a same-size monochrome image: white means permitted.
Optional --background is a registered, empty-bed image from the same camera setup.
Optional --live opens a separate camera preview. No calibration updates, nesting,
export or production writes occur.
All output is pixels, not millimetres. Each run writes to a new output directory.
Experimental masks and holes are not production cutting geometry.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import cv2
import numpy as np

# Even the optional comparison must not update existing Python bytecode files.
sys.dont_write_bytecode = True


@dataclass
class Detection:
    mask: np.ndarray
    overlay: np.ndarray
    sheaths: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "ok"
    diagnostics: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"experimental": True, "units": "pixels", "status": self.status,
                "sheath_count": len(self.sheaths), "sheaths": self.sheaths,
                "warnings": self.warnings, "diagnostics": self.diagnostics,
                "production_use": "Not connected to the current application or cutting workflow."}


def _image(image: np.ndarray, name: str = "image") -> None:
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3
            or min(image.shape[:2]) < 8):
        raise ValueError(f"{name} must be a nonempty uint8 BGR image, at least 8 x 8 pixels.")


def _mask(mask: np.ndarray | None, shape: tuple[int, int], name: str) -> np.ndarray:
    if mask is None:
        return np.full(shape, 255, np.uint8)
    if not isinstance(mask, np.ndarray) or mask.shape != shape or mask.dtype != np.uint8:
        raise ValueError(f"{name} must be a same-size uint8 monochrome mask.")
    return np.where(mask > 0, 255, 0).astype(np.uint8)


def _markers(image: np.ndarray) -> np.ndarray:
    """Exclude only each marker and a small surrounding margin, not a large rectangle."""
    excluded = np.zeros(image.shape[:2], np.uint8)
    if not hasattr(cv2, "aruco"):
        return excluded
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for family in (cv2.aruco.DICT_4X4_50, cv2.aruco.DICT_4X4_100):
        parameters = cv2.aruco.DetectorParameters()
        parameters.minMarkerPerimeterRate = 0.01
        detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(family), parameters)
        corners, _, _ = detector.detectMarkers(gray)
        for quad in corners:
            points = quad.reshape(4, 2)
            center = points.mean(axis=0)
            padded = np.rint(center + (points - center) * 1.15).astype(np.int32)
            cv2.fillConvexPoly(excluded, padded, 255)
    return excluded


def _fill_small_dark_holes(material: np.ndarray, green: np.ndarray,
                           allowed: np.ndarray, markers: np.ndarray, limit: int) -> np.ndarray:
    """Fill only enclosed small non-green noise; preserve green holes and open notches."""
    if not limit:
        return material
    result = material.copy()
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(material), 8)
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if area > limit or x == 0 or y == 0 or x + width == material.shape[1] or y + height == material.shape[0]:
            continue
        region = labels[y:y + height, x:x + width] == index
        if (np.any(green[y:y + height, x:x + width][region])
                or np.any(markers[y:y + height, x:x + width][region])
                or np.any(allowed[y:y + height, x:x + width][region] == 0)):
            continue
        result[y:y + height, x:x + width][region] = 255
    return result


def detect(image: np.ndarray, *, workspace_mask: np.ndarray | None = None,
           background: np.ndarray | None = None, min_area_px: int = 5000,
           max_dark_hole_px: int = 64, marker_mask: np.ndarray | None = None) -> Detection:
    """Return new arrays and pixel geometry without mutating any input."""
    _image(image)
    if isinstance(min_area_px, bool) or not isinstance(min_area_px, int) or min_area_px < 1:
        raise ValueError("min_area_px must be a positive integer.")
    if isinstance(max_dark_hole_px, bool) or not isinstance(max_dark_hole_px, int) or max_dark_hole_px < 0:
        raise ValueError("max_dark_hole_px must be a non-negative integer.")
    if background is not None:
        _image(background, "background")
        if background.shape != image.shape:
            raise ValueError("Empty-bed background must have the same image dimensions.")
    allowed = _mask(workspace_mask, image.shape[:2], "workspace_mask")
    if not cv2.countNonZero(allowed):
        raise ValueError("workspace_mask has no permitted pixels.")
    markers = _markers(image) if marker_mask is None else _mask(marker_mask, image.shape[:2], "marker_mask")
    result = Detection(np.zeros(image.shape[:2], np.uint8), image.copy())
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    scope = allowed > 0
    if np.percentile(value[scope], 75) < 35:
        result.status = "rejected"
        result.warnings.append("Image is too dark for a reliable boundary. Increase illumination or exposure.")
        return result
    seeds = ((hue >= 30) & (hue <= 100) & (saturation >= 40)
             & (value >= 30) & scope & (markers == 0)).astype(np.uint8)
    fraction = float(seeds.sum() / np.count_nonzero(scope))
    result.diagnostics["green_seed_fraction"] = fraction
    if fraction < 0.05:
        result.status = "rejected"
        result.warnings.append("Insufficient visible green background; no full-frame fallback was used.")
        return result
    components, labels, stats, _ = cv2.connectedComponentsWithStats(seeds, 8)
    if components < 2:
        result.status = "rejected"
        result.warnings.append("No usable connected green bed found.")
        return result
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    seed_region = labels == main
    if np.count_nonzero(seed_region) < max(20, 0.02 * np.count_nonzero(scope)):
        result.status = "rejected"
        result.warnings.append("Green background is too fragmented for a reliable work area.")
        return result
    yy, xx = np.nonzero(seed_region)
    hull = cv2.convexHull(np.column_stack((xx, yy)).astype(np.int32))
    board = np.zeros(image.shape[:2], np.uint8)
    cv2.fillConvexPoly(board, hull, 255)
    board = cv2.bitwise_and(board, allowed)
    # Learn colour from the board pixels in this capture rather than assuming a fixed cream colour.
    center = float(np.median(hue[seed_region]))
    spread = float(np.percentile(np.abs(hue[seed_region].astype(float) - center), 95))
    tolerance = float(np.clip(spread + 6, 12, 30))
    green_bool = ((np.abs(hue.astype(float) - center) <= tolerance)
                  & (saturation >= 30) & (value >= 18))
    green = np.where(green_bool, 255, 0).astype(np.uint8)
    candidate = ((board > 0) & ~green_bool & (value >= 18) & (markers == 0))
    result.diagnostics.update(green_hue=center, hue_tolerance=tolerance,
                              roi_source="provided workspace clipped to visible green-bed hull" if workspace_mask is not None
                              else "inferred green-bed hull; not physical calibration")
    if workspace_mask is None:
        result.warnings.append("Work area inferred from the green bed. Supply a workspace mask for more reliable limits.")
    if background is not None:
        old_lab = cv2.cvtColor(background, cv2.COLOR_BGR2LAB).astype(np.float32)
        new_lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
        delta = np.linalg.norm(new_lab - old_lab, axis=2)
        candidate &= delta >= 18
        if float(np.median(delta[seed_region])) > 25:
            result.warnings.append("Background reference differs strongly on the green bed; check lighting and registration.")
    material = np.where(candidate, 255, 0).astype(np.uint8)
    material = _fill_small_dark_holes(material, green, board, markers, max_dark_hole_px)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(material, 8)
    selected = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_area_px]
    selected.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    material_colour = (((hue <= 35) & (saturation >= 12) & (value >= 40))
                       | ((saturation <= 70) & (value >= 90)))
    unsupported = 0
    image_edge_regions = 0
    for index in selected:
        bx, by, bw, bh, _ = [int(v) for v in stats[index]]
        if bx == 0 or by == 0 or bx + bw == image.shape[1] or by + bh == image.shape[0]:
            image_edge_regions += 1
            continue
        region = np.where(labels == index, 255, 0).astype(np.uint8)
        support = np.count_nonzero((region > 0) & material_colour) / stats[index, cv2.CC_STAT_AREA]
        if support < 0.08:
            unsupported += 1
            continue
        contours, hierarchy = cv2.findContours(region, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        outer_indices = [i for i, node in enumerate(hierarchy[0]) if node[3] == -1]
        if not outer_indices:
            continue
        outer = max(outer_indices, key=lambda i: cv2.contourArea(contours[i]))
        contour = contours[outer]
        if cv2.contourArea(contour) <= 0:
            continue
        holes = []
        child = int(hierarchy[0][outer][2])
        while child != -1:
            holes.append(contours[child].reshape(-1, 2).astype(float).tolist())
            child = int(hierarchy[0][child][0])
        warnings = []
        # Touching inferred/calibrated limits or marker exclusions means the complete edge is uncertain.
        restricted = cv2.bitwise_or(cv2.bitwise_not(board), markers)
        restricted[[0, -1], :] = 255
        restricted[:, [0, -1]] = 255
        near_limit = cv2.dilate(restricted, np.ones((3, 3), np.uint8))
        if np.any((region > 0) & (near_limit > 0)):
            warnings.append("Boundary touches a work-area or marker limit; complete shape may be clipped.")
        if stats[index, cv2.CC_STAT_AREA] > 0.85 * cv2.countNonZero(board):
            warnings.append("One region occupies most of the work area; inspect for a merged detection.")
        fraction_dark = float(np.count_nonzero((region > 0) & (value < 45)) / stats[index, cv2.CC_STAT_AREA])
        if fraction_dark > 0.35:
            warnings.append("Large dark region; inspect for a shadow or underexposed material.")
        result.mask[region > 0] = 255
        x, y, width, height, area = [int(v) for v in stats[index]]
        item = {"id": f"experimental-sheath-{len(result.sheaths) + 1}",
                "outer_polygon_px": contour.reshape(-1, 2).astype(float).tolist(),
                "holes_px": holes, "material_pixels": area,
                "bounds_px": {"x": x, "y": y, "width": width, "height": height},
                "warnings": warnings}
        result.sheaths.append(item)
        cv2.drawContours(result.overlay, contours, -1, (0, 220, 80), 2)
        cv2.putText(result.overlay, item["id"], (x, max(16, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 80), 1, cv2.LINE_AA)
    result.diagnostics["excluded_regions_at_image_edge"] = image_edge_regions
    if image_edge_regions:
        result.warnings.append(f"Excluded {image_edge_regions} region(s) cut off by the image edge; show the complete material inside the frame.")
    result.diagnostics["excluded_regions_without_material_colour"] = unsupported
    if unsupported:
        result.warnings.append(f"Excluded {unsupported} region(s) without enough pale or brown material evidence. Very dark material needs review.")
    result.diagnostics["material_pixels"] = cv2.countNonZero(result.mask)
    result.status = "review" if result.sheaths and any(p["warnings"] for p in result.sheaths) else ("ok" if result.sheaths else "no_material")
    if not result.sheaths:
        result.warnings.append("No material component meets the area threshold.")
    return result
def _read(path: Path, grayscale: bool = False) -> np.ndarray:
    # imdecode supports Windows filenames containing non-ASCII characters.
    try:
        data = np.frombuffer(path.read_bytes(), np.uint8)
    except OSError as error:
        raise ValueError(f"Cannot read {path}: {error}") from error
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Not a readable image: {path}")
    return image


def _save(path: Path, image: np.ndarray) -> None:
    ok, data = cv2.imencode(".png", image)
    if not ok:
        raise OSError(f"Could not encode {path.name}")
    with path.open("xb") as stream:
        stream.write(data.tobytes())


def _comparison(image: np.ndarray, result: Detection, old: np.ndarray) -> np.ndarray:
    previous = image.copy()
    contours, _ = cv2.findContours(old, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(previous, contours, -1, (0, 120, 255), 2)
    panels = []
    for title, panel in [("CURRENT - orange", previous), ("EXPERIMENTAL - green", result.overlay)]:
        banner = np.zeros((32, panel.shape[1], 3), np.uint8)
        cv2.putText(banner, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(np.vstack((banner, panel)))
    return np.hstack(panels)


def _current(image: np.ndarray, allowed: np.ndarray | None, min_area: int) -> np.ndarray:
    from app.vision.preprocessing import segment_sheath
    return segment_sheath(image.copy(), workspace_mask=allowed, min_area_px=min_area)


def _write_run(image: np.ndarray, result: Detection, args, *, allowed=None,
               current=None, background=None, live=False) -> Path:
    output = args.output.resolve() / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    _save(output / "experimental_mask.png", result.mask)
    _save(output / "experimental_overlay.png", result.overlay)
    summary = result.summary()
    summary.update(source_file=str(Path(__file__).resolve()), live_camera=live)
    if live:
        _save(output / "camera_frame.png", image)
        summary.update(camera_index=args.camera, input_image="camera_frame.png",
                       lens_correction_applied=False)
        if background is not None:
            _save(output / "background_reference.png", background)
    else:
        summary["input_image"] = str(args.image.resolve())
    if args.compare_current:
        try:
            old = current if current is not None else _current(image, allowed, args.min_area_px)
            _save(output / "current_mask.png", old)
            _save(output / "comparison.png", _comparison(image, result, old))
            summary["comparison"] = {"current_material_pixels": cv2.countNonZero(old),
                "experimental_material_pixels": cv2.countNonZero(result.mask),
                "disagreement_pixels": int(np.count_nonzero((old > 0) != (result.mask > 0))),
                "note": "Mask disagreement is not accuracy; no manually labelled ground truth is supplied."}
        except Exception as error:
            summary["comparison_error"] = str(error)
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
    print(json.dumps({"status": result.status, "sheaths": len(result.sheaths),
                      "output": str(output), "warnings": result.warnings}, indent=2))
    return output


def _camera_list() -> list[dict]:
    if sys.platform != "win32":
        raise ValueError("Automatic camera identities are supported on Windows. Specify --camera INDEX on this platform.")
    from app.camera.windows_devices import windows_camera_profiles, is_usb_camera
    return [{**item, "external": is_usb_camera(item)} for item in windows_camera_profiles()]


def _select_camera(index: int | None) -> int:
    if index is not None:
        if index < 0:
            raise ValueError("Camera index must be zero or greater.")
        return index
    external = [item for item in _camera_list() if item["external"]]
    if len(external) == 1:
        return int(external[0]["index"])
    raise ValueError("No unique external camera found. Run --list-cameras, then use --live --camera INDEX.")


def _open_camera(index: int, width: int, height: int):
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
    for backend in backends:
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            capture.set(cv2.CAP_PROP_FPS, 15)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return capture
        capture.release()
    raise ValueError(f"Cannot open camera {index}. Close Sheath Template Cutting or any other app using that camera, then retry.")


def _live(args) -> int:
    """Interactive pixel-only test, with no connection to the application's backend."""
    allowed = _read(args.workspace_mask, True) if args.workspace_mask else None
    background = _read(args.background) if args.background else None
    args.camera = _select_camera(args.camera)
    capture = _open_camera(args.camera, args.width, args.height)
    title = "Separate sheath detection test"
    last_image = last_result = old = preview = None
    next_detection = 0.0
    failures = 0
    note = "LIVE RAW CAMERA - PIXELS ONLY"
    show_mask = False
    try:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, 1440 if args.compare_current else 1000, 720)
        print(f"Live camera {args.camera}. Q/Esc: quit | S: save | M: mask | B: empty-bed reference | R: clear reference")
        print("Raw camera images are used; no millimetre or laser-origin calibration is changed.")
        while True:
            ok, image = capture.read()
            if not ok or image is None:
                failures += 1
                if failures >= 10:
                    raise ValueError("Camera stopped delivering frames. Check its connection and close competing camera apps.")
                if cv2.waitKey(50) & 0xFF in (27, ord("q")):
                    break
                continue
            failures = 0
            now = time.monotonic()
            if last_result is None or now >= next_detection:
                last_image = image.copy()
                last_result = detect(last_image, workspace_mask=allowed, background=background,
                                     min_area_px=args.min_area_px, max_dark_hole_px=args.max_dark_hole_px)
                old = None
                preview = last_result.overlay
                if args.compare_current:
                    try:
                        old = _current(last_image, allowed, args.min_area_px)
                        preview = _comparison(last_image, last_result, old)
                    except Exception as error:
                        note = f"Current comparison unavailable: {error}"
                next_detection = time.monotonic() + args.interval
            display = cv2.cvtColor(last_result.mask, cv2.COLOR_GRAY2BGR) if show_mask else preview
            banner = np.zeros((76, display.shape[1], 3), np.uint8)
            state = f"{last_result.status.upper()} | sheaths: {len(last_result.sheaths)} | camera: {args.camera} | pixels only"
            cv2.putText(banner, state, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 255, 170), 1, cv2.LINE_AA)
            cv2.putText(banner, "Q/Esc quit | S save | M mask | B empty bed | R clear", (8, 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
            cv2.putText(banner, note[:140], (8, 67), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (210, 210, 210), 1, cv2.LINE_AA)
            cv2.imshow(title, np.vstack((banner, display)))
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("m"):
                show_mask = not show_mask
            elif key == ord("s"):
                output = _write_run(last_image, last_result, args, allowed=allowed,
                                    current=old, background=background, live=True)
                note = "Saved: " + str(output)
            elif key == ord("b"):
                if last_result.status == "no_material":
                    background = last_image.copy()
                    note = "Empty-bed reference captured for this session."
                    next_detection = 0
                else:
                    note = "Remove material and use a well-lit empty green bed before pressing B."
            elif key == ord("r"):
                background = None
                note = "Background reference cleared."
                next_detection = 0
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
        return 0
    finally:
        capture.release()
        try:
            cv2.destroyWindow(title)
        except cv2.error:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", nargs="?", type=Path, help="Saved corrected image, or omit for --live.")
    parser.add_argument("--live", action="store_true", help="Open a separate live camera preview.")
    parser.add_argument("--camera", type=int, help="Camera index; otherwise choose a single identified USB camera.")
    parser.add_argument("--list-cameras", action="store_true", help="List Windows camera names and indexes without opening them.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--interval", type=float, default=0.3, help="Seconds between live detection updates.")
    parser.add_argument("--workspace-mask", type=Path, help="Same-size monochrome mask, white inside the usable area.")
    parser.add_argument("--background", type=Path, help="Empty-bed image from the same view and lighting.")
    parser.add_argument("--min-area-px", type=int, default=5000)
    parser.add_argument("--max-dark-hole-px", type=int, default=64)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output/sheath_detection_experimental")
    parser.add_argument("--compare-current", action="store_true", help="Show current and experimental outlines side by side.")
    args = parser.parse_args(argv)
    if args.live and args.image is not None:
        parser.error("Choose a saved image or --live, not both.")
    if not args.list_cameras and not args.live and args.image is None:
        parser.error("Supply an image path, --live, or --list-cameras.")
    if (args.width < 8 or args.height < 8 or not np.isfinite(args.interval)
            or args.interval < 0.05 or args.min_area_px < 1 or args.max_dark_hole_px < 0):
        parser.error("Use positive dimensions/area, an interval of at least 0.05 seconds, and a non-negative hole limit.")
    try:
        if args.list_cameras:
            print(json.dumps(_camera_list(), indent=2))
            return 0
        if args.live:
            return _live(args)
        image = _read(args.image)
        allowed = _read(args.workspace_mask, True) if args.workspace_mask else None
        background = _read(args.background) if args.background else None
        result = detect(image, workspace_mask=allowed, background=background,
                        min_area_px=args.min_area_px, max_dark_hole_px=args.max_dark_hole_px)
        _write_run(image, result, args, allowed=allowed)
        return 2 if result.status == "rejected" else 0
    except KeyboardInterrupt:
        print("Live test stopped.")
        return 0
    except (ValueError, OSError, cv2.error) as error:
        parser.exit(2, f"Experimental detection error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
