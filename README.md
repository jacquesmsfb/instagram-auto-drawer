# Instagram Auto Drawer

A desktop GUI for turning any image into contour-traced mouse drawing on Instagram's (or any) drawing canvas. Load an image, calibrate the canvas once, tune detection settings with live preview, and let it draw — no source code editing required to change images or settings.

Built with CustomTkinter, OpenCV, and PyAutoGUI.

## Features

- **Image picker** with thumbnail preview (jpg, jpeg, png, bmp)
- **One-time canvas calibration** — point at the top-left and bottom-right corners of your drawing canvas, the app remembers it
- **Live edge/contour preview** as you tune sliders, debounced so it stays responsive
- **Full drawing settings**: detail level, min contour area, Canny edge thresholds, Gaussian blur toggle, draw delay, mouse speed
- **Background drawing** — the window stays fully responsive while drawing runs, since it never blocks the UI thread
- **Pause / Resume and Stop**, both from buttons and global keybinds (works even if you're not focused on the app window)
- **Emergency stop** via PyAutoGUI's FAILSAFE (drag mouse to a screen corner) — caught and logged cleanly instead of crashing
- **macOS Accessibility permission check** — tells you clearly if the app can't control the mouse, instead of silently doing nothing
- **Duplicate-line detection** — the pipeline finds and drops the redundant "return trip" `findContours` produces for open strokes (it always traces closed loops, so an open line gets traced forward then back over almost the same pixels). Cuts total draw points substantially, which also means faster overall drawing and less risk of the extra-long sessions that can crash Instagram's own drawing tool.
- **Settings + calibration persist** across restarts in `config.json`

## Pausing and stopping

| Method | Action | Works without app window focus? |
|---|---|---|
| `Esc` key | Force **Stop** immediately | No — needs this window focused |
| `Space` key | Toggle **Pause / Resume** | No — needs this window focused |
| Flick mouse to top-left screen corner | **Pause** (click Resume in the app to continue) | **Yes** — works even while you're looking at Instagram's window |
| PyAutoGUI FAILSAFE (exact top-left pixel) | Hard **Stop** (backstop, in case the corner-pause is somehow missed) | Yes |

The corner-flick is the one to actually rely on mid-draw: since the app is controlling your real mouse, you're usually looking at Instagram's window, not this app's — so `Space`/`Esc` won't receive the keypress. Flicking to the corner pauses regardless of which window has focus; click **Resume** in the app once you've got mouse control back to continue, or **Stop Drawing** to end the session.

Both work globally in the window — you don't need to have any particular button focused.

## Requirements

- macOS (uses `pyautogui` mouse automation — the Accessibility permission check is macOS-specific; other platforms should mostly work but aren't tested)
- Python 3.10+ **with Tk 8.6+**

> **Important (macOS):** the system Python at `/usr/bin/python3` bundles Apple's deprecated Tk 8.5, which renders CustomTkinter windows as a blank gray screen. Use a Python build with a modern Tk instead — see Setup below.

## Setup

```bash
# 1. Install a Python build with modern Tk support (Homebrew example):
brew install python-tk@3.13

# 2. Create a virtual environment with that Python:
python3.13 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies:
pip install -r requirements.txt

# 4. Run it:
python app.py
```

On macOS, grant Accessibility permission the first time you calibrate or draw: **System Settings > Privacy & Security > Accessibility**, enable Terminal (or whichever app launched Python). Without this, PyAutoGUI silently can't move the mouse.

## Usage

1. **Choose Image** — pick a jpg/jpeg/png/bmp file.
2. **Calibrate Canvas** — click, then move your mouse to the top-left corner of your drawing canvas within the countdown, then the bottom-right corner. Do this once per canvas position.
3. Tune **Drawing Settings** — watch **Preview Edges** / **Preview Contours** update live as you adjust sliders.
4. **Start Drawing** — a short countdown gives you time to get your hands clear, then the app takes over the mouse and draws.
5. **Stop Drawing** (button or `Esc`) or **Pause/Resume** (button or `Space`) at any time.

Settings and calibration are saved automatically and restored the next time you launch the app.

## Project structure

```
app.py              CustomTkinter GUI — wires everything together
drawing.py           Background-thread drawing loop, injected mouse driver
image_processing.py  Image load / resize / edge-detect / contour-extract pipeline
calibration.py       Canvas calibration flow + macOS Accessibility check
config.py            Settings + calibration persistence (config.json)
utils.py             Shared coordinate math
tests/               pytest unit tests for the non-GUI modules
requirements.txt
TODOS.md             Deferred features (zoom, drag-and-drop, ETA, multi-monitor)
```

## Running tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Known limitations

See [TODOS.md](TODOS.md) for deferred features and their rationale (zoomable preview, drag-and-drop image loading, estimated drawing time, multi-monitor/mixed-DPI calibration).
