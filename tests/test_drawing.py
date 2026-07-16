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
        self._drag_count = 0

    def move_to(self, x, y):
        self.calls.append(("move", x, y))

    def mouse_down(self):
        self.calls.append(("down",))

    def mouse_up(self):
        self.calls.append(("up",))

    def drag_to(self, x, y, duration):
        if self._fail_after is not None and self._drag_count >= self._fail_after:
            raise pyautogui.FailSafeException()
        self._drag_count += 1
        self.calls.append(("drag", x, y, duration))


def make_contour(points):
    """Build a contour in the same [[x, y]] nested-array shape OpenCV uses."""
    return np.array([[[x, y]] for x, y in points], dtype=np.int32)


def test_draw_contours_happy_path_applies_offset():
    # Points kept within MAX_DRAG_STEP_PX of each other so each one maps to
    # exactly one drag call — subdivision of longer jumps is covered
    # separately below.
    contours = [make_contour([(0, 0), (3, 0), (3, 4)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(100, 200), offset=(5, 5),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is True
    # move to first point (0,0) + top_left(100,200) + offset(5,5), then the
    # button is held down for the whole stroke: one down, all drags, one up.
    assert driver.calls[0] == ("move", 105, 205)
    assert driver.calls[1] == ("down",)
    assert driver.calls[2] == ("drag", 108, 205, 0.001)
    assert driver.calls[3] == ("drag", 108, 209, 0.001)
    assert driver.calls[4] == ("up",)


def test_draw_contours_subdivides_long_jumps_into_short_steps():
    # Regression test: a long jump between two widely-spaced contour points
    # (typical of a big, simplified shape after approxPolyDP) used to move
    # in a single instant jump, which the receiving canvas renders as one
    # straight chord instead of the real path — this is what made large
    # shapes look like "rushed", jagged scribbles. Every step here must be
    # no more than MAX_DRAG_STEP_PX apart, ending exactly on the target.
    from drawing import MAX_DRAG_STEP_PX

    contours = [make_contour([(0, 0), (23, 0)])]  # one long horizontal jump
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is True
    drags = [call for call in driver.calls if call[0] == "drag"]
    assert len(drags) > 1  # subdivided, not one instant jump
    assert drags[-1][1:3] == (23, 0)  # last step lands exactly on the target

    prev_x = 0
    for _, x, _y, _duration in drags:
        assert x - prev_x <= MAX_DRAG_STEP_PX
        prev_x = x


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
    assert all(call[1] < 50 for call in driver.calls if len(call) > 1)


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
    # The button must still be released even though the drag that raised
    # FAILSAFE never got the chance to release it itself.
    assert driver.calls[-1] == ("up",)


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


def test_draw_contours_holds_button_down_for_whole_contour_not_per_point():
    # Regression test: dragTo defaults to pressing AND releasing on every
    # call, which would turn each point into its own disconnected
    # press-move-release (a dotted line) instead of one continuous stroke.
    contours = [make_contour([(0, 0), (10, 0), (20, 0), (30, 0)])]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is True
    assert driver.calls.count(("down",)) == 1
    assert driver.calls.count(("up",)) == 1
    # down happens once, before every drag (however many subdivision
    # produced), up happens once, after all of them.
    down_index = driver.calls.index(("down",))
    up_index = driver.calls.index(("up",))
    drag_indices = [i for i, call in enumerate(driver.calls) if call[0] == "drag"]
    assert len(drag_indices) > 0
    assert down_index < min(drag_indices)
    assert up_index > max(drag_indices)


def test_draw_contours_each_contour_gets_its_own_down_up_pair():
    contours = [
        make_contour([(0, 0), (1, 0)]),
        make_contour([(50, 50), (60, 50)]),
    ]
    driver = FakeDriver()
    stop_event = threading.Event()
    pause_event = threading.Event()

    draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert driver.calls.count(("down",)) == 2
    assert driver.calls.count(("up",)) == 2


def test_draw_contours_releases_button_when_stopped_mid_contour():
    contours = [make_contour([(0, 0), (10, 0), (20, 0), (30, 0)])]
    stop_event = threading.Event()
    pause_event = threading.Event()

    class StoppingDriver(FakeDriver):
        def drag_to(self, x, y, duration):
            super().drag_to(x, y, duration)
            stop_event.set()  # simulate Stop being clicked mid-stroke

    driver = StoppingDriver()
    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
    )

    assert completed is False
    # Stopped after the first drag call (which may be a subdivided
    # intermediate step, not necessarily the raw contour point itself),
    # but the button must still be released rather than left stuck down.
    drags = [call for call in driver.calls if call[0] == "drag"]
    assert len(drags) == 1
    assert driver.calls[-1] == ("up",)


def test_draw_contours_forces_release_even_if_release_itself_hits_failsafe():
    contours = [make_contour([(0, 0), (10, 0), (20, 0)])]
    stop_event = threading.Event()
    pause_event = threading.Event()

    class FlakyReleaseDriver(FakeDriver):
        def __init__(self):
            super().__init__()
            self.up_attempts = 0

        def mouse_up(self):
            self.up_attempts += 1
            if self.up_attempts == 1:
                # Physical mouse happens to be sitting exactly on the
                # failsafe corner the instant we try to release.
                raise pyautogui.FailSafeException()
            super().mouse_up()

    driver = FlakyReleaseDriver()
    logs = []

    completed = draw_contours(
        contours, driver, canvas_top_left=(0, 0), offset=(0, 0),
        mouse_speed=0.001, stop_event=stop_event, pause_event=pause_event,
        on_log=logs.append,
    )

    # The forced release still re-raises FAILSAFE, so this is honored as a
    # real stop — but the button was released along the way (up_attempts
    # == 2: the failed attempt, then the forced bypass attempt).
    assert completed is False
    assert stop_event.is_set()
    assert driver.up_attempts == 2
    assert driver.calls[-1] == ("up",)
    assert any("Failsafe" in msg for msg in logs)


def test_estimate_drawing_seconds_sums_drag_points_plus_delay():
    from drawing import ESTIMATED_PER_POINT_OVERHEAD_S, estimate_drawing_seconds

    contours = [
        make_contour([(0, 0), (1, 0), (2, 0)]),  # 2 drag points
        make_contour([(0, 0), (1, 0)]),  # 1 drag point
    ]

    seconds = estimate_drawing_seconds(contours, mouse_speed=0.01, draw_delay=5)

    assert seconds == pytest.approx(5 + 3 * (0.01 + ESTIMATED_PER_POINT_OVERHEAD_S))


def test_estimate_drawing_seconds_handles_empty_contours():
    from drawing import estimate_drawing_seconds

    assert estimate_drawing_seconds([], mouse_speed=0.01, draw_delay=5) == 5


def test_format_duration_under_a_minute():
    from drawing import format_duration

    assert format_duration(12.4) == "~12s"


def test_format_duration_minutes_and_seconds():
    from drawing import format_duration

    assert format_duration(270) == "~4m 30s"
