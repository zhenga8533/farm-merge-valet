import numpy as np

from farm_merge_valet.vision.matcher import find_best_match


def _checkerboard(height: int, width: int) -> np.ndarray:
    yy, xx = np.indices((height, width))
    pattern = ((xx // 4 + yy // 4) % 2 * 255).astype(np.uint8)
    return np.repeat(pattern[:, :, None], 3, axis=2)


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
