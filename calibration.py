"""Canvas calibration: countdown, capture two screen points, derive
the drawing canvas rectangle.

Uses pyautogui.position() (not a separate OS-level mouse hook) so
calibration and drawing.py agree on the same coordinate space — see
the plan's Architecture review, issue 4.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

import pyautogui

from utils import Point, canvas_dimensions, is_degenerate_rect

LogFn = Callable[[str], None]


class CalibrationError(Exception):
    """Raised when calibration produces an unusable (degenerate) rectangle."""


def check_mouse_control(log: LogFn = lambda msg: None) -> bool:
    """Cheap sanity check that this process can actually control the
    mouse. On macOS, a packaged (PyInstaller) app without Accessibility
    permission granted in System Settings silently no-ops every
    pyautogui call — no exception, mouse just doesn't move. This nudges
    the cursor by one pixel and verifies it actually moved, rather than
    letting that failure mode be a mystery to the user.
    """
    try:
        start = pyautogui.position()
        target = (start[0] + 1, start[1])
        pyautogui.moveTo(*target, duration=0)
        moved_to = pyautogui.position()
        pyautogui.moveTo(*start, duration=0)  # restore
        if tuple(moved_to) != tuple(target):
            log(
                "Could not move the mouse — check System Settings > Privacy & "
                "Security > Accessibility and enable this app."
            )
            return False
        return True
    except Exception:  # pragma: no cover - platform-dependent failure path
        log(
            "Could not move the mouse — check System Settings > Privacy & "
            "Security > Accessibility and enable this app."
        )
        return False


def countdown_and_capture(label: str, seconds: int, log: LogFn = lambda msg: None) -> Point:
    """Count down, then capture and return the current mouse position.

    Reused for both the top-left and bottom-right calibration points so
    the countdown/prompt behavior can't drift out of sync between them.
    """
    for remaining in range(seconds, 0, -1):
        log(f"Move mouse to {label} corner... {remaining}")
        time.sleep(1)
    point = pyautogui.position()
    log(f"Captured {label}: {tuple(point)}")
    return tuple(point)


def calibrate(countdown_seconds: int = 5, log: LogFn = lambda msg: None) -> Tuple[Point, Point]:
    """Run the full two-corner calibration flow. Returns (top_left, bottom_right).

    Raises CalibrationError if the resulting rectangle is degenerate
    (zero or negative width/height) rather than silently saving an
    unusable calibration.
    """
    top_left = countdown_and_capture("top-left", countdown_seconds, log)
    bottom_right = countdown_and_capture("bottom-right", countdown_seconds, log)

    if is_degenerate_rect(top_left, bottom_right):
        raise CalibrationError(
            "Bottom-right must be below and to the right of top-left — calibration not saved."
        )

    width, height = canvas_dimensions(top_left, bottom_right)
    log(f"Canvas calibrated: {width}x{height}")
    return top_left, bottom_right
