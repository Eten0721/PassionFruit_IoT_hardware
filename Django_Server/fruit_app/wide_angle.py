"""Remove the off-axis stretch of an ultra-wide phone camera before ROI.

The phone already straightens lens distortion, so straight lines stay
straight but a round fruit away from the image centre is stretched into an
egg. Re-projecting the frame stereographically keeps every sphere circular
wherever it sits, with no per-station setting.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np


MIN_FOCAL_RATIO = 0.2
MAX_FOCAL_RATIO = 3.0


def _lens(height: int, width: int, focal_ratio: float):
    """Return focal length (px), output zoom and the image centre."""
    long_half = max(height, width) / 2
    focal = focal_ratio * max(height, width)
    # Keep both ends of the long side in frame: the outer stations sit there.
    zoom = long_half / (2 * focal * np.tan(np.arctan(long_half / focal) / 2))
    return focal, zoom, (width - 1) / 2, (height - 1) / 2


@lru_cache(maxsize=4)
def _maps(height: int, width: int, focal_ratio: float):
    focal, zoom, center_x, center_y = _lens(height, width, focal_ratio)
    x, y = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32),
    )
    dx, dy = x - center_x, y - center_y
    target_radius = np.maximum(np.hypot(dx, dy), 1e-6)
    angle = 2 * np.arctan(target_radius / zoom / (2 * focal))
    scale = focal * np.tan(angle) / target_radius
    return (
        (center_x + dx * scale).astype(np.float32),
        (center_y + dy * scale).astype(np.float32),
    )


def _validated(focal_ratio: float) -> float:
    focal_ratio = float(focal_ratio)
    if not MIN_FOCAL_RATIO <= focal_ratio <= MAX_FOCAL_RATIO:
        raise ValueError(
            f'超廣角 focal_ratio 必須介於 {MIN_FOCAL_RATIO} 與 '
            f'{MAX_FOCAL_RATIO}，目前為 {focal_ratio}。'
        )
    return focal_ratio


def rectify(image: np.ndarray, focal_ratio: float | None) -> np.ndarray:
    """Return ``image`` with off-axis fruit restored to its round outline.

    ``focal_ratio`` is the lens focal length in pixels divided by the long
    side of the frame; ``None`` leaves the image untouched.
    """
    if focal_ratio is None:
        return image
    map_x, map_y = _maps(image.shape[0], image.shape[1], _validated(focal_ratio))
    return cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR)


def station_area_scale(
    image_shape: tuple[int, ...], focal_ratio: float | None,
    x: float, y: float,
) -> float:
    """Return the factor that sizes a fruit at ``(x, y)`` as if it were centred.

    In a rectified frame a fruit at an outer station is drawn smaller only
    because it is farther from the lens; multiplying its image area by this
    factor makes the same fruit measure the same at every station.
    """
    if focal_ratio is None:
        return 1.0
    focal, zoom, center_x, center_y = _lens(
        image_shape[0], image_shape[1], _validated(focal_ratio),
    )
    radius = float(np.hypot(x - center_x, y - center_y)) / zoom
    cosine = float(np.cos(2 * np.arctan(radius / (2 * focal))))
    return ((1 + cosine) / (2 * cosine)) ** 2
