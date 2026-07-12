import pyautogui
import pytest

import calibration


def test_countdown_and_capture_returns_mouse_position(monkeypatch):
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pyautogui, "position", lambda: (42, 99))

    logged = []
    point = calibration.countdown_and_capture("top-left", seconds=5, log=logged.append)

    assert point == (42, 99)
    assert any("top-left" in msg for msg in logged)


def test_calibrate_returns_correct_dimensions(monkeypatch):
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)
    positions = iter([(100, 200), (300, 500)])
    monkeypatch.setattr(pyautogui, "position", lambda: next(positions))

    top_left, bottom_right = calibration.calibrate(countdown_seconds=1, log=lambda _msg: None)

    assert top_left == (100, 200)
    assert bottom_right == (300, 500)


def test_calibrate_raises_on_degenerate_rectangle(monkeypatch):
    monkeypatch.setattr(calibration.time, "sleep", lambda _seconds: None)
    # bottom-right is above/left of top-left -> zero/negative width & height
    positions = iter([(300, 500), (100, 200)])
    monkeypatch.setattr(pyautogui, "position", lambda: next(positions))

    with pytest.raises(calibration.CalibrationError):
        calibration.calibrate(countdown_seconds=1, log=lambda _msg: None)


def test_check_mouse_control_success(monkeypatch):
    state = {"pos": (500, 500)}
    monkeypatch.setattr(pyautogui, "position", lambda: state["pos"])

    def fake_move_to(x, y, duration=0):
        state["pos"] = (x, y)

    monkeypatch.setattr(pyautogui, "moveTo", fake_move_to)

    assert calibration.check_mouse_control(log=lambda _msg: None) is True


def test_check_mouse_control_detects_no_permission(monkeypatch):
    # Mouse never actually moves — simulates a macOS app without Accessibility permission.
    monkeypatch.setattr(pyautogui, "position", lambda: (500, 500))
    monkeypatch.setattr(pyautogui, "moveTo", lambda *a, **k: None)

    logged = []
    assert calibration.check_mouse_control(log=logged.append) is False
    assert any("Accessibility" in msg for msg in logged)
