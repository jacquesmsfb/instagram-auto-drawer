"""The drawing loop: turns contours into real mouse movement.

Runs on a background thread (see app.py) so PyAutoGUI's blocking
dragTo() calls never freeze the Tkinter mainloop. The mouse driver is
injected so this loop can be unit-tested without moving a real mouse —
see the plan's Test review / Architecture review issue 1.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional, Protocol

import pyautogui

from utils import Point, image_point_to_screen

pyautogui.FAILSAFE = True


class MouseDriver(Protocol):
    """Anything that can move and drag the mouse. Real implementation
    wraps pyautogui; tests use a fake that records calls instead."""

    def move_to(self, x: int, y: int) -> None: ...

    def drag_to(self, x: int, y: int, duration: float) -> None: ...


class PyAutoGuiDriver:
    """Real mouse driver — the only place in this codebase that calls
    pyautogui.moveTo/dragTo directly."""

    def move_to(self, x: int, y: int) -> None:
        pyautogui.moveTo(x, y, duration=0)

    def drag_to(self, x: int, y: int, duration: float) -> None:
        pyautogui.dragTo(x, y, duration=duration, button="left")


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

            for point in contour[1:]:
                if stop_event.is_set():
                    return False
                check_corner_pause()
                while pause_event.is_set():
                    if stop_event.is_set():
                        return False
                    time.sleep(0.1)

                x, y = point[0]
                px, py = image_point_to_screen(x, y, origin)
                driver.drag_to(px, py, mouse_speed)

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
