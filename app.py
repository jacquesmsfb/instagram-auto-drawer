"""Instagram Auto Drawer — CustomTkinter GUI.

Wires together config.py, image_processing.py, calibration.py, and
drawing.py. See the layout/interaction-state decisions from
/plan-design-review for why things are arranged and enabled the way
they are — the short version:

  - Image + Calibration share a "setup" row (one-time steps).
  - Settings sliders are a 2-column grid, disabled while drawing/paused.
  - Start Drawing is disabled until calibrated; Stop is bound to Escape
    as well as its button, since it's the emergency-stop control.
  - Calibration and drawing both run on background threads (never the
    Tkinter mainloop) and report back via a thread-safe queue polled
    with `after()`.
"""

from __future__ import annotations

import queue
import sys
import threading
import types
from tkinter import filedialog
from typing import Optional

# PyAutoGUI imports mouseinfo for its interactive MouseInfo debugging tool,
# which this app never uses (calibration.py/drawing.py only call
# moveTo/dragTo/position/mouseDown/mouseUp/size/FAILSAFE). On this machine,
# mouseinfo's rubicon-objc dependency raises "AttributeError: dlsym(...,
# objc_msgSendSuper_stret): symbol not found" at import time specifically
# when launched outside an interactive shell (e.g. double-clicking a .app
# bundle instead of running from Terminal) — and PyAutoGUI's own fallback
# only catches ImportError, not AttributeError, so that takes the whole
# app down before a single window opens. Stub the module out before
# anything imports pyautogui, so that import always succeeds trivially.
if "mouseinfo" not in sys.modules:
    sys.modules["mouseinfo"] = types.ModuleType("mouseinfo")

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

import calibration
import drawing
import image_processing
from config import ConfigManager
from drawing import DrawingThread

PREVIEW_MAX_W, PREVIEW_MAX_H = 320, 320
THUMB_SIZE = 96
DEBOUNCE_MS = 180

# Zoomable detail-preview panel (Preview Edges / Preview Contours output).
# Separate from THUMB_SIZE, which is just the small "which file did I pick"
# thumbnail in the Image section. Kept modest — this app runs on laptop
# screens where vertical space is tight (see the geometry check in
# __init__): zoom does the heavy lifting for checking detail, not raw
# on-screen size.
PREVIEW_PANEL_SIZE = 120
PREVIEW_MIN_ZOOM = 1.0
PREVIEW_MAX_ZOOM = 6.0
PREVIEW_ZOOM_STEP = 1.25

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Instagram Auto Drawer")
        self.geometry("810x820")

        self.config_manager = ConfigManager(log=self._log_safe)

        self.current_image = None  # loaded, full-res BGR ndarray
        self.current_image_path: Optional[str] = None
        self.preview_mode = "thumbnail"  # "thumbnail" | "edges" | "contours"

        self.drawing_thread: Optional[DrawingThread] = None
        self.drawing_queue: "queue.Queue" = queue.Queue()
        self.calibration_thread: Optional[threading.Thread] = None
        self.calibration_queue: "queue.Queue" = queue.Queue()

        self._debounce_id: Optional[str] = None

        # Zoomable detail-preview state — see _render_zoomed_preview.
        self._preview_source: Optional[np.ndarray] = None
        self._preview_zoom = PREVIEW_MIN_ZOOM
        self._preview_pan = (0.5, 0.5)  # fractional center within the source image
        self._preview_drag_start: Optional[tuple] = None
        self._preview_pan_start: Optional[tuple] = None

        self._build_ui()
        self._restore_from_config()
        self._refresh_button_states()

        # Global keybinds — work regardless of which widget has focus, since
        # the whole point is a fast way out while the mouse is being
        # automated. Escape = force stop (emergency stop, see Design Pass 6).
        # Space = toggle pause/resume (mirrors the media-player convention).
        self.bind("<Escape>", lambda _event: self.stop_drawing())
        self.bind("<space>", lambda _event: self.toggle_pause())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        setup_row = ctk.CTkFrame(self, fg_color="transparent")
        setup_row.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        setup_row.grid_columnconfigure((0, 1), weight=1)
        self._build_image_section(setup_row)
        self._build_calibration_section(setup_row)

        self._build_settings_section()
        self._build_button_row()
        self._build_preview_section()
        self._build_console_section()

    def _build_image_section(self, parent) -> None:
        frame = ctk.CTkFrame(parent)
        frame.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        ctk.CTkLabel(frame, text="Image", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 0)
        )

        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="x", padx=10, pady=8)

        self.thumbnail_label = ctk.CTkLabel(body, text="No image", width=THUMB_SIZE, height=THUMB_SIZE)
        self.thumbnail_label.pack(side="left")

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", padx=10, fill="x", expand=True)
        ctk.CTkButton(right, text="Choose Image", command=self.choose_image).pack(anchor="w")
        self.filename_label = ctk.CTkLabel(right, text="No file selected", wraplength=160)
        self.filename_label.pack(anchor="w", pady=(6, 0))

    def _build_calibration_section(self, parent) -> None:
        frame = ctk.CTkFrame(parent)
        frame.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        ctk.CTkLabel(frame, text="Canvas Calibration", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 0)
        )
        ctk.CTkButton(frame, text="Calibrate Canvas", command=self.start_calibration).pack(
            anchor="w", padx=10, pady=8
        )
        self.calibration_label = ctk.CTkLabel(
            frame, text="Top Left: —\nBottom Right: —\nWidth: —   Height: —", justify="left"
        )
        self.calibration_label.pack(anchor="w", padx=10, pady=(0, 10))

    def _build_settings_section(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, padx=12, pady=6, sticky="ew")
        ctk.CTkLabel(frame, text="Drawing Settings", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4)
        )

        self.sliders = {}
        specs = [
            ("detail", "Detail", 0.001, 0.03, 0.0001),
            ("canny_threshold_1", "Canny Threshold 1", 0, 255, 1),
            ("min_contour_area", "Min Contour Area", 0, 500, 1),
            ("canny_threshold_2", "Canny Threshold 2", 0, 255, 1),
            ("draw_delay", "Draw Delay (s)", 0, 15, 1),
            ("mouse_speed", "Mouse Speed", 0.0005, 0.02, 0.0005),
        ]
        for i, (key, label, lo, hi, _step) in enumerate(specs):
            row, col = 1 + (i // 2), (i % 2) * 2
            cell = ctk.CTkFrame(frame, fg_color="transparent")
            cell.grid(row=row, column=col, columnspan=2, sticky="ew", padx=10, pady=6)
            cell.grid_columnconfigure(0, weight=1)

            value_label = ctk.CTkLabel(cell, text=f"{label}: {self._fmt(key, lo)}")
            value_label.grid(row=0, column=0, sticky="w")
            slider = ctk.CTkSlider(
                cell,
                from_=lo,
                to=hi,
                command=lambda v, k=key, lbl=value_label, name=label: self._on_slider_change(k, v, lbl, name),
            )
            slider.grid(row=1, column=0, sticky="ew")
            self.sliders[key] = (slider, value_label, label)

        self.blur_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            frame,
            text="Gaussian Blur",
            variable=self.blur_var,
            command=lambda: self._on_setting_changed("gaussian_blur", self.blur_var.get()),
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10))

        self.fill_canvas_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            frame,
            text="Fill Canvas (crop to fit)",
            variable=self.fill_canvas_var,
            command=lambda: self._on_setting_changed("fill_canvas", self.fill_canvas_var.get()),
        ).grid(row=4, column=2, columnspan=2, sticky="w", padx=10, pady=(0, 10))

    def _build_button_row(self) -> None:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=2, column=0, padx=12, pady=6, sticky="ew")

        self.preview_edges_btn = ctk.CTkButton(frame, text="Preview Edges", command=self.show_edges_preview)
        self.preview_edges_btn.pack(side="left", padx=(0, 6))
        self.preview_contours_btn = ctk.CTkButton(
            frame, text="Preview Contours", command=self.show_contours_preview
        )
        self.preview_contours_btn.pack(side="left", padx=6)
        self.pause_btn = ctk.CTkButton(frame, text="Pause", command=self.toggle_pause, width=80)
        self.pause_btn.pack(side="left", padx=6)
        self.reset_btn = ctk.CTkButton(frame, text="Reset", command=self.reset_settings, width=80)
        self.reset_btn.pack(side="left", padx=6)

        self.start_btn = ctk.CTkButton(
            frame, text="Start Drawing", fg_color="#2fa84f", hover_color="#268a41", command=self.start_drawing
        )
        self.start_btn.pack(side="right", padx=(6, 0))
        self.stop_btn = ctk.CTkButton(
            frame, text="Stop Drawing", fg_color="#c0392b", hover_color="#a5301f", command=self.stop_drawing
        )
        self.stop_btn.pack(side="right", padx=6)

    def _build_preview_section(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=3, column=0, padx=12, pady=4, sticky="ew")

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(header, text="Preview", font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkLabel(
            header,
            text="  ·  scroll to zoom, drag to pan, double-click to reset",
            text_color="gray60",
            font=ctk.CTkFont(size=11),
        ).pack(side="left")

        self.detail_preview_label = ctk.CTkLabel(
            frame,
            text="Click Preview Edges / Preview Contours to see detail here",
            width=PREVIEW_PANEL_SIZE,
            height=PREVIEW_PANEL_SIZE,
        )
        self.detail_preview_label.pack(padx=10, pady=(6, 8))

        # Bound at the application level (bind_all), not on the label/canvas
        # sub-widgets CTkLabel composes internally — those didn't reliably
        # receive real trackpad/mouse events in practice. bind_all catches
        # the event regardless of which internal widget the OS thinks the
        # cursor is over; each handler below gates on whether the cursor
        # was actually within the preview panel's screen rectangle, so it
        # doesn't interfere with scrolling/clicking elsewhere in the window.
        self.bind_all("<MouseWheel>", self._on_preview_scroll)
        self.bind_all("<ButtonPress-1>", self._on_preview_drag_start, add="+")
        self.bind_all("<B1-Motion>", self._on_preview_drag_move, add="+")
        self.bind_all("<Double-Button-1>", self._on_preview_reset_zoom, add="+")

    def _point_in_preview_panel(self, x_root: int, y_root: int) -> bool:
        lbl = self.detail_preview_label
        x0, y0 = lbl.winfo_rootx(), lbl.winfo_rooty()
        return x0 <= x_root <= x0 + lbl.winfo_width() and y0 <= y_root <= y0 + lbl.winfo_height()

    def _build_console_section(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=4, column=0, padx=12, pady=(6, 12), sticky="nsew")
        self.grid_rowconfigure(4, weight=1)

        self.progress_bar = ctk.CTkProgressBar(frame)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=10, pady=(10, 6))

        self.console = ctk.CTkTextbox(frame, height=110, font=ctk.CTkFont(family="Menlo", size=12))
        self.console.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.console.configure(state="disabled")

    # ------------------------------------------------------------ helpers

    def _fmt(self, key: str, value: float) -> str:
        if key in ("detail", "mouse_speed"):
            return f"{value:.4f}"
        return f"{int(value)}"

    def log(self, message: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", message + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _log_safe(self, message: str) -> None:
        # Used by ConfigManager during __init__, before the console widget exists.
        if hasattr(self, "console"):
            self.log(message)

    # --------------------------------------------------------- image load

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp")],
        )
        if not path:
            return
        try:
            img = image_processing.load_image(path)
        except image_processing.ImageLoadError as exc:
            self.log(f"Error: {exc}")
            return

        self.current_image = img
        self.current_image_path = path
        self.config_manager.set("last_image_path", path)
        self.filename_label.configure(text=path.split("/")[-1])
        self.preview_mode = "thumbnail"
        self._show_thumbnail(img)
        self.log("Loaded image")
        self._refresh_button_states()

    def _show_thumbnail(self, img) -> None:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        pil_img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
        self.thumbnail_label.configure(image=ctk_img, text="")
        self.thumbnail_label.image = ctk_img

    # -------------------------------------------------------- calibration

    def start_calibration(self) -> None:
        if not calibration.check_mouse_control(log=self.log):
            return
        self.calibration_label.configure(text="Calibrating... move your mouse when prompted.")

        def worker() -> None:
            try:
                top_left, bottom_right = calibration.calibrate(
                    countdown_seconds=5, log=lambda msg: self.calibration_queue.put(("log", msg))
                )
                self.calibration_queue.put(("done", top_left, bottom_right))
            except calibration.CalibrationError as exc:
                self.calibration_queue.put(("error", str(exc)))

        self.calibration_thread = threading.Thread(target=worker, daemon=True)
        self.calibration_thread.start()
        self._poll_calibration_queue()

    def _poll_calibration_queue(self) -> None:
        try:
            while True:
                item = self.calibration_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log(item[1])
                elif kind == "done":
                    _, top_left, bottom_right = item
                    self.config_manager.set_calibration(top_left, bottom_right)
                    self._update_calibration_label()
                    self._refresh_button_states()
                elif kind == "error":
                    self.log(f"Error: {item[1]}")
        except queue.Empty:
            pass

        if self.calibration_thread and self.calibration_thread.is_alive():
            self.after(50, self._poll_calibration_queue)

    def _update_calibration_label(self) -> None:
        cal = self.config_manager.calibration
        if not cal:
            self.calibration_label.configure(text="Top Left: —\nBottom Right: —\nWidth: —   Height: —")
            return
        top_left, bottom_right = cal
        width = bottom_right[0] - top_left[0]
        height = bottom_right[1] - top_left[1]
        self.calibration_label.configure(
            text=f"Top Left: {top_left}\nBottom Right: {bottom_right}\nWidth: {width}   Height: {height}"
        )

    # ------------------------------------------------------------ preview

    def _on_slider_change(self, key: str, value: float, label_widget, label_text: str) -> None:
        label_widget.configure(text=f"{label_text}: {self._fmt(key, value)}")
        self._on_setting_changed(key, value)

    def _on_setting_changed(self, key: str, value) -> None:
        self.config_manager.set(key, value)
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(DEBOUNCE_MS, self._refresh_current_preview)

    def _refresh_current_preview(self) -> None:
        if self.preview_mode == "edges":
            self.show_edges_preview()
        elif self.preview_mode == "contours":
            self.show_contours_preview()

    def _run_preview_pipeline(self):
        if self.current_image is None:
            return None
        return image_processing.process_pipeline(
            self.current_image,
            PREVIEW_MAX_W,
            PREVIEW_MAX_H,
            int(self.config_manager.get("canny_threshold_1")),
            int(self.config_manager.get("canny_threshold_2")),
            bool(self.config_manager.get("gaussian_blur")),
            float(self.config_manager.get("min_contour_area")),
            float(self.config_manager.get("detail")),
            bool(self.config_manager.get("fill_canvas")),
        )

    def show_edges_preview(self) -> None:
        result = self._run_preview_pipeline()
        if result is None:
            return
        is_new_mode = self.preview_mode != "edges"
        self.preview_mode = "edges"
        edges_rgb = cv2.cvtColor(result.edges, cv2.COLOR_GRAY2RGB)
        self._set_preview_source(edges_rgb, reset_view=is_new_mode)

    def show_contours_preview(self) -> None:
        result = self._run_preview_pipeline()
        if result is None:
            return
        is_new_mode = self.preview_mode != "contours"
        self.preview_mode = "contours"
        canvas = cv2.cvtColor(result.edges, cv2.COLOR_GRAY2RGB)
        canvas[:] = 0
        cv2.drawContours(canvas, result.contours, -1, (46, 168, 79), 1)
        self.log(f"Found {result.total_found} contours")
        if result.skipped:
            self.log(f"Skipping {result.skipped} small contours")
        self._set_preview_source(canvas, reset_view=is_new_mode)

    # ------------------------------------------------------ zoomable preview

    def _set_preview_source(self, rgb_array: np.ndarray, reset_view: bool = True) -> None:
        """Swap in a new source image for the detail preview panel.

        reset_view=False keeps the current zoom/pan across a live slider
        refresh of the *same* preview mode — so tuning a threshold slider
        while zoomed into a busy region doesn't snap back out to fully
        zoomed-out on every tick. A genuine mode switch (edges<->contours,
        or a newly chosen image) starts fresh instead.
        """
        self._preview_source = rgb_array
        if reset_view:
            self._preview_zoom = PREVIEW_MIN_ZOOM
            self._preview_pan = (0.5, 0.5)
        self._render_zoomed_preview()

    def _render_zoomed_preview(self) -> None:
        if self._preview_source is None:
            return
        src_h, src_w = self._preview_source.shape[:2]
        crop_w = max(1, min(src_w, int(round(src_w / self._preview_zoom))))
        crop_h = max(1, min(src_h, int(round(src_h / self._preview_zoom))))
        center_x = self._preview_pan[0] * src_w
        center_y = self._preview_pan[1] * src_h
        x0 = int(round(center_x - crop_w / 2))
        y0 = int(round(center_y - crop_h / 2))
        x0 = max(0, min(x0, src_w - crop_w))
        y0 = max(0, min(y0, src_h - crop_h))
        cropped = self._preview_source[y0 : y0 + crop_h, x0 : x0 + crop_w]

        pil_img = Image.fromarray(cropped)
        # NEAREST once zoomed in, so users see actual pixels/contour
        # segments instead of a blurred resample when checking fine detail.
        resample = Image.NEAREST if self._preview_zoom > 1 else Image.LANCZOS
        pil_img = pil_img.resize((PREVIEW_PANEL_SIZE, PREVIEW_PANEL_SIZE), resample)
        ctk_img = ctk.CTkImage(
            light_image=pil_img, dark_image=pil_img, size=(PREVIEW_PANEL_SIZE, PREVIEW_PANEL_SIZE)
        )
        self.detail_preview_label.configure(image=ctk_img, text="")
        self.detail_preview_label.image = ctk_img

    def _on_preview_scroll(self, event) -> None:
        if self._preview_source is None:
            return
        if not self._point_in_preview_panel(event.x_root, event.y_root):
            return
        direction = 1 if event.delta > 0 else -1
        new_zoom = self._preview_zoom * (PREVIEW_ZOOM_STEP**direction)
        self._preview_zoom = max(PREVIEW_MIN_ZOOM, min(PREVIEW_MAX_ZOOM, new_zoom))
        self._render_zoomed_preview()

    def _on_preview_drag_start(self, event) -> None:
        # Gated here (drag START only) — once a drag is under way, motion
        # keeps tracking even if the cursor briefly overshoots the panel's
        # edge, same as any normal drag interaction.
        if not self._point_in_preview_panel(event.x_root, event.y_root):
            self._preview_drag_start = None
            return
        self._preview_drag_start = (event.x_root, event.y_root)
        self._preview_pan_start = self._preview_pan

    def _on_preview_drag_move(self, event) -> None:
        if self._preview_source is None or self._preview_drag_start is None:
            return
        dx = event.x_root - self._preview_drag_start[0]
        dy = event.y_root - self._preview_drag_start[1]
        # Dragging the content right should reveal what's to its left, so
        # the pan center moves opposite the drag direction.
        pan_dx = -dx / PREVIEW_PANEL_SIZE / self._preview_zoom
        pan_dy = -dy / PREVIEW_PANEL_SIZE / self._preview_zoom
        start_x, start_y = self._preview_pan_start
        self._preview_pan = (
            max(0.0, min(1.0, start_x + pan_dx)),
            max(0.0, min(1.0, start_y + pan_dy)),
        )
        self._render_zoomed_preview()

    def _on_preview_reset_zoom(self, event=None) -> None:
        if event is not None and not self._point_in_preview_panel(event.x_root, event.y_root):
            return
        self._preview_zoom = PREVIEW_MIN_ZOOM
        self._preview_pan = (0.5, 0.5)
        self._render_zoomed_preview()

    # ------------------------------------------------------------ drawing

    def start_drawing(self) -> None:
        if self.current_image is None or not self.config_manager.is_calibrated():
            return
        if not calibration.check_mouse_control(log=self.log):
            return

        top_left, _bottom_right = self.config_manager.calibration
        canvas_w, canvas_h = self._canvas_dims()

        result = image_processing.process_pipeline(
            self.current_image,
            canvas_w,
            canvas_h,
            int(self.config_manager.get("canny_threshold_1")),
            int(self.config_manager.get("canny_threshold_2")),
            bool(self.config_manager.get("gaussian_blur")),
            float(self.config_manager.get("min_contour_area")),
            float(self.config_manager.get("detail")),
        )
        self.log(f"Found {result.total_found} contours")
        if result.skipped:
            self.log(f"Skipping {result.skipped} small contours")
        mouse_speed = float(self.config_manager.get("mouse_speed"))
        draw_delay = int(self.config_manager.get("draw_delay"))
        estimate_s = drawing.estimate_drawing_seconds(result.contours, mouse_speed, draw_delay)
        self.log(f"Estimated drawing time: {drawing.format_duration(estimate_s)}")

        self.drawing_queue = queue.Queue()
        self.drawing_thread = DrawingThread(
            result.contours,
            top_left,
            (result.offset_x, result.offset_y),
            mouse_speed,
            self.drawing_queue,
            draw_delay=draw_delay,
        )
        self._total_contours = len(result.contours)
        self.progress_bar.set(0)
        self.drawing_thread.start()
        self._refresh_button_states(drawing=True)
        self._poll_drawing_queue()

    def _canvas_dims(self):
        top_left, bottom_right = self.config_manager.calibration
        return bottom_right[0] - top_left[0], bottom_right[1] - top_left[1]

    def _poll_drawing_queue(self) -> None:
        try:
            while True:
                kind, *rest = self.drawing_queue.get_nowait()
                if kind == "log":
                    self.log(rest[0])
                elif kind == "progress":
                    current, total = rest
                    self.progress_bar.set(current / total if total else 0)
        except queue.Empty:
            pass

        if self.drawing_thread and self.drawing_thread.is_alive():
            # Pause can be triggered from the worker thread itself (corner-flick
            # gesture — see drawing.py's check_corner_pause), not just by
            # clicking Pause/Resume here, so sync the button label from the
            # thread's actual pause_event on every poll tick rather than only
            # updating it in toggle_pause().
            is_paused = self.drawing_thread.pause_event.is_set()
            self.pause_btn.configure(text="Resume" if is_paused else "Pause")
            self.after(50, self._poll_drawing_queue)
        else:
            self._refresh_button_states(drawing=False)

    def stop_drawing(self) -> None:
        if self.drawing_thread and self.drawing_thread.is_alive():
            self.drawing_thread.stop_event.set()

    def toggle_pause(self) -> None:
        if not (self.drawing_thread and self.drawing_thread.is_alive()):
            return
        if self.drawing_thread.pause_event.is_set():
            self.drawing_thread.pause_event.clear()
            self.pause_btn.configure(text="Pause")
        else:
            self.drawing_thread.pause_event.set()
            self.pause_btn.configure(text="Resume")

    def reset_settings(self) -> None:
        """Reset the drawing-settings sliders/checkbox to defaults.
        Does not clear the chosen image or canvas calibration — those are
        session setup, not "settings" in the sense this button addresses."""
        from config import DEFAULTS

        for key, (slider, label_widget, label_text) in self.sliders.items():
            default = DEFAULTS[key]
            slider.set(default)
            label_widget.configure(text=f"{label_text}: {self._fmt(key, default)}")
            self.config_manager.set(key, default, save=False)
        self.blur_var.set(DEFAULTS["gaussian_blur"])
        self.config_manager.set("gaussian_blur", DEFAULTS["gaussian_blur"])
        self._refresh_current_preview()

    # -------------------------------------------------------------- state

    def _refresh_button_states(self, drawing: Optional[bool] = None) -> None:
        is_drawing = drawing if drawing is not None else bool(
            self.drawing_thread and self.drawing_thread.is_alive()
        )
        has_image = self.current_image is not None
        is_calibrated = self.config_manager.is_calibrated()

        def state(enabled: bool) -> str:
            return "normal" if enabled else "disabled"

        self.thumbnail_label  # no-op, keeps linter quiet about unused import path
        for widget in (
            self.preview_edges_btn,
            self.preview_contours_btn,
        ):
            widget.configure(state=state(has_image and not is_drawing))

        self.start_btn.configure(state=state(has_image and is_calibrated and not is_drawing))
        self.stop_btn.configure(state=state(is_drawing))
        self.pause_btn.configure(state=state(is_drawing))
        for slider, _label, _name in self.sliders.values():
            slider.configure(state=state(not is_drawing))

    # -------------------------------------------------------- persistence

    def _restore_from_config(self) -> None:
        for key, (slider, label_widget, label_text) in self.sliders.items():
            value = self.config_manager.get(key)
            slider.set(value)
            label_widget.configure(text=f"{label_text}: {self._fmt(key, value)}")
        self.blur_var.set(bool(self.config_manager.get("gaussian_blur")))
        self._update_calibration_label()

        last_path = self.config_manager.get("last_image_path")
        if last_path:
            try:
                img = image_processing.load_image(last_path)
                self.current_image = img
                self.current_image_path = last_path
                self.filename_label.configure(text=last_path.split("/")[-1])
                self._show_thumbnail(img)
            except image_processing.ImageLoadError:
                pass  # previous image no longer available — start with none selected

    def _on_close(self) -> None:
        self.stop_drawing()
        self.config_manager.save()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
