from __future__ import annotations

import cv2
import numpy as np


def contour_polygons(
    mask: np.ndarray,
    min_area_px: float = 5000,
    approximation_rate: float = 0.00025,
) -> list[list[list[float]]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    valid = [contour for contour in contours if cv2.contourArea(contour) >= min_area_px]
    if not valid:
        raise RuntimeError("Unable to detect sheath. Check lighting/background.")
    polygons = []
    for contour in sorted(valid, key=cv2.contourArea, reverse=True):
        epsilon = approximation_rate * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        polygons.append(approx.reshape(-1, 2).astype(float).tolist())
    return polygons


def largest_contour_polygon(mask: np.ndarray, min_area_px: float = 5000, approximation_rate: float = 0.00025) -> list[list[float]]:
    return contour_polygons(mask, min_area_px, approximation_rate)[0]


def draw_contour_overlay(image: np.ndarray, polygon: list[list[float]]) -> np.ndarray:
    overlay = image.copy()
    pts = np.array(polygon, dtype=np.int32).reshape(-1, 1, 2)
    cv2.polylines(overlay, [pts], True, (0, 255, 80), 3)
    return overlay
