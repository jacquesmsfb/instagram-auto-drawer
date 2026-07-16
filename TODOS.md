# TODOS

Deferred bonus features from the initial `/plan-eng-review` of the Instagram Auto Drawer GUI (2026-07-11). Core app ships without these; revisit if the plain version proves limiting.

## Done (2026-07-16)

- **Zoomable preview** — a dedicated, larger "Preview" panel now shows Preview Edges / Preview Contours output with scroll-to-zoom and drag-to-pan (double-click to reset). See `App._render_zoomed_preview` and friends in `app.py`.
- **Estimated drawing time before starting** — `drawing.estimate_drawing_seconds` / `format_duration`, logged as "Estimated drawing time: ~Xm Ys" when Start Drawing is clicked.
- **`min_contour_area` hairline bug** — `extract_contours` now filters hairline-shaped contours (open strokes) by stroke length instead of area, so straight vector-style lines with exactly-zero area no longer get discarded outright.
- **Drawing speed + quality**: `pyautogui.PAUSE` was left at its 0.1s default, adding ~0.2s of dead sleep per contour point (dwarfing the `mouse_speed` setting). Fixed to 0. Separately, `dragTo()`'s default `mouseDownUp=True` pressed and released the mouse button on every single point instead of holding it for the whole stroke, which was very likely the cause of "bad drawings" (broken/dotted lines) — fixed by holding the button down for each whole contour (see `draw_contours` in `drawing.py`).

## Drag-and-drop image support

**What:** Drop an image file onto the window instead of only using the file picker.

**Why:** Minor convenience; the file picker (`filedialog.askopenfilename`) already covers the full requirement (jpg/jpeg/png/bmp).

**Pros:** Slightly faster workflow for repeat use.

**Cons:** Native Tkinter has no drag-and-drop; needs the `tkinterdnd2` third-party package — a new dependency for a convenience feature, and it has known rough edges on some Tkinter/macOS combinations.

**Context:** If pursued, bind to the root window's `<<Drop>>` event via `tkinterdnd2.TkinterDnD`, validate the extension the same way the file picker does, and route through the same `load_image()` path so there's exactly one place that loads images.

**Depends on:** Nothing blocking.

## Estimated drawing time before starting

**What:** Before clicking Start Drawing, show an estimate like "~4m 30s for 411 contours."

**Why:** Nice-to-have expectation-setting; not required to hit the stated goal.

**Pros:** Cheap once the driver-injected drawing loop exists — sum contour point counts × per-point duration (draw delay + mouse-speed-derived duration).

**Cons:** Estimate will be approximate (doesn't account for OS scheduling jitter or actual dragTo timing variance) — needs a "~" framing, not an exact promise.

**Context:** Compute from the already-extracted, already-sorted, already-filtered contour list (same data the drawing loop consumes) — no new pipeline needed, just a sum over `len(contour) * (duration_per_point)` across all contours above `min_area`.

**Depends on:** The drawing engine's driver-injected loop (Test review, Architecture) needs to exist first so the per-point timing model is shared between the estimate and the real loop, not duplicated.

## Multi-monitor / mixed-DPI calibration support

**What:** Handle calibration correctly when the drawing canvas lives on a secondary monitor or a display with a different DPI scaling factor than the primary.

**Why:** Deferred in Architecture review (issue 4) — using `pyautogui.position()` for calibration keeps calibration and drawing in the same coordinate space on the *primary* display, but per-monitor scaling mismatches on secondary displays aren't explicitly handled.

**Pros:** Removes a documented known limitation.

**Cons:** Testing multi-monitor/mixed-DPI behavior is hard to verify without the actual hardware setup; real fix may require querying per-monitor scale factors (platform-specific, no clean cross-platform API).

**Context:** Document as a known limitation in the README for now: "calibration assumes the drawing canvas is on the main display." If a user hits this, the diagnostic signature is likely "drawing lands offset from the calibrated canvas" — the Accessibility-permission check (Architecture #8) rules out the other common cause of "nothing happens."

**Depends on:** Nothing blocking — independent investigation whenever it becomes a real pain point.

## `min_contour_area` filter can discard perfectly straight open strokes

**What:** `extract_contours` filters on `cv2.contourArea(contour)` before simplification. For a perfectly straight synthetic line, the forward and backward passes of the closed-loop contour exactly coincide on integer pixel coordinates, giving exactly zero area — so any `min_contour_area > 0` discards it entirely, regardless of how long or visually significant the stroke is.

**Why not fixed now:** Discovered while testing the hairline-dedup fix (2026-07-11) with a synthetic clean diagonal line. Real photographs (this app's actual use case) essentially never hit this — JPEG noise/blur means the forward/backward passes never perfectly coincide, leaving a sliver of nonzero area. Confirmed dragon.jpg (a real photo) is unaffected.

**Pros of fixing:** Makes the area filter correct for clean vector-style line art / logos, not just noisy photos.

**Cons:** The right fix is filtering on stroke length (`cv2.arcLength`) for hairline contours and area for real shapes — a small but real branch in `extract_contours`'s filtering logic, not a one-liner.

**Context:** See `_dedupe_hairline` and `HAIRLINE_AREA_PERIMETER_RATIO` in `image_processing.py` — the same area/perimeter signal that identifies hairline strokes there could also decide which metric (area vs. arc length) to filter on.

**Depends on:** Nothing blocking.
