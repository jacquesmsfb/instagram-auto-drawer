"""Coordinate/geometry math shared between calibration.py and drawing.py.

Scope is intentionally narrow: anything image-processing-specific belongs
in image_processing.py, anything config-specific belongs in config.py.
This module only exists for the screen<->canvas coordinate math both
calibration and drawing need to agree on.
"""

from __future__ import annotations

from typing import Tuple

Point = Tuple[int, int]


def canvas_dimensions(top_left: Point, bottom_right: Point) -> Tuple[int, int]:
    """Return (width, height) of the calibrated canvas rectangle."""
    width = bottom_right[0] - top_left[0]
    height = bottom_right[1] - top_left[1]
    return width, height


def is_degenerate_rect(top_left: Point, bottom_right: Point) -> bool:
    """True if the calibrated rectangle has zero (or negative) width/height."""
    width, height = canvas_dimensions(top_left, bottom_right)
    return width <= 0 or height <= 0


def image_point_to_screen(image_x: int, image_y: int, canvas_top_left: Point) -> Point:
    """Map a point in resized-image space (origin top-left of the image)
    to absolute screen coordinates, given the calibrated canvas's top-left."""
    screen_x = canvas_top_left[0] + int(image_x)
    screen_y = canvas_top_left[1] + int(image_y)
    return screen_x, screen_y
