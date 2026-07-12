"""Persisted app settings and canvas calibration.

ConfigManager is the single source of truth for every setting the GUI
exposes. Widgets read from it on startup and write to it on change;
drawing.py reads from it directly rather than from Tkinter widget state,
so drawing logic can be tested without a GUI.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Tuple

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS: dict[str, Any] = {
    "last_image_path": None,
    "calibration": None,  # {"top_left": [x, y], "bottom_right": [x, y]}
    "detail": 0.003,
    "min_contour_area": 10,
    "canny_threshold_1": 80,
    "canny_threshold_2": 150,
    "gaussian_blur": True,
    "draw_delay": 5,
    "mouse_speed": 0.001,  # seconds per drag point, matches main.py's duration
}


class ConfigManager:
    """Loads, holds, and persists settings + calibration to config.json."""

    def __init__(self, path: str = CONFIG_PATH, log=lambda msg: None) -> None:
        self._path = path
        self._log = log
        self._data: dict[str, Any] = self.load()

    def load(self) -> dict[str, Any]:
        if not os.path.exists(self._path):
            self._log("No saved settings found, using defaults")
            return dict(DEFAULTS)
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._log("Saved settings were corrupted, resetting to defaults")
            return dict(DEFAULTS)
        # Merge over defaults so newly-added settings get sane values
        # even if the user's config.json predates them.
        merged = dict(DEFAULTS)
        merged.update(loaded)
        return merged

    def save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self._data[key] = value
        if save:
            self.save()

    @property
    def calibration(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        cal = self._data.get("calibration")
        if not cal:
            return None
        return tuple(cal["top_left"]), tuple(cal["bottom_right"])

    def set_calibration(self, top_left: Tuple[int, int], bottom_right: Tuple[int, int]) -> None:
        self.set(
            "calibration",
            {"top_left": list(top_left), "bottom_right": list(bottom_right)},
        )

    def is_calibrated(self) -> bool:
        return self.calibration is not None
