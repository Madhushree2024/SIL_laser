"""Display the green work surface without changing calibration pixel coordinates."""
import math

import cv2
import numpy as np


from app.vision.green_surface import green_surface_workspace as _preview_workspace


def _brightness_lut(frame, mask):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    samples = hsv[:, :, 2][(mask > 0) & (hsv[:, :, 1] > 35) &
                              (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 100)]
    median = float(np.median(samples)) if samples.size else 130.0
    gamma = max(.45, min(1.0, math.log(130 / 255) / math.log(max(1, min(254, median)) / 255)))
    return np.rint(255 * (np.arange(256) / 255) ** gamma).astype(np.uint8), round(gamma, 3)


def green_preview(frame, workspace=None, *, brightness=None):
    if workspace is None:
        workspace = _preview_workspace(frame)
    height, width = frame.shape[:2]
    if workspace is None:
        return frame.copy(), {'x':0,'y':0,'width':width,'height':height,'source_width':width,'source_height':height,'green_found':False,'display_gamma':1.0}
    mask, (x,y,w,h) = workspace
    lut, gamma = brightness if brightness is not None else _brightness_lut(frame, mask)
    # Display-only correction: raw captures, lens calibration, measurement and
    # nesting continue to use the unmodified full-resolution camera frame.
    image = cv2.LUT(frame, lut)
    image = cv2.bitwise_and(image, image, mask=mask)
    return image, {'x':x,'y':y,'width':w,'height':h,'source_width':width,'source_height':height,'green_found':True,'display_gamma':gamma}


class LiveGreenPreview:
    def __init__(self):
        self.reset()

    def reset(self):
        self.workspace = None
        self.metadata = None
        self.image_size = None
        self.brightness = None
        self.frames = 0

    def apply(self, frame):
        size = frame.shape[:2]
        if size != self.image_size:
            self.reset()
            self.image_size = size
        # Refresh after exposure/lighting changes; do not freeze a startup mask.
        if self.workspace is None or self.frames % 15 == 0:
            self.workspace = _preview_workspace(frame)
            self.brightness = _brightness_lut(frame, self.workspace[0]) if self.workspace is not None else None
        image, metadata = green_preview(frame, self.workspace, brightness=self.brightness)
        self.frames += 1
        self.metadata = metadata
        return image
