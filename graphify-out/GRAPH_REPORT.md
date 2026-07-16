# Graph Report - .  (2026-07-16)

## Corpus Check
- Corpus is ~9,225 words - fits in a single context window. You may not need a graph.

## Summary
- 231 nodes · 411 edges · 10 communities
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 22 edges (avg confidence: 0.79)
- Token cost: 59,818 input · 0 output

## Community Hubs (Navigation)
- App GUI & Preview Controls
- Drawing Time Estimate & Tests
- Mouse Driver & Drawing Loop Core
- Canvas Calibration & Coordinate Utils
- Image Processing Pipeline
- README & Dependencies Overview
- Settings & Calibration Persistence
- Image Processing Test Suite
- PyAutoGUI Speed Fix (TODO)

## God Nodes (most connected - your core abstractions)
1. `App` - 40 edges
2. `draw_contours()` - 29 edges
3. `ConfigManager` - 19 edges
4. `FakeDriver` - 17 edges
5. `make_contour()` - 15 edges
6. `calibrate()` - 11 edges
7. `MouseDriver` - 10 edges
8. `extract_contours()` - 8 edges
9. `countdown_and_capture()` - 7 edges
10. `PyAutoGuiDriver` - 7 edges

## Surprising Connections (you probably didn't know these)
- `Drawing speed + quality fix (done): PAUSE + continuous mouse-down stroke` --references--> `draw_contours()`  [EXTRACTED]
  TODOS.md → drawing.py
- `Drag-and-drop image support (deferred)` --references--> `load_image()`  [EXTRACTED]
  TODOS.md → image_processing.py
- `min_contour_area hairline bug fix (done)` --references--> `extract_contours()`  [EXTRACTED]
  TODOS.md → image_processing.py
- `App` --uses--> `ConfigManager`  [INFERRED]
  app.py → config.py
- `App` --uses--> `DrawingThread`  [INFERRED]
  app.py → drawing.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Instagram Auto Drawer module architecture** — app, drawing, image_processing, calibration, config, utils [INFERRED 0.85]
- **Pause/Resume/Stop control flow** — readme_pause_resume_stop, readme_emergency_stop_failsafe, app, drawing [INFERRED 0.75]
- **Contour extraction and drawing pipeline** — image_processing_extract_contours, image_processing_dedupe_hairline, drawing_draw_contours, drawing_estimate_drawing_seconds [INFERRED 0.85]

## Communities (10 total, 0 thin omitted)

### Community 0 - "App GUI & Preview Controls"
Cohesion: 0.11
Nodes (4): App, ndarray, Swap in a new source image for the detail preview panel.          reset_view=Fal, Reset the drawing-settings sliders/checkbox to defaults.         Does not clear

### Community 1 - "Drawing Time Estimate & Tests"
Cohesion: 0.12
Nodes (30): draw_contours(), estimate_drawing_seconds(), format_duration(), Rough total wall-clock estimate for the pre-draw "~Xm Ys" readout.     Sums one, Format a duration for the ETA readout, e.g. "~4m 30s" or "~12s"., Draw every contour in order. Checks stop_event before every single     drag poin, LogCallback, PositionReader (+22 more)

### Community 2 - "Mouse Driver & Drawing Loop Core"
Cohesion: 0.07
Nodes (22): Instagram Auto Drawer — CustomTkinter GUI.  Wires together config.py, image_proc, DrawingThread, _force_release(), MouseDriver, _near_pause_corner(), Point, PyAutoGuiDriver, The drawing loop: turns contours into real mouse movement.  Runs on a background (+14 more)

### Community 3 - "Canvas Calibration & Coordinate Utils"
Cohesion: 0.11
Nodes (26): calibrate(), CalibrationError, check_mouse_control(), countdown_and_capture(), Exception, Point, Canvas calibration: countdown, capture two screen points, derive the drawing can, Raised when calibration produces an unusable (degenerate) rectangle. (+18 more)

### Community 4 - "Image Processing Pipeline"
Cohesion: 0.12
Nodes (25): _dedupe_hairline(), detect_edges(), extract_contours(), HAIRLINE_AREA_PERIMETER_RATIO, ImageLoadError, _is_hairline_shaped(), load_image(), PipelineResult (+17 more)

### Community 5 - "README & Dependencies Overview"
Cohesion: 0.09
Nodes (20): CustomTkinter, Instagram Auto Drawer (project), Known limitations section, macOS Accessibility permission check, OpenCV, PyAutoGUI, customtkinter>=5.2,<6, numpy>=1.24 (+12 more)

### Community 6 - "Settings & Calibration Persistence"
Cohesion: 0.16
Nodes (10): Any, ConfigManager, Persisted app settings and canvas calibration.  ConfigManager is the single sour, Loads, holds, and persists settings + calibration to config.json., Settings + calibration persistence (config.json), test_load_corrupt_file_falls_back_to_defaults(), test_load_missing_file_returns_defaults(), test_merged_defaults_survive_partial_saved_file() (+2 more)

### Community 7 - "Image Processing Test Suite"
Cohesion: 0.17
Nodes (6): make_square_image(), test_dedupe_hairline_leaves_real_shapes_untouched(), test_detect_edges_runs_with_and_without_blur(), test_extract_contours_filters_by_min_area(), test_load_image_valid_returns_ndarray(), test_process_pipeline_end_to_end()

### Community 8 - "PyAutoGUI Speed Fix (TODO)"
Cohesion: 0.67
Nodes (3): pyautogui dragTo() / mouseDownUp, Drawing speed + quality fix (done): PAUSE + continuous mouse-down stroke, pyautogui.PAUSE

## Knowledge Gaps
- **15 isolated node(s):** `Image picker feature`, `One-time canvas calibration feature`, `Estimated drawing time feature`, `Full drawing settings feature`, `Background drawing (non-blocking UI) feature` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `App` connect `App GUI & Preview Controls` to `Mouse Driver & Drawing Loop Core`, `Settings & Calibration Persistence`?**
  _High betweenness centrality (0.277) - this node is a cross-community bridge._
- **Why does `draw_contours()` connect `Drawing Time Estimate & Tests` to `App GUI & Preview Controls`, `PyAutoGUI Speed Fix (TODO)`, `Mouse Driver & Drawing Loop Core`, `Canvas Calibration & Coordinate Utils`?**
  _High betweenness centrality (0.201) - this node is a cross-community bridge._
- **Why does `ConfigManager` connect `Settings & Calibration Persistence` to `App GUI & Preview Controls`, `Mouse Driver & Drawing Loop Core`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `App` (e.g. with `ConfigManager` and `DrawingThread`) actually correct?**
  _`App` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Image picker feature`, `One-time canvas calibration feature`, `Estimated drawing time feature` to the rest of the system?**
  _15 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `App GUI & Preview Controls` be split into smaller, more focused modules?**
  _Cohesion score 0.10609756097560975 - nodes in this community are weakly interconnected._
- **Should `Drawing Time Estimate & Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.11904761904761904 - nodes in this community are weakly interconnected._