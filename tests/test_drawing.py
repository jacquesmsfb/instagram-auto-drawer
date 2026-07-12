import threading
import time

import numpy as np
import pyautogui
import pytest

from drawing import draw_contours


class FakeDriver:
    """Records calls instead of moving a real mouse."""

    def __init__(self, fail_after: int = None):
        self.calls = []
        self._fail_after = fail_after

    def move_to(self, x, y):
        self.calls.append(("move", x, y))

    def drag_to(self, x, y, duration):
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise pyautogui.FailSafeException()
        self.calls.append(("drag", x, y, duration))


def make_contour(points):
    """Build a contour in the same [[x, y]] nested-array shape OpenCV uses."""
    return np.array([[[x, y]] for x, y in points], dtype=np.int32)


def test_draw_contours_happy_path_applies_offset():
    contours = [make_contour([(0, 0), (10, 0), (10, 10)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(100, 200), offset=(5, 5),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is True
    # move to first point (0,0) + top_left(100,200) + offset(5,5)
    assert driver.calls[0] == ("move", 105, 205)
    assert driver.calls[1] == ("drag", 115, 205, 0.001)
    assert driver.calls[2] == ("drag", 115, 215, 0.001)


def test_draw_contours_stops_before_starting_if_stop_already_set():
    contours = [make_contour([(0, 0), (10, 0)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    stop_event.set()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is False
    assert driver.calls == []


def test_draw_contours_stop_mid_second_contour_does_not_finish_remaining():
    contours = [
        make_contour([(0, 0), (1, 0)]),
        make_contour([(50, 50), (60, 50), (70, 50)]),
        make_contour([(90, 90), (95, 95)]),
    ]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    logs = []

    def on_progress(i, _total):
        # Stop right after the first contour completes — the loop should
        # never reach contour 3.
        if i == 1:
            stop_event.set()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        on_progress=on_progress, on_log=logs.append,
    )

    assert completed is False
    assert "Finished!" not in logs
    # Only the first contour's moves should have been issued.
    assert all(call[1] < 50 for call in driver.calls)


def test_draw_contours_pause_and_stop_together_exits_without_hanging():
    contours = [make_contour([(0, 0), (10, 0), (20, 0)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()
    stop_event.set()  # paused AND stopped -> must exit immediately, not hang

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is False


def test_draw_contours_catches_failsafe_exception():
    contours = [make_contour([(0, 0), (10, 0), (20, 0), (30, 0)])]
    driver = FakeDriver(fail_after=1)  # first drag succeeds, second raises
    stop_event = threading.Event()
    pause_event = threading.Event()
    logs = []

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        on_log=logs.append,
    )

    assert completed is False
    assert stop_event.is_set()
    assert any("Failsafe" in msg for msg in logs)


def test_draw_contours_pauses_when_real_mouse_flicked_to_corner():
    # Simulates the physical mouse being flicked to the top-left corner
    # WHILE drawing — a gesture that works even without this app's window
    # having keyboard focus (unlike the Space keybind).
    #
    # In production, pause_event is only ever CLEARED externally (the
    # user clicking Resume in the app, from a different thread) — the
    # drawing loop's own pause-wait never re-checks the mouse position.
    # So the test mirrors that: a watcher thread plays the role of
    # "user clicks Resume" once it observes the pause, with a bounded
    # wait so a regression (pause never triggering) fails fast instead
    # of hanging.
    contours = [make_contour([(0, 0), (10, 0), (20, 0), (30, 0)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()
    logs = []
    state = {"resumed": False}

    def fake_get_position():
        if state["resumed"]:
            return (500, 500)  # moved away after resuming — don't re-trigger
        return (2, 3)  # near corner (0,0), within PAUSE_CORNER_RADIUS_PX

    def simulate_resume_click():
        for _ in range(100):  # bounded: ~1s max, fails fast instead of hanging
            if pause_event.is_set():
                state["resumed"] = True
                time.sleep(0.02)
                pause_event.clear()
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=simulate_resume_click, daemon=True)
    watcher.start()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        on_log=logs.append, get_position=fake_get_position,
    )
    watcher.join(timeout=2)

    assert completed is True  # paused, then resumed, then finished normally
    assert any("Paused" in msg and "corner" in msg for msg in logs)


def test_draw_contours_does_not_pause_when_mouse_is_far_from_corner():
    contours = [make_contour([(0, 0), (10, 0)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        get_position=lambda: (900, 900),
    )

    assert completed is True
    assert pause_event.is_set() is False


def test_draw_contours_progress_callback_fires_per_contour():
    contours = [
        make_contour([(0, 0), (1, 0)]),
        make_contour([(5, 5), (6, 5)]),
    ]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()
    progress_calls = []

    draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        on_progress=lambda i, total: progress_calls.append((i, total)),
    )

    assert progress_calls == [(1, 2), (2, 2)]
