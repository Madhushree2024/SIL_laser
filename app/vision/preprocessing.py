from __future__ import annotations

import cv2
import numpy as np


def segment_sheath(image: np.ndarray, blur_kernel: int = 5, morphology_kernel: int = 9, workspace_mask: np.ndarray | None = None, min_area_px: float = 5000) -> np.ndarray:
    if workspace_mask is not None and workspace_mask.shape != image.shape[:2]:
        raise ValueError("Working-area mask differs from image resolution")
    green_screen_mask = segment_cream_sheath_on_green_background(image, workspace_mask, min_area_px)
    if green_screen_mask is not None:
        return green_screen_mask

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    _, a, b = cv2.split(lab)
    color_distance = cv2.addWeighted(a, 0.45, b, 0.55, 0)
    if blur_kernel % 2 == 0:
        blur_kernel += 1
    blurred = cv2.GaussianBlur(color_distance, (blur_kernel, blur_kernel), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((morphology_kernel, morphology_kernel), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def segment_cream_sheath_on_green_background(image: np.ndarray, workspace_mask: np.ndarray | None = None, min_area_px: float = 5000) -> np.ndarray | None:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    green = cv2.inRange(hsv, np.array([35, 35, 25]), np.array([100, 255, 255]))
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))

    green_pixels = cv2.countNonZero(green)
    image_area = image.shape[0] * image.shape[1]
    if green_pixels < image_area * 0.05:
        return None

    ys, xs = np.where(green > 0)
    if len(xs) == 0:
        return None

    roi = np.zeros(green.shape, np.uint8)
    margin = max(60, int(min(image.shape[:2]) * 0.04))
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(image.shape[1] - 1, int(xs.max()) + margin)
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(image.shape[0] - 1, int(ys.max()) + margin)
    roi[y0 : y1 + 1, x0 : x1 + 1] = 255

    marker_exclusion = aruco_marker_exclusion_mask(image, expand=2.2)
    if marker_exclusion is not None:
        cream = cv2.inRange(hsv, np.array([5, 18, 55]), np.array([38, 210, 255]))
        pale_tan = cv2.bitwise_and(
            cv2.inRange(hsv, np.array([0, 10, 90]), np.array([45, 95, 255])),
            cv2.bitwise_not(green),
        )
        mask = cv2.bitwise_or(cream, pale_tan)
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(cv2.dilate(green, np.ones((3, 3), np.uint8))))
    else:
        _, saturation, value = cv2.split(hsv)
        not_green = cv2.bitwise_not(cv2.dilate(green, np.ones((5, 5), np.uint8)))
        bright_or_saturated = cv2.bitwise_or(cv2.inRange(value, 120, 255), cv2.inRange(saturation, 20, 255))
        mask = cv2.bitwise_and(not_green, cv2.inRange(value, 55, 255))
        mask = cv2.bitwise_and(mask, bright_or_saturated)
    mask = cv2.bitwise_and(mask, roi)
    component_mask = cv2.bitwise_and(mask, workspace_mask) if workspace_mask is not None else mask
    components, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    components = [contour for contour in components if cv2.contourArea(contour) >= min_area_px]
    if len(components) > 1:
        # Close each material separately so the existing 71px kernel cannot
        # bridge the visible green gap between two independent sheaths.
        separate = np.zeros_like(mask)
        for contour in components:
            piece = np.zeros_like(mask)
            cv2.drawContours(piece, [contour], -1, 255, -1)
            piece = cv2.morphologyEx(piece, cv2.MORPH_CLOSE, np.ones((71, 71), np.uint8))
            piece = cv2.morphologyEx(piece, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
            separate = cv2.bitwise_or(separate, piece)
        mask = separate
    else:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((71, 71), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))

    if workspace_mask is not None:
        mask = cv2.bitwise_and(mask, workspace_mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    valid = [contour for contour in contours if cv2.contourArea(contour) >= min_area_px]
    if sum(cv2.contourArea(contour) for contour in valid) < image_area * 0.02:
        return None

    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, valid, -1, 255, -1)
    return filled


def aruco_marker_exclusion_mask(image: np.ndarray, expand: float = 2.0) -> np.ndarray | None:
    if not hasattr(cv2, "aruco"):
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dictionaries = [cv2.aruco.DICT_4X4_50, cv2.aruco.DICT_4X4_100]
    all_corners = []
    for dictionary_id in dictionaries:
        dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        params = cv2.aruco.DetectorParameters()
        params.adaptiveThreshWinSizeMin = 3
        params.adaptiveThreshWinSizeMax = 101
        params.adaptiveThreshWinSizeStep = 10
        params.minMarkerPerimeterRate = 0.01
        params.maxMarkerPerimeterRate = 1.0
        params.polygonalApproxAccuracyRate = 0.08
        detector = cv2.aruco.ArucoDetector(dictionary, params)
        corners, ids, _ = detector.detectMarkers(gray)
        if ids is not None:
            all_corners.extend(corners)
    if not all_corners:
        return None

    exclusion = np.zeros(image.shape[:2], np.uint8)
    for corners in all_corners:
        pts = corners.reshape(4, 2)
        center = pts.mean(axis=0)
        expanded = center + (pts - center) * expand
        x0, y0 = np.floor(expanded.min(axis=0)).astype(int)
        x1, y1 = np.ceil(expanded.max(axis=0)).astype(int)
        pad = int(max(x1 - x0, y1 - y0) * 0.35)
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(image.shape[1] - 1, x1 + pad)
        y1 = min(image.shape[0] - 1, y1 + pad)
        exclusion[y0 : y1 + 1, x0 : x1 + 1] = 255
    return exclusion
