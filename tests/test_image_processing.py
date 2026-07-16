import cv2
import numpy as np
import pytest

import image_processing as ip


def make_square_image(size=200, square_size=120, channels=3):
    img = np.zeros((size, size, channels), dtype=np.uint8)
    offset = (size - square_size) // 2
    cv2.rectangle(
        img,
        (offset, offset),
        (offset + square_size, offset + square_size),
        (255, 255, 255),
        thickness=-1,
    )
    return img


def test_load_image_missing_file_raises(tmp_path):
    with pytest.raises(ip.ImageLoadError):
        ip.load_image(str(tmp_path / "does_not_exist.png"))


def test_load_image_valid_returns_ndarray(tmp_path):
    img = make_square_image()
    path = tmp_path / "square.png"
    cv2.imwrite(str(path), img)

    loaded = ip.load_image(str(path))
    assert loaded is not None
    assert loaded.shape[0] > 0 and loaded.shape[1] > 0


def test_resize_to_fit_preserves_aspect_ratio_wide_canvas():
    img = np.zeros((100, 50, 3), dtype=np.uint8)  # tall image (h=100, w=50)
    resized, offset_x, offset_y = ip.resize_to_fit(img, canvas_w=300, canvas_h=100)
    h, w = resized.shape[:2]
    assert w <= 300 and h <= 100
    # aspect ratio preserved: original w/h == 0.5
    assert abs((w / h) - 0.5) < 0.02
    # centered: offsets are non-negative and image fits within canvas
    assert offset_x >= 0 and offset_y >= 0
    assert offset_x + w <= 300
    assert offset_y + h <= 100


def test_resize_to_fit_preserves_aspect_ratio_tall_canvas():
    img = np.zeros((50, 100, 3), dtype=np.uint8)  # wide image (h=50, w=100)
    resized, offset_x, offset_y = ip.resize_to_fit(img, canvas_w=100, canvas_h=300)
    h, w = resized.shape[:2]
    assert abs((w / h) - 2.0) < 0.02
    assert offset_x + w <= 100
    assert offset_y + h <= 300


def test_resize_to_fill_exactly_matches_canvas_dims():
    img = np.zeros((100, 50, 3), dtype=np.uint8)  # tall image (h=100, w=50)
    resized, offset_x, offset_y = ip.resize_to_fill(img, canvas_w=300, canvas_h=100)
    h, w = resized.shape[:2]
    # unlike resize_to_fit, the result exactly fills the canvas - no letterboxing
    assert (w, h) == (300, 100)
    assert (offset_x, offset_y) == (0, 0)


def test_resize_to_fill_crops_instead_of_letterboxing():
    img = np.zeros((50, 100, 3), dtype=np.uint8)  # wide image (h=50, w=100)
    resized, offset_x, offset_y = ip.resize_to_fill(img, canvas_w=100, canvas_h=100)
    h, w = resized.shape[:2]
    assert (w, h) == (100, 100)
    assert (offset_x, offset_y) == (0, 0)


def test_process_pipeline_fill_canvas_uses_resize_to_fill():
    img = make_square_image()
    result = ip.process_pipeline(
        img, canvas_w=150, canvas_h=300,
        canny_threshold_1=80, canny_threshold_2=150, gaussian_blur=True,
        min_contour_area=5, detail=0.003, fill_canvas=True,
    )
    # fill_canvas always fully fills the canvas -> edges match canvas dims exactly
    assert result.edges.shape[:2] == (300, 150)
    assert (result.offset_x, result.offset_y) == (0, 0)


def test_detect_edges_runs_with_and_without_blur():
    img = make_square_image()
    edges_blurred = ip.detect_edges(img, 80, 150, gaussian_blur=True)
    edges_unblurred = ip.detect_edges(img, 80, 150, gaussian_blur=False)
    assert edges_blurred.shape == edges_unblurred.shape == img.shape[:2]


def test_extract_contours_filters_by_min_area():
    img = make_square_image(size=200, square_size=120)
    edges = ip.detect_edges(img, 80, 150, gaussian_blur=True)

    kept_high_area, _total, skipped_high = ip.extract_contours(edges, min_contour_area=100000, detail=0.003)
    kept_low_area, _total2, skipped_low = ip.extract_contours(edges, min_contour_area=1, detail=0.003)

    assert len(kept_high_area) == 0
    assert len(kept_low_area) >= 1
    assert skipped_high >= skipped_low


def test_extract_contours_sorted_largest_first():
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.rectangle(img, (10, 10), (60, 60), (255, 255, 255), -1)   # small square
    cv2.rectangle(img, (100, 100), (190, 190), (255, 255, 255), -1)  # big square
    edges = ip.detect_edges(img, 80, 150, gaussian_blur=False)

    kept, _total, _skipped = ip.extract_contours(edges, min_contour_area=1, detail=0.003)
    assert len(kept) >= 2
    areas = [cv2.contourArea(c) for c in kept]
    assert areas == sorted(areas, reverse=True)


def test_dedupe_hairline_truncates_open_stroke():
    # A straight open line, as a closed contour: cv2 traces it forward then
    # back, so points 0..4 go right and points 5..9 return almost the same way.
    there_and_back = np.array(
        [[[x, 0]] for x in range(10)] + [[[x, 1]] for x in range(9, -1, -1)],
        dtype=np.int32,
    )
    deduped = ip._dedupe_hairline(there_and_back)
    assert len(deduped) < len(there_and_back)


def test_dedupe_hairline_leaves_real_shapes_untouched():
    # A real filled square's outline contour has genuine area relative to
    # its perimeter — must NOT be truncated, or the shape gets clipped.
    img = make_square_image(size=100, square_size=60)
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    square_contour = max(contours, key=cv2.contourArea)
    deduped = ip._dedupe_hairline(square_contour)
    assert len(deduped) == len(square_contour)


def test_extract_contours_dedupe_reduces_total_points_on_line_art():
    img = np.zeros((150, 150, 3), dtype=np.uint8)
    cv2.line(img, (10, 10), (140, 140), (255, 255, 255), 1)  # open diagonal stroke
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 150)

    # min_contour_area=0: a perfectly straight synthetic line has exactly
    # zero enclosed area (forward/backward passes perfectly coincide on
    # integer pixel coords), unlike real photos where noise gives it a
    # sliver of nonzero area. Use 0 here so the area filter doesn't mask
    # what this test is actually checking: hairline dedup.
    kept, _total, _skipped = ip.extract_contours(edges, min_contour_area=0, detail=0.003)
    total_points = sum(len(c) for c in kept)

    # An open diagonal stroke traced there-and-back would be ~2x the direct
    # point count; dedup should bring it down close to a one-way trace.
    assert total_points > 0
    assert total_points < 200  # sanity bound, well under a full there-and-back trace


def test_extract_contours_keeps_straight_line_despite_zero_area():
    # Regression test for the min_contour_area hairline bug: a perfectly
    # straight synthetic line's forward/backward passes coincide exactly
    # on integer pixel coordinates, giving cv2.contourArea == 0 — so the
    # old plain `area < min_contour_area` filter discarded it whenever
    # min_contour_area > 0, no matter how long the line was.
    img = np.zeros((150, 150, 3), dtype=np.uint8)
    cv2.line(img, (10, 10), (140, 140), (255, 255, 255), 1)
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 150)

    raw_contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    line_contour = max(raw_contours, key=len)
    assert cv2.contourArea(line_contour) == 0  # confirms the bug's precondition
    line_perimeter = cv2.arcLength(line_contour, True)

    kept, _total, _skipped = ip.extract_contours(edges, min_contour_area=10, detail=0.003)
    # The long diagonal stroke must survive — matched by arc length, since
    # small Canny/thinning artifacts near the endpoints may also be found
    # and correctly skipped as genuinely tiny, separate from this contour.
    assert any(cv2.arcLength(c, False) > line_perimeter / 4 for c in kept)


def test_extract_contours_still_filters_short_hairline_strokes():
    # A short diagonal nub should still be dropped by a high min_contour_area
    # — the fix filters hairline shapes by length instead of area, it
    # doesn't stop filtering them altogether.
    img = np.zeros((150, 150, 3), dtype=np.uint8)
    cv2.line(img, (10, 10), (15, 15), (255, 255, 255), 1)
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 150)

    kept, _total, skipped = ip.extract_contours(edges, min_contour_area=100, detail=0.003)
    assert len(kept) == 0
    assert skipped >= 1


def test_extract_contours_handles_blank_image():
    blank_edges = np.zeros((100, 100), dtype=np.uint8)
    kept, total_found, skipped = ip.extract_contours(blank_edges, min_contour_area=10, detail=0.003)
    assert kept == []
    assert total_found == 0
    assert skipped == 0


def test_process_pipeline_end_to_end():
    img = make_square_image()
    result = ip.process_pipeline(
        img,
        canvas_w=150,
        canvas_h=150,
        canny_threshold_1=80,
        canny_threshold_2=150,
        gaussian_blur=True,
        min_contour_area=5,
        detail=0.003,
    )
    assert result.total_found >= 1
    assert result.edges.shape[:2] != img.shape[:2]  # resized to fit canvas
