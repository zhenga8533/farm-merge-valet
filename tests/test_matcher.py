import numpy as np

from farm_merge_valet.vision.matcher import find_best_match


def _checkerboard(height: int, width: int) -> np.ndarray:
    yy, xx = np.indices((height, width))
    pattern = ((xx // 4 + yy // 4) % 2 * 255).astype(np.uint8)
    return np.repeat(pattern[:, :, None], 3, axis=2)


def _checkerboard_bgra(height: int, width: int) -> np.ndarray:
    """A checkerboard with a non-rectangular alpha mask (a plus-shape) --
    lets tests confirm masked-out corner pixels genuinely don't affect the
    match, not just that a mask is technically present."""
    bgr = _checkerboard(height, width)
    alpha = np.zeros((height, width), dtype=np.uint8)
    third_h, third_w = height // 3, width // 3
    alpha[third_h : 2 * third_h, :] = 255
    alpha[:, third_w : 2 * third_w] = 255
    return np.dstack([bgr, alpha])


def test_find_best_match_locates_exact_subregion() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = _checkerboard(20, 40)
    frame[40:60, 30:70] = template

    match = find_best_match(frame, template, min_confidence=0.9)

    assert match is not None
    assert match.x == 30
    assert match.y == 40


def test_find_best_match_returns_none_below_confidence() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = _checkerboard(20, 40)

    match = find_best_match(frame, template, min_confidence=0.9)

    assert match is None


def test_find_best_match_bgra_template_locates_exact_subregion() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = _checkerboard_bgra(20, 40)
    frame[40:60, 30:70] = template[:, :, :3]

    match = find_best_match(frame, template, min_confidence=0.9)

    assert match is not None
    assert match.x == 30
    assert match.y == 40


def test_find_best_match_bgra_ignores_masked_out_pixels() -> None:
    """Corners are masked out (alpha=0), so corrupting only those pixels in
    the frame must not affect the match at all."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = _checkerboard_bgra(20, 40)
    frame[40:60, 30:70] = template[:, :, :3]
    # Corrupt a masked-out corner region of the placed template in the frame.
    frame[40:46, 30:36] = 128

    match = find_best_match(frame, template, min_confidence=0.9)

    assert match is not None
    assert match.x == 30
    assert match.y == 40


def test_find_best_match_bgra_returns_none_below_confidence() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    template = _checkerboard_bgra(20, 40)

    match = find_best_match(frame, template, min_confidence=0.9)

    assert match is None
