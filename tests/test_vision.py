import cv2
import numpy as np

from app.vision.sheath_detection import ClassicalSheathDetector


def test_classical_detector_finds_synthetic_sheath() -> None:
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:] = (25, 25, 25)
    points = np.array([[60, 50], [250, 45], [280, 170], [140, 210], [50, 160]], dtype=np.int32)
    cv2.fillPoly(image, [points], (80, 170, 190))
    result = ClassicalSheathDetector().detect(image, min_area_px=1000)
    assert len(result.pixel_polygon) >= 4
    assert len(result.mm_polygon) == len(result.pixel_polygon)
