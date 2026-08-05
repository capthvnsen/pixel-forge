"""Tests for PNG ingestion: assign_chars, png_to_bitmap, extract_palette, load_image."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge.domain.palette import ResolvedPalette, resolve_palette
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.ingest import (
    assign_chars,
    extract_palette,
    load_image,
    png_to_bitmap,
)
from pixel_forge.schemas.palette import Palette, PaletteColor

RGBA = tuple[int, int, int, int]

TRANSPARENT: RGBA = (0, 0, 0, 0)
INK: RGBA = (0, 0, 0, 255)
SKIN: RGBA = (255, 204, 153, 255)
OFF_PALETTE: RGBA = (18, 52, 86, 255)  # #123456, not in the test palette


def _palette(ids_hex: list[tuple[str, str]], palette_id: str = "p") -> ResolvedPalette:
    return resolve_palette(
        Palette(id=palette_id, colors=[PaletteColor(id=i, hex=h) for i, h in ids_hex])
    )


def _image(rows: list[list[RGBA]]) -> Image.Image:
    height = len(rows)
    width = len(rows[0])
    img = Image.new("RGBA", (width, height))
    for y, row in enumerate(rows):
        for x, rgba in enumerate(row):
            img.putpixel((x, y), rgba)
    return img


# --- assign_chars -----------------------------------------------------------------------


def test_assign_chars_stable_across_calls_and_reconstruction() -> None:
    rp1 = _palette([("ink", "#000000"), ("iron", "#888888"), ("indigo", "#4b0082")])
    rp2 = _palette([("ink", "#000000"), ("iron", "#888888"), ("indigo", "#4b0082")])
    m1 = assign_chars(rp1)
    m2 = assign_chars(rp1)
    m3 = assign_chars(rp2)
    assert m1 == m2 == m3


def test_assign_chars_shared_first_letter_gets_distinct_chars() -> None:
    rp = _palette([("ink", "#000000"), ("iron", "#888888")])
    chars = assign_chars(rp)
    assert chars["ink"] != chars["iron"]
    assert len(set(chars.values())) == 2


def test_assign_chars_never_assigns_reserved_chars() -> None:
    rp = _palette([(f"c{i}", f"#{(i * 111) % 0xFFFFFF:06x}") for i in range(50)])
    chars = assign_chars(rp)
    assert "." not in chars.values()
    assert " " not in chars.values()
    assert len(set(chars.values())) == 50


def test_assign_chars_raises_when_palette_exceeds_pool() -> None:
    rp = _palette([(f"c{i:03d}", "#000000") for i in range(63)])
    with pytest.raises(ForgeError):
        assign_chars(rp)


# --- png_to_bitmap: exact match, trim ----------------------------------------------------


def _ink_skin_palette() -> ResolvedPalette:
    return _palette([("ink", "#000000"), ("skin", "#ffcc99")])


def test_exact_match_trim_true_crops_and_reports() -> None:
    rp = _ink_skin_palette()
    img = _image(
        [
            [TRANSPARENT, TRANSPARENT, TRANSPARENT, TRANSPARENT],
            [TRANSPARENT, INK, INK, TRANSPARENT],
            [TRANSPARENT, SKIN, SKIN, TRANSPARENT],
            [TRANSPARENT, TRANSPARENT, TRANSPARENT, TRANSPARENT],
        ]
    )
    bitmap, report = png_to_bitmap(img, rp, trim=True)
    chars = assign_chars(rp)
    assert bitmap == {
        "op": "bitmap",
        "at": [0, 0],
        "key": {chars["ink"]: "ink", chars["skin"]: "skin"},
        "rows": [chars["ink"] * 2, chars["skin"] * 2],
    }
    assert report.width == 2
    assert report.height == 2
    assert report.matched == 4
    assert report.snapped == {}
    assert report.unmatched == {}
    assert report.added_colors == ()
    assert report.trimmed_to == (1, 1, 3, 3)


def test_trim_false_keeps_full_size() -> None:
    rp = _ink_skin_palette()
    img = _image(
        [
            [TRANSPARENT, TRANSPARENT, TRANSPARENT, TRANSPARENT],
            [TRANSPARENT, INK, INK, TRANSPARENT],
            [TRANSPARENT, SKIN, SKIN, TRANSPARENT],
            [TRANSPARENT, TRANSPARENT, TRANSPARENT, TRANSPARENT],
        ]
    )
    bitmap, report = png_to_bitmap(img, rp, trim=False)
    chars = assign_chars(rp)
    i, s = chars["ink"], chars["skin"]
    assert bitmap["rows"] == [
        "....",
        f".{i}{i}.",
        f".{s}{s}.",
        "....",
    ]
    assert report.width == 4
    assert report.height == 4
    assert report.trimmed_to is None


def test_fully_transparent_image_with_trim_raises() -> None:
    rp = _ink_skin_palette()
    img = _image([[TRANSPARENT] * 3 for _ in range(3)])
    with pytest.raises(ForgeError):
        png_to_bitmap(img, rp, trim=True)


# --- png_to_bitmap: unmatched / snapped ---------------------------------------------------


def test_off_palette_pixel_unmatched_renders_transparent() -> None:
    rp = _ink_skin_palette()
    img = _image([[INK, OFF_PALETTE]])
    bitmap, report = png_to_bitmap(img, rp, snap=False, trim=False)
    chars = assign_chars(rp)
    assert bitmap["rows"] == [f"{chars['ink']}."]
    assert report.matched == 1
    assert report.snapped == {}
    assert report.unmatched == {"#123456": 1}


def test_off_palette_pixel_snapped_to_nearest() -> None:
    rp = _ink_skin_palette()
    img = _image([[INK, OFF_PALETTE]])
    bitmap, report = png_to_bitmap(img, rp, snap=True, trim=False)
    chars = assign_chars(rp)
    nearest_id = rp.nearest(OFF_PALETTE)
    assert bitmap["rows"] == [f"{chars['ink']}{chars[nearest_id]}"]
    assert report.matched == 1
    assert report.unmatched == {}
    assert report.snapped == {"#123456": 1}


# --- alpha threshold ----------------------------------------------------------------------


def test_alpha_boundary_127_transparent_128_opaque() -> None:
    rp = _ink_skin_palette()
    below = (0, 0, 0, 127)
    at_threshold = (0, 0, 0, 128)
    img = _image([[below, at_threshold]])
    bitmap, report = png_to_bitmap(img, rp, trim=False)
    chars = assign_chars(rp)
    assert bitmap["rows"] == [f".{chars['ink']}"]
    assert report.matched == 1


# --- extract_palette ------------------------------------------------------------------


def test_extract_palette_deterministic_order_and_max_colors() -> None:
    # 3x red, 2x green, 1x blue, plus transparent pixels that must be excluded.
    red: RGBA = (255, 0, 0, 255)
    green: RGBA = (0, 255, 0, 255)
    blue: RGBA = (0, 0, 255, 255)
    img = _image(
        [
            [red, red, red, green],
            [green, blue, TRANSPARENT, TRANSPARENT],
        ]
    )
    palette = extract_palette(img, max_colors=2)
    assert [c.id for c in palette.colors] == ["c00", "c01"]
    assert [c.hex for c in palette.colors] == ["#ff0000", "#00ff00"]

    palette_again = extract_palette(img, max_colors=2)
    assert palette.model_dump() == palette_again.model_dump()


def test_extract_palette_tie_broken_by_ascending_hex() -> None:
    a: RGBA = (255, 0, 0, 255)  # #ff0000
    b: RGBA = (0, 0, 255, 255)  # #0000ff
    img = _image([[a, b]])  # equal counts of each
    palette = extract_palette(img, max_colors=5)
    assert [c.hex for c in palette.colors] == ["#0000ff", "#ff0000"]


def test_extract_palette_then_ingest_round_trips_with_zero_unmatched() -> None:
    red: RGBA = (255, 0, 0, 255)
    green: RGBA = (0, 255, 0, 255)
    img = _image([[red, green, red], [green, red, green]])
    extracted = extract_palette(img)
    rp = resolve_palette(extracted)
    _bitmap, report = png_to_bitmap(img, rp, trim=False)
    assert report.unmatched == {}
    assert report.matched == 6


# --- load_image ----------------------------------------------------------------------


def test_load_image_missing_file_raises_forge_error(tmp_path: Path) -> None:
    with pytest.raises(ForgeError):
        load_image(tmp_path / "does-not-exist.png")


def test_load_image_loads_and_converts_to_rgba(tmp_path: Path) -> None:
    src = _image([[INK, SKIN]])
    path = tmp_path / "sprite.png"
    src.save(path, format="PNG")
    loaded = load_image(path)
    assert loaded.mode == "RGBA"
    assert loaded.size == (2, 1)


# --- BitmapShape validation (schemas.common owned by a concurrent task) ---------------


def test_returned_dict_validates_as_bitmap_shape() -> None:
    try:
        from pixel_forge.schemas.common import BitmapShape
    except ImportError:
        pytest.skip("BitmapShape not implemented yet in schemas/common.py")
        return

    rp = _ink_skin_palette()
    img = _image([[INK, SKIN]])
    bitmap, _report = png_to_bitmap(img, rp, trim=False)
    shape = BitmapShape.model_validate(bitmap)
    assert shape.op == "bitmap"
