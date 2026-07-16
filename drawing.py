"""The drawing loop: turns contours into real mouse movement.

Runs on a background thread (see app.py) so PyAutoGUI's blocking
dragTo() calls never freeze the Tkinter mainloop. The mouse driver is
injected so this loop can be unit-tested without moving a real mouse —
see the plan's Test review / Architecture review issue 1.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Callable, List, Optional, Protocol

import pyautogui

from utils import Point, image_point_to_screen

pyautogui.FAILSAFE = True

# PyAutoGUI sleeps PAUSE seconds after EVERY call (moveTo, dragTo,
# position(), ...) — 0.1s by default. draw_contours calls into pyautogui
# at least once per contour point (drag_to, plus position() for the
# corner-pause check), so the default PAUSE alone cost ~0.2s/point —
# roughly 100-200x the actual mouse_speed setting most of that time was
# supposedly controlling. This app already does its own per-point pacing
# via mouse_speed and its own stop/pause checks, so PyAutoGUI's blanket
# pause was pure dead time, not a real safety margin.
pyautogui.PAUSE = 0


class MouseDriver(Protocol):
    """Anything that can move and drag the mouse. Real implementation
    wraps pyautogui; tests use a fake that records calls instead."""

    def move_to(self, x: int, y: int) -> None: ...

    def mouse_down(self) -> None: ...

    def mouse_up(self) -> None: ...

    def drag_to(self, x: int, y: int, duration: float) -> None: ...


class PyAutoGuiDriver:
    """Real mouse driver — the only place in this codebase that calls
    pyautogui.moveTo/mouseDown/mouseUp/dragTo directly."""

    def move_to(self, x: int, y: int) -> None:
        pyautogui.moveTo(x, y, duration=0)

    def mouse_down(self) -> None:
        pyautogui.mouseDown(button="left")

    def mouse_up(self) -> None:
        pyautogui.mouseUp(button="left")

    def drag_to(self, x: int, y: int, duration: float) -> None:
        # mouseDownUp=False: the button is already held down for the whole
        # contour (see draw_contours) — pyautogui.dragTo's default of
        # pressing AND releasing on every single call would turn each
        # point into its own disconnected press-move-release, breaking
        # the stroke into a dotted line instead of one continuous drag.
        pyautogui.dragTo(x, y, duration=duration, button="left", mouseDownUp=False)


def _force_release(driver: MouseDriver) -> None:
    """Guarantee the mouse button gets released, even if PyAutoGUI's
    FAILSAFE re-triggers on the release call itself (the physical mouse
    still sitting on the failsafe corner at that exact instant — the one
    gap in draw_contours's corner checks, since nothing re-checks
    position between the last drag and this cleanup call). A stuck-down
    button is worse than a bypassed check on this one call, so we force
    it through — but we still re-raise, so the caller treats it as a
    real failsafe stop rather than silently continuing.
    """
    try:
        driver.mouse_up()
    except pyautogui.FailSafeException:
        was_enabled = pyautogui.FAILSAFE
        pyautogui.FAILSAFE = False
        try:
            driver.mouse_up()
        finally:
            pyautogui.FAILSAFE = was_enabled
        raise


ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]
PositionReader = Callable[[], Point]

# Same corner PyAutoGUI's own FAILSAFE watches by default (0, 0) — but with
# a real capture radius instead of requiring the exact pixel, so a quick
# flick of the physical mouse reliably lands inside it. This lets you pause
# from the *other* app's window (e.g. Instagram) without needing this
# window's keyboard focus, which a Tkinter keybind can't do — a Tkinter
# <space>/<Escape> binding only fires when this window itself has OS focus.
# PyAutoGUI's exact-pixel FAILSAFE stays active underneath as a backstop:
# if this check is somehow missed, hitting the literal corner still raises
# FailSafeException and hard-stops (see the except clause below).
PAUSE_CORNER: Point = (0, 0)
PAUSE_CORNER_RADIUS_PX = 25


def _near_pause_corner(pos: Point) -> bool:
    dx = pos[0] - PAUSE_CORNER[0]
    dy = pos[1] - PAUSE_CORNER[1]
    return (dx * dx + dy * dy) <= PAUSE_CORNER_RADIUS_PX * PAUSE_CORNER_RADIUS_PX


# Max canvas-pixel distance a single drag call is allowed to cover. PyAutoGUI
# only interpolates a dragTo() call when duration exceeds MINIMUM_DURATION
# (0.1s) — every duration this app uses (mouse_speed's whole slider range)
# is far below that, so PyAutoGUI moves the cursor in one instant jump
# per call, no matter the distance. cv2.approxPolyDP simplifies large,
# simple shapes (the big-area contours drawn first, see extract_contours'
# largest-first sort) down to a handful of widely-spaced vertices, so
# without subdividing, those jumps span tens-to-hundreds of pixels in a
# single ~10ms step — the receiving canvas (Instagram's included) only
# samples position on its own mousemove cadence, so it draws one straight
# chord between two far-apart samples instead of following the real path.
# That's what a "rushed circle" is: a big simplified shape's vertices
# connected by long, fast straight jumps instead of a traced curve. Small
# detail contours don't show this because their points are already close
# together. Capping the per-call distance turns one long jump into several
# short ones, so every visible motion is small enough to read as continuous
# drawing rather than a connect-the-dots scribble.
MAX_DRAG_STEP_PX = 5

# Rough floor for real per-drag-point wall-clock cost beyond the configured
# mouse_speed duration itself, dominated by OS-level mouse-event dispatch
# (e.g. macOS's ~10ms post-move settle, see PyAutoGUI's DARWIN_CATCH_UP_TIME)
# rather than anything mouse_speed controls. Used only for the pre-draw ETA
# below — the real loop's actual pace is whatever the OS + mouse_speed produce.
ESTIMATED_PER_POINT_OVERHEAD_S = 0.01


def _subdivide(start: Point, end: Point, max_step_px: float) -> List[Point]:
    """Points from just after `start` up to and including `end`, spaced no
    more than max_step_px apart — see MAX_DRAG_STEP_PX for why. Always
    returns at least one point (`end`), even when start == end."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)
    steps = max(1, math.ceil(distance / max_step_px))
    if steps == 1:
        return [end]
    return [(start[0] + dx * i / steps, start[1] + dy * i / steps) for i in range(1, steps + 1)]


def _drag_step_count(start: Point, end: Point, max_step_px: float = MAX_DRAG_STEP_PX) -> int:
    """How many drag calls _subdivide would issue for this segment —
    shared by draw_contours and estimate_drawing_seconds so the ETA and
    the real loop use exactly one counting rule, not two that can drift."""
    return max(1, math.ceil(math.hypot(end[0] - start[0], end[1] - start[1]) / max_step_px))


def estimate_drawing_seconds(contours: List, mouse_speed: float, draw_delay: float) -> float:
    """Rough total wall-clock estimate for the pre-draw "~Xm Ys" readout.
    Counts the same (possibly subdivided) per-segment drag calls
    draw_contours actually issues, so this estimate and the real loop
    share one timing model instead of two that can drift apart."""
    total_drag_points = 0
    for contour in contours:
        prev = contour[0][0]
        for point in contour[1:]:
            cur = point[0]
            total_drag_points += _drag_step_count(prev, cur)
            prev = cur
    return draw_delay + total_drag_points * (mouse_speed + ESTIMATED_PER_POINT_OVERHEAD_S)


def format_duration(seconds: float) -> str:
    """Format a duration for the ETA readout, e.g. "~4m 30s" or "~12s"."""
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    if minutes:
        return f"~{minutes}m {secs}s"
    return f"~{secs}s"


def draw_contours(
    contours: List,
    driver: MouseDriver,
    canvas_top_left: Point,
    offset: Point,
    mouse_speed: float,
    stop_event: threading.Event,
    pause_event: threading.Event,
    on_progress: Optional[ProgressCallback] = None,
    on_log: Optional[LogCallback] = None,
    get_position: PositionReader = pyautogui.position,
) -> bool:
    """Draw every contour in order. Checks stop_event before every single
    drag point (not just between contours) so Stop Drawing responds in
    under ~100ms regardless of how large the current contour is.

    Also checks the real mouse position against PAUSE_CORNER before every
    point — flicking the physical mouse to the top-left corner pauses
    drawing (see _near_pause_corner) even when this app's window doesn't
    have keyboard focus. Resume from the app's Resume button (or Space,
    once this window has focus again).

    Returns True if drawing completed normally, False if it was stopped
    early (by the Stop button or a FAILSAFE trigger).
    """
    log = on_log or (lambda msg: None)
    origin = (canvas_top_left[0] + offset[0], canvas_top_left[1] + offset[1])
    total = len(contours)

    def check_corner_pause() -> None:
        if not pause_event.is_set() and _near_pause_corner(get_position()):
            pause_event.set()
            log("Paused (mouse moved to corner) — click Resume to continue")

    try:
        for index, contour in enumerate(contours, start=1):
            if stop_event.is_set():
                return False

            check_corner_pause()
            while pause_event.is_set():
                if stop_event.is_set():
                    return False
                time.sleep(0.1)

            start_x, start_y = contour[0][0]
            sx, sy = image_point_to_screen(start_x, start_y, origin)
            driver.move_to(sx, sy)

            if len(contour) > 1:
                driver.mouse_down()
                prev_screen = (sx, sy)
                try:
                    for point in contour[1:]:
                        x, y = point[0]
                        target_screen = image_point_to_screen(x, y, origin)

                        # See MAX_DRAG_STEP_PX: a big simplified shape's
                        # vertices can be far apart, so walk to the target
                        # in several short steps instead of one long jump.
                        for step_x, step_y in _subdivide(prev_screen, target_screen, MAX_DRAG_STEP_PX):
                            if stop_event.is_set():
                                return False
                            check_corner_pause()
                            while pause_event.is_set():
                                if stop_event.is_set():
                                    return False
                                time.sleep(0.1)

                            driver.drag_to(round(step_x), round(step_y), mouse_speed)

                        prev_screen = target_screen
                finally:
                    # Runs on normal completion, early stop, and exceptions
                    # alike — the button never stays stuck down.
                    _force_release(driver)

            log(f"Drawing contour {index} of {total}")
            if on_progress:
                on_progress(index, total)

        log("Finished!")
        return True

    except pyautogui.FailSafeException:
        log("Failsafe triggered — drawing stopped")
        stop_event.set()
        return False


class DrawingThread(threading.Thread):
    """Runs draw_contours on a background thread and forwards log/progress
    messages to the main thread via a queue.Queue, polled with
    widget.after() — see app.py."""

    def __init__(
        self,
        contours: List,
        canvas_top_left: Point,
        offset: Point,
        mouse_speed: float,
        message_queue: "queue.Queue[str]",
        draw_delay: float = 0,
        driver: Optional[MouseDriver] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._contours = contours
        self._canvas_top_left = canvas_top_left
        self._offset = offset
        self._mouse_speed = mouse_speed
        self._queue = message_queue
        self._draw_delay = draw_delay
        self._driver = driver or PyAutoGuiDriver()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def run(self) -> None:
        remaining = self._draw_delay
        while remaining > 0:
            if self.stop_event.is_set():
                return
            self._queue.put(("log", f"Starting in {remaining}..."))
            time.sleep(1)
            remaining -= 1
        if self.stop_event.is_set():
            return

        draw_contours(
            self._contours,
            self._driver,
            self._canvas_top_left,
            self._offset,
            self._mouse_speed,
            self.stop_event,
            self.pause_event,
            on_progress=lambda i, n: self._queue.put(("progress", i, n)),
            on_log=lambda msg: self._queue.put(("log", msg)),
        )
