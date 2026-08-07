"""Find a DingXiang slider-CAPTCHA slot and drag its handle.

Detection uses OpenCV's native morphology and normalized template matching.
The piece's inner alpha contour qualifies shape matches; brightness only ranks
the qualified matches because the genuine slot is dimmer than its decoys.
"""

from pathlib import Path

import cv2
import numpy as np

BG_NATURAL_W = 400  # canvas natural size (400x200)
PIECE_IMG_NATURAL = 68  # sub-slider img natural size (square)

ALPHA_THRESHOLD = 10
GLOW_RADIUS_RATIO = 0.055
MIN_GLOW_RADIUS = 2
INTERIOR_EROSION_RADIUS = 2
MIN_TEMPLATE_PIXELS = 20
MIN_MATCH_SCORE = 0.25
RELATIVE_MATCH_SCORE = 0.60
MIN_CONTOUR_SIZE_RATIO = 0.45
MAX_CONTOUR_SIZE_RATIO = 1.50
MIN_CONTOUR_AREA_RATIO = 0.10
MAX_SHAPE_DISTANCE = 0.15
RELATIVE_SHAPE_DISTANCE = 3.0


class GapNotFoundError(RuntimeError):
    pass


def _read_image(path: Path, flags: int) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(path)
    image = cv2.imread(str(path), flags)
    if image is None:
        raise GapNotFoundError(f"could not decode image: {path}")
    return image


def _alpha_channel(piece: np.ndarray) -> np.ndarray:
    if piece.ndim == 3 and piece.shape[2] >= 4:
        return piece[:, :, 3]
    return np.full(piece.shape[:2], 255, dtype=np.uint8)


def _opaque_bbox(alpha: np.ndarray, piece_path: Path) -> tuple[int, int, int, int]:
    points = cv2.findNonZero((alpha > ALPHA_THRESHOLD).astype(np.uint8))
    if points is None:
        raise GapNotFoundError(f"no opaque pixels in {piece_path}")
    x, y, width, height = cv2.boundingRect(points)
    return x, y, x + width - 1, y + height - 1


def piece_geometry(piece_path: Path) -> tuple[int, int, int, int]:
    """Return the inclusive opaque bbox inside the downloaded piece image."""
    piece_path = Path(piece_path)
    piece = _read_image(piece_path, cv2.IMREAD_UNCHANGED)
    return _opaque_bbox(_alpha_channel(piece), piece_path)


def _ellipse_kernel(radius: int) -> np.ndarray:
    size = radius * 2 + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _build_slot_templates(
    piece_path: Path, width: int, height: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build float32 inner-contour and interior templates for matching."""
    piece = _read_image(piece_path, cv2.IMREAD_UNCHANGED)
    alpha = _alpha_channel(piece)
    x0, y0, x1, y1 = _opaque_bbox(alpha, piece_path)
    cropped = (alpha[y0 : y1 + 1, x0 : x1 + 1] > ALPHA_THRESHOLD).astype(
        np.uint8
    )
    mask = cv2.resize(cropped, (width, height), interpolation=cv2.INTER_NEAREST)

    glow_radius = max(
        MIN_GLOW_RADIUS, int(round(min(width, height) * GLOW_RADIUS_RATIO))
    )
    slot_mask = cv2.erode(mask, _ellipse_kernel(glow_radius))
    outline = cv2.morphologyEx(slot_mask, cv2.MORPH_GRADIENT, _ellipse_kernel(1))
    interior = cv2.erode(slot_mask, _ellipse_kernel(INTERIOR_EROSION_RADIUS))

    if cv2.countNonZero(outline) < MIN_TEMPLATE_PIXELS:
        raise GapNotFoundError("slider piece has no usable inner contour")
    if cv2.countNonZero(interior) < MIN_TEMPLATE_PIXELS:
        raise GapNotFoundError("slider piece has no usable interior")
    contours, _ = cv2.findContours(
        (slot_mask * 255).astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        raise GapNotFoundError("slider piece has no usable contour")
    contour = max(contours, key=cv2.contourArea)
    return outline.astype(np.float32), interior.astype(np.float32), contour


def _edge_magnitude(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy)
    maximum = float(magnitude.max())
    if maximum <= 0:
        raise GapNotFoundError("background has no edges to match")
    return magnitude / maximum


def _candidate_peaks(
    score_map: np.ndarray, threshold: float, width: int, height: int
) -> list[tuple[float, int, int]]:
    """Return one local maximum per spatially separated matching object."""
    nms_width = max(3, width // 2) | 1
    nms_height = max(3, height // 2) | 1
    neighborhood = np.ones((nms_height, nms_width), dtype=np.uint8)
    local_max = cv2.dilate(score_map, neighborhood)
    peak_mask = (
        (score_map >= threshold) & (score_map >= local_max - 1e-6)
    ).astype(np.uint8)

    component_count, labels = cv2.connectedComponents(peak_mask)
    candidates: list[tuple[float, int, int]] = []
    for label in range(1, component_count):
        ys, xs = np.nonzero(labels == label)
        if not len(xs):
            continue
        values = score_map[ys, xs]
        index = int(values.argmax())
        candidates.append((float(values[index]), int(xs[index]), int(ys[index])))
    return candidates


def _background_shape_records(
    gray: np.ndarray, template_contour: np.ndarray, width: int, height: int
) -> list[tuple[float, float, float, float]]:
    """Return shape distance, center, and interior mean for each contour."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    lower = int(max(0, median * 0.66))
    upper = int(min(255, max(lower + 1, median * 1.33)))
    edges = cv2.Canny(blurred, lower, upper)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, _ellipse_kernel(1))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    minimum_area = width * height * MIN_CONTOUR_AREA_RATIO
    records: list[tuple[float, float, float, float]] = []
    for contour in contours:
        x, y, contour_width, contour_height = cv2.boundingRect(contour)
        if not (
            width * MIN_CONTOUR_SIZE_RATIO
            <= contour_width
            <= width * MAX_CONTOUR_SIZE_RATIO
            and height * MIN_CONTOUR_SIZE_RATIO
            <= contour_height
            <= height * MAX_CONTOUR_SIZE_RATIO
            and cv2.contourArea(contour) >= minimum_area
        ):
            continue
        distance = float(
            cv2.matchShapes(
                template_contour, contour, cv2.CONTOURS_MATCH_I1, 0.0
            )
        )
        contour_mask = np.zeros_like(gray)
        cv2.drawContours(contour_mask, [contour], -1, 255, thickness=cv2.FILLED)
        brightness = float(cv2.mean(gray, mask=contour_mask)[0])
        records.append(
            (
                distance,
                x + contour_width / 2,
                y + contour_height / 2,
                brightness,
            )
        )
    return records


def _shape_qualified_candidates(
    candidates: list[tuple[float, int, int]],
    records: list[tuple[float, float, float, float]],
    width: int,
    height: int,
) -> list[tuple[float, float, int, int]]:
    """Reject edge-template peaks that contain a differently shaped object."""
    # Several nearby template peaks can overlap one physical object. Associate
    # every peak with its best matching contour, then keep only the strongest
    # localization for each contour before applying the brightness tie-break.
    matched_by_record: dict[int, tuple[float, float, int, int, float]] = {}
    for score, x, y in candidates:
        matches = [
            (distance, index, brightness)
            for index, (distance, center_x, center_y, brightness) in enumerate(records)
            if x <= center_x <= x + width and y <= center_y <= y + height
        ]
        if not matches:
            continue
        distance, record_index, brightness = min(matches)
        previous = matched_by_record.get(record_index)
        if previous is None or score > previous[1]:
            matched_by_record[record_index] = (
                distance,
                score,
                x,
                y,
                brightness,
            )
    matched = list(matched_by_record.values())
    if not matched:
        raise GapNotFoundError("no closed background contour matches the piece")

    best_distance = min(item[0] for item in matched)
    distance_limit = max(
        MAX_SHAPE_DISTANCE, best_distance * RELATIVE_SHAPE_DISTANCE
    )
    return [
        (brightness, score, x, y)
        for distance, score, x, y, brightness in matched
        if distance <= distance_limit
    ]


def find_gap_left(
    bg_path: Path, piece_path: Path, expected_width: float, expected_height: float
) -> int:
    """Locate the slot's outer piece-alignment x-coordinate in canvas pixels."""
    bg_path = Path(bg_path)
    piece_path = Path(piece_path)
    background = _read_image(bg_path, cv2.IMREAD_GRAYSCALE)
    height, width = background.shape
    template_width = max(1, int(round(expected_width)))
    template_height = max(1, int(round(expected_height)))
    if template_width > width or template_height > height:
        raise GapNotFoundError("expected piece is larger than the background")

    outline, _interior, template_contour = _build_slot_templates(
        piece_path, template_width, template_height
    )
    score_map = cv2.matchTemplate(
        _edge_magnitude(background), outline, cv2.TM_CCORR_NORMED
    )
    score_map = np.nan_to_num(score_map, nan=-1.0, posinf=-1.0, neginf=-1.0)
    best_score = float(score_map.max())
    if best_score < MIN_MATCH_SCORE:
        raise GapNotFoundError(
            f"no matching piece contour found (best score {best_score:.3f})"
        )

    threshold = max(MIN_MATCH_SCORE, best_score * RELATIVE_MATCH_SCORE)
    candidates = _candidate_peaks(
        score_map, threshold, template_width, template_height
    )
    if not candidates:
        raise GapNotFoundError("no qualified piece-contour candidates found")
    candidates = _shape_qualified_candidates(
        candidates,
        _background_shape_records(
            background, template_contour, template_width, template_height
        ),
        template_width,
        template_height,
    )

    ranked = [
        (brightness, -score, x, y)
        for brightness, score, x, y in candidates
    ]
    _, _, gap, _ = min(ranked)
    if not 0 < gap < width:
        raise GapNotFoundError(f"matched slot lies outside the canvas: {gap}")
    return int(gap)


def drag_slider(tab, handle, distance: float, duration: float = 0.9) -> float:
    """Drag the slider handle by an exact horizontal screen distance."""
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError(f"drag distance must be positive and finite, got {distance!r}")

    actions = tab.actions
    # Mousedown must be dispatched on the bar's handle. The floating puzzle
    # image follows the handle but is not the SDK's drag event target.
    start_x = float(handle.rect.location[0])
    actions.move_to(handle, duration=0.2)
    actions.hold()
    n = 18
    xs = [distance * (3 * t**2 - 2 * t**3) for t in np.linspace(0, 1, n + 1)]
    # Wobble is an absolute path, converted to relative movements. This avoids
    # the random-walk vertical drift in the previous implementation.
    ys = [1.2 * np.sin(2 * np.pi * t) for t in np.linspace(0, 1, n + 1)]
    try:
        for i in range(1, n + 1):
            actions.move(
                xs[i] - xs[i - 1],
                ys[i] - ys[i - 1],
                duration=duration / n,
            )
        actions.wait(0.08)
        actual_distance = float(handle.rect.location[0]) - start_x
        tolerance = max(2.0, distance * 0.02)
        if abs(actual_distance - distance) > tolerance:
            raise RuntimeError(
                "slider did not follow the automated pointer: "
                f"requested {distance:.1f}px, moved {actual_distance:.1f}px. "
                "Keep the physical mouse outside the browser tab and retry."
            )
    finally:
        # Do not leave the browser with a pressed button if a move fails.
        actions.release()
    return actual_distance
