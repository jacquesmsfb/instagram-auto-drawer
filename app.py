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
import threading
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk
import cv2
from PIL import Image

import calibration
import image_processing
from config import ConfigManager
from drawing import DrawingThread

PREVIEW_MAX_W, PREVIEW_MAX_H = 320, 320
THUMB_SIZE = 96
DEBOUNCE_MS = 180

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Instagram Auto Drawer")
        self.geometry("760x780")

        self.config_manager = ConfigManager(log=self._log_safe)

        self.current_image = None  # loaded, full-res BGR ndarray
        self.current_image_path: Optional[str] = None
        self.preview_mode = "thumbnail"  # "thumbnail" | "edges" | "contours"

        self.drawing_thread: Optional[DrawingThread] = None
        self.drawing_queue: "queue.Queue" = queue.Queue()
        self.calibration_thread: Optional[threading.Thread] = None
        self.calibration_queue: "queue.Queue" = queue.Queue()

        self._debounce_id: Optional[str] = None

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

    def _build_console_section(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=3, column=0, padx=12, pady=(6, 12), sticky="nsew")
        self.grid_rowconfigure(3, weight=1)

        self.progress_bar = ctk.CTkProgressBar(frame)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=10, pady=(10, 6))

        self.console = ctk.CTkTextbox(frame, height=180, font=ctk.CTkFont(family="Menlo", size=12))
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
        )

    def show_edges_preview(self) -> None:
        result = self._run_preview_pipeline()
        if result is None:
            return
        self.preview_mode = "edges"
        edges_rgb = cv2.cvtColor(result.edges, cv2.COLOR_GRAY2RGB)
        self._render_preview_image(edges_rgb)

    def show_contours_preview(self) -> None:
        result = self._run_preview_pipeline()
        if result is None:
            return
        self.preview_mode = "contours"
        canvas = cv2.cvtColor(result.edges, cv2.COLOR_GRAY2RGB)
        canvas[:] = 0
        cv2.drawContours(canvas, result.contours, -1, (46, 168, 79), 1)
        self.log(f"Found {result.total_found} contours")
        if result.skipped:
            self.log(f"Skipping {result.skipped} small contours")
        self._render_preview_image(canvas)

    def _render_preview_image(self, rgb_array) -> None:
        pil_img = Image.fromarray(rgb_array)
        pil_img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
        self.thumbnail_label.configure(image=ctk_img, text="")
        self.thumbnail_label.image = ctk_img

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

        self.drawing_queue = queue.Queue()
        self.drawing_thread = DrawingThread(
            result.contours,
            top_left,
            (result.offset_x, result.offset_y),
            float(self.config_manager.get("mouse_speed")),
            self.drawing_queue,
            draw_delay=int(self.config_manager.get("draw_delay")),
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
