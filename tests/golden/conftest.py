"""`golden_image` fixture: compares a rendered `Canvas` against a committed PNG fixture.

Set `UPDATE_GOLDEN=1` to (re)write the fixture instead of comparing against it — used to
generate/refresh fixtures, then re-run without the env var to confirm they pass.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_forge.rendering.canvas import Canvas

FIXTURES_DIR = Path(__file__).parent / "fixtures"

GoldenImage = Callable[[str, Canvas], None]


def _diff_canvas(expected: Canvas, actual: Canvas) -> Canvas:
    """Side-by-side: expected | actual | actual with mismatching pixels highlighted red."""
    cw = max(expected.width, actual.width)
    ch = max(expected.height, actual.height)
    gutter = 1
    combined = Canvas(cw * 3 + gutter * 2, ch)
    combined.blit(expected, (0, 0))
    combined.blit(actual, (cw + gutter, 0))

    if (expected.width, expected.height) == (actual.width, actual.height):
        highlight = actual.copy()
        mismatch = np.any(expected.array != actual.array, axis=-1)
        highlight.array[mismatch] = (255, 0, 0, 255)
    else:
        highlight = actual
    combined.blit(highlight, (2 * (cw + gutter), 0))
    return combined


def _write_failure_artifacts(name: str, expected: Canvas, actual: Canvas) -> None:
    actual.save_png(FIXTURES_DIR / f"{name}.actual.png")
    _diff_canvas(expected, actual).save_png(FIXTURES_DIR / f"{name}.diff.png")


@pytest.fixture
def golden_image() -> GoldenImage:
    def _compare(name: str, canvas: Canvas) -> None:
        path = FIXTURES_DIR / f"{name}.png"

        if os.environ.get("UPDATE_GOLDEN") == "1":
            created = not path.exists()
            FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            canvas.save_png(path)
            print(f"golden_image: {'created' if created else 'regenerated'} fixture {path}")
            return

        if not path.exists():
            pytest.fail(
                f"golden_image: fixture {path} does not exist; "
                f"run with UPDATE_GOLDEN=1 to create it"
            )

        expected = Canvas.from_image(Image.open(path))

        if (expected.width, expected.height) != (canvas.width, canvas.height):
            _write_failure_artifacts(name, expected, canvas)
            pytest.fail(
                f"golden_image: {name} size mismatch: fixture is "
                f"{expected.width}x{expected.height}, actual is {canvas.width}x{canvas.height}"
            )

        if canvas.equals(expected):
            return

        mismatch = np.any(expected.array != canvas.array, axis=-1)
        count = int(np.count_nonzero(mismatch))
        ys, xs = np.nonzero(mismatch)
        first_x, first_y = int(xs[0]), int(ys[0])

        _write_failure_artifacts(name, expected, canvas)
        pytest.fail(
            f"golden_image: {name} differs from fixture at {count} pixel(s); "
            f"first differing coordinate (x={first_x}, y={first_y})"
        )

    return _compare
