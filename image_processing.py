"""Image loading and the edge/contour extraction pipeline.

Pipeline: load_image -> resize_to_fit -> detect_edges -> extract_contours.
Every function takes explicit parameters (no hidden globals) so the GUI's
settings sliders map directly onto function arguments, and so the pipeline
can be exercised in tests without a config object or a GUI.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np


class ImageLoadError(Exception):
    """Raised when an image file can't be decoded by OpenCV."""


def load_image(path: str) -> np.ndarray:
    """Load an image file. Raises ImageLoadError with a clear message on
    failure instead of letting a None propagate into later OpenCV calls,
    which fail with a cryptic C++-level error."""
    img = cv2.imread(path)
    if img is None:
        raise ImageLoadError(f"Could not load image: {path} (corrupted or unsupported format)")
    return img


def resize_to_fit(img: np.ndarray, canvas_w: int, canvas_h: int) -> Tuple[np.ndarray, int, int]:
    """Resize the image to the largest size that fits inside
    (canvas_w, canvas_h) without distorting its aspect ratio.

    Returns (resized_img, offset_x, offset_y) where offset_x/offset_y is
    the centering padding within the canvas — the caller adds this to the
    canvas's top-left when mapping contour points to screen coordinates,
    so the drawing is centered rather than pinned to the canvas's corner.
    """
    img_h, img_w = img.shape[:2]
    scale = min(canvas_w / img_w, canvas_h / img_h)
    new_w = max(1, int(round(img_w * scale)))
    new_h = max(1, int(round(img_h * scale)))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    offset_x = (canvas_w - new_w) // 2
    offset_y = (canvas_h - new_h) // 2
    return resized, offset_x, offset_y


def detect_edges(
    img: np.ndarray,
    canny_threshold_1: int,
    canny_threshold_2: int,
    gaussian_blur: bool,
) -> np.ndarray:
    """Grayscale + optional blur + Canny edge detection + thinning.

    Canny edges have real thickness (1-3px), which means findContours
    would trace BOTH sides of every line as separate, nearly-identical
    overlapping contours — the mouse retraces almost the same path twice,
    doubling draw time for no visual gain. Thinning (Zhang-Suen, via
    cv2.ximgproc) collapses each line to a single-pixel centerline before
    contour extraction, so each stroke gets traced once.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if gaussian_blur:
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, canny_threshold_1, canny_threshold_2)
    return cv2.ximgproc.thinning(edges)


# Below this area-to-perimeter ratio, a contour is treated as a "hairline"
# loop rather than a real enclosed shape — see _dedupe_hairline below.
HAIRLINE_AREA_PERIMETER_RATIO = 1.5


def _dedupe_hairline(contour: np.ndarray) -> np.ndarray:
    """Cut the redundant return trip out of an open-stroke contour.

    cv2.findContours always returns CLOSED loops. For an open stroke (a
    single hand-drawn line, not an enclosed region), that means it walks
    out to the far end of the stroke, then immediately walks BACK along
    almost the same pixels to close the loop — the mouse would retrace
    a line it just drew. A contour like this has near-zero enclosed area
    relative to its perimeter (the forward and backward passes cancel
    out), which distinguishes it from a real closed shape (a filled
    blob's full-perimeter contour has genuine area and should stay whole).

    Real closed shapes are returned unchanged — going around their
    outline once isn't wasted motion, it's the actual shape.
    """
    if len(contour) < 4:
        return contour
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return contour
    area = cv2.contourArea(contour)
    if (area / perimeter) >= HAIRLINE_AREA_PERIMETER_RATIO:
        return contour
    # Keep only the forward pass (start -> the far tip), drop the return trip.
    midpoint = len(contour) // 2 + 1
    return contour[:midpoint]


def extract_contours(
    edges: np.ndarray,
    min_contour_area: float,
    detail: float,
) -> Tuple[List[np.ndarray], int, int]:
    """Find contours, drop tiny ones, simplify with approxPolyDP, dedupe
    hairline there-and-back strokes, and sort largest-first so a
    stopped/partial drawing still reads as a recognizable silhouette
    rather than scattered small marks.

    Returns (kept_contours, total_found, skipped_count). Logging the
    "Found N contours" / "Skipping M small contours" messages is left to
    the caller (app.py) so this function stays a pure, easily-testable
    transform with no side effects.
    """
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    total_found = len(contours)

    kept: List[np.ndarray] = []
    skipped = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_contour_area:
            skipped += 1
            continue
        epsilon = detail * cv2.arcLength(contour, False)
        simplified = cv2.approxPolyDP(contour, epsilon, False)
        if len(simplified) < 2:
            skipped += 1
            continue
        kept.append(_dedupe_hairline(simplified))

    kept.sort(key=cv2.contourArea, reverse=True)
    return kept, total_found, skipped


class PipelineResult:
    """Result of running the full image-processing pipeline once."""

    def __init__(
        self,
        contours: List[np.ndarray],
        offset_x: int,
        offset_y: int,
        edges: np.ndarray,
        total_found: int,
        skipped: int,
    ) -> None:
        self.contours = contours
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.edges = edges
        self.total_found = total_found
        self.skipped = skipped


def process_pipeline(
    img: np.ndarray,
    canvas_w: int,
    canvas_h: int,
    canny_threshold_1: int,
    canny_threshold_2: int,
    gaussian_blur: bool,
    min_contour_area: float,
    detail: float,
) -> PipelineResult:
    """Run the full pipeline: resize -> edges -> contours.

    Edges are returned too so the GUI's Preview Edges button doesn't have
    to recompute them separately from Preview Contours.
    """
    resized, offset_x, offset_y = resize_to_fit(img, canvas_w, canvas_h)
    edges = detect_edges(resized, canny_threshold_1, canny_threshold_2, gaussian_blur)
    contours, total_found, skipped = extract_contours(edges, min_contour_area, detail)
    return PipelineResult(contours, offset_x, offset_y, edges, total_found, skipped)
