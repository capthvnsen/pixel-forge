"""Tests for GIF/WebP animated preview writers."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge.errors import ForgeError
from pixel_forge.preview.animated import write_gif, write_preview, write_webp
from pixel_forge.rendering.canvas import Canvas

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


def _frame(
    color: tuple[int, int, int, int], transparent_at: tuple[int, int] | None = None
) -> Canvas:
    c = Canvas(4, 4)
    c.draw_rect((0, 0), (4, 4), color, fill=True)
    if transparent_at is not None:
        c.set_pixel(transparent_at[0], transparent_at[1], (0, 0, 0, 0))
    return c


def _frames(n: int) -> list[Canvas]:
    colors = [RED, BLUE]
    return [_frame(colors[i % 2]) for i in range(n)]


# --- write_gif -----------------------------------------------------------------------------


def test_write_gif_frame_count_and_durations(tmp_path: Path) -> None:
    path = tmp_path / "out.gif"
    write_gif(path, _frames(3), [100, 200, 300])

    with Image.open(path) as im:
        assert im.n_frames == 3
        durations = []
        for i in range(im.n_frames):
            im.seek(i)
            durations.append(im.info["duration"])
        assert durations == [100, 200, 300]


def test_write_gif_preserves_transparency(tmp_path: Path) -> None:
    path = tmp_path / "out.gif"
    frame = _frame(RED, transparent_at=(1, 1))
    write_gif(path, [frame], [100])

    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        assert rgba.getpixel((1, 1))[3] == 0
        assert rgba.getpixel((0, 0))[3] == 255


def test_write_gif_loop_flag_honoured(tmp_path: Path) -> None:
    looping = tmp_path / "loop.gif"
    once = tmp_path / "once.gif"
    write_gif(looping, _frames(2), [100, 100], loop=True)
    write_gif(once, _frames(2), [100, 100], loop=False)

    with Image.open(looping) as im:
        assert im.info.get("loop") == 0
    with Image.open(once) as im:
        assert im.info.get("loop") is None


def test_write_gif_scale_doubles_dimensions(tmp_path: Path) -> None:
    path_1x = tmp_path / "1x.gif"
    path_2x = tmp_path / "2x.gif"
    write_gif(path_1x, _frames(1), [100], scale=1)
    write_gif(path_2x, _frames(1), [100], scale=2)

    with Image.open(path_1x) as im1, Image.open(path_2x) as im2:
        assert im2.size == (im1.size[0] * 2, im1.size[1] * 2)


def test_write_gif_byte_determinism(tmp_path: Path) -> None:
    path_a = tmp_path / "a.gif"
    path_b = tmp_path / "b.gif"
    write_gif(path_a, _frames(3), [100, 150, 200])
    write_gif(path_b, _frames(3), [100, 150, 200])
    assert path_a.read_bytes() == path_b.read_bytes()


def test_write_gif_mismatched_lengths_raises(tmp_path: Path) -> None:
    with pytest.raises(ForgeError):
        write_gif(tmp_path / "bad.gif", _frames(2), [100])


def test_write_gif_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ForgeError):
        write_gif(tmp_path / "bad.gif", [], [])


# --- write_webp ----------------------------------------------------------------------------


def test_write_webp_frame_count(tmp_path: Path) -> None:
    path = tmp_path / "out.webp"
    write_webp(path, _frames(3), [100, 200, 300])

    with Image.open(path) as im:
        assert im.n_frames == 3


def test_write_webp_byte_determinism(tmp_path: Path) -> None:
    path_a = tmp_path / "a.webp"
    path_b = tmp_path / "b.webp"
    write_webp(path_a, _frames(3), [100, 150, 200])
    write_webp(path_b, _frames(3), [100, 150, 200])
    assert path_a.read_bytes() == path_b.read_bytes()


def test_write_webp_mismatched_lengths_raises(tmp_path: Path) -> None:
    with pytest.raises(ForgeError):
        write_webp(tmp_path / "bad.webp", _frames(2), [100])


def test_write_webp_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ForgeError):
        write_webp(tmp_path / "bad.webp", [], [])


# --- write_preview ---------------------------------------------------------------------------


def test_write_preview_gif(tmp_path: Path) -> None:
    result = write_preview(tmp_path / "preview", _frames(2), [100, 100], fmt="gif")
    assert result == tmp_path / "preview.gif"
    assert result.exists()


def test_write_preview_webp(tmp_path: Path) -> None:
    result = write_preview(tmp_path / "preview", _frames(2), [100, 100], fmt="webp")
    assert result == tmp_path / "preview.webp"
    assert result.exists()
