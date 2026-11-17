"""Visible green board geometry in full-resolution camera pixel coordinates."""
import cv2
import numpy as np


def green_surface_workspace(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # Tolerate shaded green when locating the board; material segmentation is separate.
    mask = cv2.inRange(hsv, np.array([35, 35, 15], np.uint8), np.array([100, 255, 255], np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    kernel = max(5, round(min(frame.shape[:2]) / 100) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kernel, kernel), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours or max(cv2.contourArea(c) for c in contours) < frame.shape[0] * frame.shape[1] * .05:
        return None
    # Fill interior material/marker holes while following the actual outside
    # boundary; a convex hull would admit furniture beside a curved board edge.
    contour = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(contour)
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
    x, y, w, h = cv2.boundingRect(hull)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
        cv2.aruco.DetectorParameters())
    corners, marker_ids, _ = detector.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    # Four board references also exclude nearby green-tinted furniture or ceiling
    # joined to the surface by shadows. Use outer margins, never marker centres.
    board_ids = {10, 11, 12, 13}
    if marker_ids is not None and board_ids <= set(marker_ids.flatten().tolist()):
        outer_points = []
        for marker, marker_id in zip(corners, marker_ids.flatten()):
            if int(marker_id) in board_ids:
                points = marker.reshape(4, 2)
                centre = points.mean(axis=0)
                outer_points.extend(centre + (points - centre) * 2.2)
        envelope = cv2.convexHull(np.rint(outer_points).astype(np.int32))
        limit = np.zeros_like(mask)
        cv2.fillConvexPoly(limit, envelope, 255)
        mask = cv2.bitwise_and(mask, limit)
    for marker in corners:
        points = marker.reshape(4, 2)
        centre = points.mean(axis=0)
        if cv2.pointPolygonTest(hull, tuple(map(float, centre)), True) < -max(10, min(w, h) * .05):
            continue
        # ArUco returns the black border; retain its surrounding white margin.
        border = np.rint(centre + (points - centre) * 1.5).astype(np.int32)
        cv2.fillConvexPoly(mask, border, 255)
    return mask, cv2.boundingRect(cv2.findNonZero(mask))

