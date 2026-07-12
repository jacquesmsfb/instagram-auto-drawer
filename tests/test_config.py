import json

from config import DEFAULTS, ConfigManager


def test_load_missing_file_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    cm = ConfigManager(path=str(path))
    assert cm.get("detail") == DEFAULTS["detail"]
    assert cm.is_calibrated() is False


def test_load_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json")
    cm = ConfigManager(path=str(path))
    assert cm.get("draw_delay") == DEFAULTS["draw_delay"]


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cm = ConfigManager(path=str(path))
    cm.set("detail", 0.02)
    cm.set_calibration((10, 20), (110, 220))

    reloaded = ConfigManager(path=str(path))
    assert reloaded.get("detail") == 0.02
    assert reloaded.calibration == ((10, 20), (110, 220))
    assert reloaded.is_calibrated() is True


def test_merged_defaults_survive_partial_saved_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"detail": 0.01}))
    cm = ConfigManager(path=str(path))
    assert cm.get("detail") == 0.01
    assert cm.get("mouse_speed") == DEFAULTS["mouse_speed"]


def test_set_without_save_does_not_touch_disk(tmp_path):
    path = tmp_path / "config.json"
    cm = ConfigManager(path=str(path))
    cm.set("detail", 0.02, save=False)
    assert not path.exists()
