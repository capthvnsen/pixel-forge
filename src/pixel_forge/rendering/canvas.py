"""Deterministic numpy-backed RGBA raster primitives.

No antialiasing, no float coordinates, no PIL resampling other than NEAREST.
Alpha is always strictly 0 or 255.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

Vec2 = tuple[int, int]
RGBA = tuple[int, int, int, int]


class Canvas:
    """A single (h, w, 4) uint8 RGBA raster, origin top-left, initialised transparent."""

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("Canvas dimensions must be positive")
        self._width = width
        self._height = height
        self._array: NDArray[np.uint8] = np.zeros((height, width, 4), dtype=np.uint8)

    @property
    def array(self) -> NDArray[np.uint8]:
        """The underlying (h, w, 4) uint8 RGBA array. Mutable and live: mutating it mutates
        this Canvas in place."""
        return self._array

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def set_pixel(self, x: int, y: int, rgba: RGBA) -> None:
        if rgba[3] not in (0, 255):
            raise ValueError("alpha must be 0 or 255")
        if x < 0 or x >= self._width or y < 0 or y >= self._height:
            return
        self._array[y, x] = rgba

    def get_pixel(self, x: int, y: int) -> RGBA:
        r, g, b, a = self._array[y, x]
        return (int(r), int(g), int(b), int(a))

    def draw_line(self, a: Vec2, b: Vec2, rgba: RGBA) -> None:
        """Integer Bresenham line, endpoints inclusive."""
        x0, y0 = a
        x1, y1 = b
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            self.set_pixel(x, y, rgba)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def draw_rect(self, at: Vec2, size: Vec2, rgba: RGBA, fill: bool) -> None:
        x0, y0 = at
        w, h = size
        if w <= 0 or h <= 0:
            return
        if fill:
            for y in range(y0, y0 + h):
                for x in range(x0, x0 + w):
                    self.set_pixel(x, y, rgba)
        else:
            for x in range(x0, x0 + w):
                self.set_pixel(x, y0, rgba)
                self.set_pixel(x, y0 + h - 1, rgba)
            for y in range(y0, y0 + h):
                self.set_pixel(x0, y, rgba)
                self.set_pixel(x0 + w - 1, y, rgba)

    def draw_ellipse(self, at: Vec2, size: Vec2, rgba: RGBA, fill: bool) -> None:
        """Integer midpoint ellipse inscribed in the at/size bounding box.

        Inclusion test uses doubled, box-centred integer coordinates so the result is exactly
        symmetric horizontally and vertically for both even and odd sizes.
        """
        x0, y0 = at
        w, h = size
        if w <= 0 or h <= 0:
            return
        limit = (w * h) ** 2
        mask = np.zeros((h, w), dtype=bool)
        for y in range(h):
            ny = 2 * y - (h - 1)
            for x in range(w):
                nx = 2 * x - (w - 1)
                if (nx * h) ** 2 + (ny * w) ** 2 <= limit:
                    mask[y, x] = True
        if fill:
            target = mask
        else:
            target = np.zeros_like(mask)
            for y in range(h):
                for x in range(w):
                    if not mask[y, x]:
                        continue
                    on_edge = (
                        x == 0
                        or x == w - 1
                        or y == 0
                        or y == h - 1
                        or not mask[y - 1, x]
                        or not mask[y + 1, x]
                        or not mask[y, x - 1]
                        or not mask[y, x + 1]
                    )
                    if on_edge:
                        target[y, x] = True
        ys, xs = np.nonzero(target)
        for yy, xx in zip(ys.tolist(), xs.tolist(), strict=True):
            self.set_pixel(x0 + xx, y0 + yy, rgba)

    def blit(self, other: Canvas, offset: Vec2) -> None:
        """Source-over with binary alpha: destination is replaced where source alpha is 255,
        left untouched where 0. Clips at the edges of this canvas."""
        ox, oy = offset
        sh, sw = other.array.shape[:2]
        sx0, sy0 = max(0, -ox), max(0, -oy)
        sx1, sy1 = min(sw, self._width - ox), min(sh, self._height - oy)
        if sx0 >= sx1 or sy0 >= sy1:
            return
        dx0, dy0 = sx0 + ox, sy0 + oy
        dx1, dy1 = sx1 + ox, sy1 + oy
        src_region = other.array[sy0:sy1, sx0:sx1]
        dest_region = self._array[dy0:dy1, dx0:dx1]
        opaque = src_region[..., 3] == 255
        dest_region[opaque] = src_region[opaque]

    def mirror_x(self) -> Canvas:
        c = Canvas(self._width, self._height)
        c._array[:] = np.fliplr(self._array)
        return c

    def translate(self, offset: Vec2) -> Canvas:
        """New canvas of the same size, contents shifted by offset. The vacated area is
        transparent and any content pushed off-canvas is discarded."""
        c = Canvas(self._width, self._height)
        c.blit(self, offset)
        return c

    def replace_color(self, src: RGBA, dst: RGBA) -> Canvas:
        c = self.copy()
        match = np.all(c._array == np.array(src, dtype=np.uint8), axis=-1)
        c._array[match] = np.array(dst, dtype=np.uint8)
        return c

    def colors(self) -> set[RGBA]:
        """Distinct non-transparent colours present on the canvas."""
        mask = self._array[..., 3] != 0
        pixels = self._array[mask]
        return {(int(p[0]), int(p[1]), int(p[2]), int(p[3])) for p in pixels}

    def bbox(self) -> tuple[int, int, int, int] | None:
        """Half-open (x0, y0, x1, y1) bounds of non-transparent pixels, None when empty."""
        mask = self._array[..., 3] != 0
        if not mask.any():
            return None
        ys, xs = np.nonzero(mask)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def opaque_count(self) -> int:
        return int(np.count_nonzero(self._array[..., 3] == 255))

    def copy(self) -> Canvas:
        c = Canvas(self._width, self._height)
        c._array[:] = self._array
        return c

    def to_image(self) -> Image.Image:
        return Image.fromarray(self._array, mode="RGBA")

    @classmethod
    def from_image(cls, img: Image.Image) -> Canvas:
        rgba = img.convert("RGBA")
        c = cls(rgba.width, rgba.height)
        c._array[:] = np.array(rgba, dtype=np.uint8)
        return c

    def save_png(self, path: Path) -> None:
        """Write a PNG deterministically: no compression-time metadata, so two runs against
        the same pixels produce byte-identical files."""
        self.to_image().save(path, format="PNG", optimize=False)

    def scale(self, factor: int) -> Canvas:
        """Integer nearest-neighbour upscale by repeating each pixel factor x factor times."""
        if type(factor) is not int or factor < 1:
            raise ValueError("scale factor must be a positive int")
        arr = np.repeat(np.repeat(self._array, factor, axis=0), factor, axis=1)
        c = Canvas(self._width * factor, self._height * factor)
        c._array[:] = arr
        return c

    def equals(self, other: Canvas) -> bool:
        return bool(np.array_equal(self._array, other._array))
