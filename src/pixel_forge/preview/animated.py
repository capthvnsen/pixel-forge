"""Animated preview writers: deterministic GIF and lossless WebP.

Scaling always goes through `Canvas.scale` (integer nearest) — never PIL resize.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from PIL import Image

from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import Canvas

_TRANSPARENT_INDEX = 255


def _validate(frames: Sequence[Canvas], durations_ms: Sequence[int]) -> None:
    if not frames:
        raise ForgeError("preview: frames must be non-empty")
    if len(frames) != len(durations_ms):
        raise ForgeError(
            f"preview: frames ({len(frames)}) and durations_ms ({len(durations_ms)}) "
            "must have the same length"
        )


def _scaled(frames: Sequence[Canvas], scale: int) -> list[Canvas]:
    return [f.scale(scale) for f in frames] if scale != 1 else list(frames)


def _to_gif_frame(canvas: Canvas) -> Image.Image:
    """Palette-quantise to at most 255 colours, reserving index 255 for transparency."""
    rgba_image = canvas.to_image()
    alpha = rgba_image.getchannel("A")
    p_image = rgba_image.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
    transparent_mask = alpha.point(lambda a: 255 if a == 0 else 0)
    p_image.paste(_TRANSPARENT_INDEX, transparent_mask)
    p_image.info["transparency"] = _TRANSPARENT_INDEX
    return p_image


def write_gif(
    path: Path,
    frames: Sequence[Canvas],
    durations_ms: Sequence[int],
    *,
    loop: bool = True,
    scale: int = 1,
) -> Path:
    _validate(frames, durations_ms)
    p_frames = [_to_gif_frame(c) for c in _scaled(frames, scale)]
    first, rest = p_frames[0], p_frames[1:]
    if loop:
        first.save(
            path,
            format="GIF",
            save_all=True,
            append_images=rest,
            duration=list(durations_ms),
            disposal=2,
            optimize=False,
            transparency=_TRANSPARENT_INDEX,
            loop=0,
        )
    else:
        first.save(
            path,
            format="GIF",
            save_all=True,
            append_images=rest,
            duration=list(durations_ms),
            disposal=2,
            optimize=False,
            transparency=_TRANSPARENT_INDEX,
        )
    return path


def write_webp(
    path: Path,
    frames: Sequence[Canvas],
    durations_ms: Sequence[int],
    *,
    loop: bool = True,
    scale: int = 1,
) -> Path:
    _validate(frames, durations_ms)
    images = [c.to_image() for c in _scaled(frames, scale)]
    first, rest = images[0], images[1:]
    first.save(
        path,
        format="WEBP",
        save_all=True,
        append_images=rest,
        duration=list(durations_ms),
        loop=0 if loop else 1,
        lossless=True,
        quality=100,
        method=6,
    )
    return path


def write_preview(
    path_without_ext: Path,
    frames: Sequence[Canvas],
    durations_ms: Sequence[int],
    *,
    fmt: Literal["gif", "webp"] = "gif",
    loop: bool = True,
    scale: int = 1,
) -> Path:
    if fmt == "gif":
        return write_gif(
            path_without_ext.with_suffix(".gif"), frames, durations_ms, loop=loop, scale=scale
        )
    if fmt == "webp":
        return write_webp(
            path_without_ext.with_suffix(".webp"), frames, durations_ms, loop=loop, scale=scale
        )
    raise ForgeError(f"write_preview: unknown fmt {fmt!r}")
