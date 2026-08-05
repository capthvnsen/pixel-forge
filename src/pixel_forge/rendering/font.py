"""A tiny built-in 3x5 bitmap font — no external font files.

Each glyph is 5 rows of a 3-bit mask; bit `x` (0..2) set means column `x` is lit. Coverage:
a-z, 0-9, `/`, `_`, `-`, `.`, `:`, and space. Uppercase input maps to the lowercase glyph.
"""

from __future__ import annotations

from pixel_forge.rendering.canvas import RGBA, Canvas, Vec2

_GLYPH_W = 3
_GLYPH_H = 5

# Source patterns, top row first, '#' = lit, '.' = blank. Packed into bitmasks below.
_GLYPH_ROWS: dict[str, tuple[str, str, str, str, str]] = {
    " ": ("...", "...", "...", "...", "..."),
    "0": (".#.", "#.#", "#.#", "#.#", ".#."),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("##.", "..#", ".#.", "#..", "###"),
    "3": ("##.", "..#", ".#.", "..#", "##."),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "##.", "..#", "##."),
    "6": (".##", "#..", "##.", "#.#", ".#."),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": (".#.", "#.#", ".#.", "#.#", ".#."),
    "9": (".#.", "#.#", ".##", "..#", ".#."),
    "a": (".#.", "#.#", "###", "#.#", "#.#"),
    "b": ("##.", "#.#", "##.", "#.#", "##."),
    "c": (".##", "#..", "#..", "#..", ".##"),
    "d": ("##.", "#.#", "#.#", "#.#", "##."),
    "e": ("###", "#..", "##.", "#..", "###"),
    "f": ("###", "#..", "##.", "#..", "#.."),
    "g": (".##", "#..", "#.#", "#.#", ".##"),
    "h": ("#.#", "#.#", "###", "#.#", "#.#"),
    "i": ("###", ".#.", ".#.", ".#.", "###"),
    "j": ("..#", "..#", "..#", "#.#", ".#."),
    "k": ("#.#", "#.#", "##.", "#.#", "#.#"),
    "l": ("#..", "#..", "#..", "#..", "###"),
    "m": ("#.#", "###", "#.#", "#.#", "#.#"),
    "n": ("##.", "#.#", "#.#", "#.#", "#.#"),
    "o": (".#.", "#.#", "#.#", "#.#", ".#."),
    "p": ("##.", "#.#", "##.", "#..", "#.."),
    "q": (".#.", "#.#", "#.#", ".##", "..#"),
    "r": ("##.", "#.#", "##.", "#.#", "#.#"),
    "s": (".##", "#..", ".#.", "..#", "##."),
    "t": ("###", ".#.", ".#.", ".#.", ".#."),
    "u": ("#.#", "#.#", "#.#", "#.#", ".#."),
    "v": ("#.#", "#.#", "#.#", ".#.", ".#."),
    "w": ("#.#", "#.#", "#.#", "###", "#.#"),
    "x": ("#.#", "#.#", ".#.", "#.#", "#.#"),
    "y": ("#.#", "#.#", ".#.", ".#.", ".#."),
    "z": ("###", "..#", ".#.", "#..", "###"),
    "/": ("..#", "..#", ".#.", "#..", "#.."),
    "_": ("...", "...", "...", "...", "###"),
    "-": ("...", "...", "###", "...", "..."),
    ".": ("...", "...", "...", "...", ".#."),
    ":": ("...", ".#.", "...", ".#.", "..."),
}


def _pack(rows: tuple[str, str, str, str, str]) -> tuple[int, int, int, int, int]:
    packed = tuple(sum(1 << x for x, ch in enumerate(row) if ch == "#") for row in rows)
    return (packed[0], packed[1], packed[2], packed[3], packed[4])


FONT: dict[str, tuple[int, ...]] = {ch: _pack(rows) for ch, rows in _GLYPH_ROWS.items()}
_UNKNOWN_GLYPH: tuple[int, ...] = (0b111, 0b111, 0b111, 0b111, 0b111)


def text_width(text: str, spacing: int = 1) -> int:
    if not text:
        return 0
    return len(text) * _GLYPH_W + (len(text) - 1) * spacing


def draw_text(canvas: Canvas, text: str, at: Vec2, rgba: RGBA, spacing: int = 1) -> int:
    """Draw `text` with the built-in bitmap font, clipped at the canvas edge. Unknown
    characters render as a filled 3x5 block. Returns the width consumed."""
    x0, y0 = at
    x = x0
    for ch in text:
        glyph = FONT.get(ch.lower(), _UNKNOWN_GLYPH)
        for row, bits in enumerate(glyph):
            for col in range(_GLYPH_W):
                if bits & (1 << col):
                    canvas.set_pixel(x + col, y0 + row, rgba)
        x += _GLYPH_W + spacing
    return text_width(text, spacing)
