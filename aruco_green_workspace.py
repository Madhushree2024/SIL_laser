"""Crop a rendered camera preview to the green surface without changing measurements."""
import cv2
import numpy as np


def green_workspace(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 35, 25], np.uint8), np.array([100, 255, 255], np.uint8))
    kernel_size = max(5, round(min(frame.shape[:2]) / 100) | 1)
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((kernel_size, kernel_size), np.uint8))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < frame.shape[0] * frame.shape[1] * .05:
        return None
    # Fill internal holes so white markers and objects on the surface remain visible.
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
    return mask, cv2.boundingRect(contour)


def crop_green_preview(annotated, frame, workspace):
    panel = annotated[frame.shape[0]:]
    if workspace is None:
        cropped = np.zeros_like(frame)
        cv2.putText(cropped, 'Green working area not found', (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 180, 255), 2, cv2.LINE_AA)
    else:
        mask, (x, y, width, height) = workspace
        image = cv2.bitwise_and(annotated[:frame.shape[0]], annotated[:frame.shape[0]], mask=mask)
        cropped = image[y:y + height, x:x + width]
    # Crop the image only; preserve the measurement panel and its legibility.
    width = max(cropped.shape[1], 1100)
    result = np.full((cropped.shape[0] + panel.shape[0], width, 3), 25, np.uint8)
    result[:cropped.shape[0], :cropped.shape[1]] = cropped
    panel_width = min(width, panel.shape[1])
    result[cropped.shape[0]:, :panel_width] = panel[:, :panel_width]
    return result
