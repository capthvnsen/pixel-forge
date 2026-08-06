"""Tests for `rendering.sheet_import.slice_sheet`: layout mapping, baseline alignment,
scale verification, background detection, and error paths.
"""

from __future__ import annotations

import pytest
from PIL import Image

from pixel_forge.errors import ForgeError
from pixel_forge.rendering.sheet_import import SheetImportOptions, slice_sheet

BG = (30, 30, 30, 255)
FG = (255, 0, 0, 255)
CELL = 10


def _fill_rect(img: Image.Image, x0: int, y0: int, w: int, h: int, rgba: tuple[int, ...]) -> None:
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            img.putpixel((x, y), rgba)


def _compass8_sheet(*, heights: dict[str, int] | None = None) -> Image.Image:
    """A 3x3, cell-size-10 compass sheet: south_west/south/south_east on top,
    west/east flanking an empty centre, north_west/north/north_east on the bottom --
    the documented `compass8` vertical convention. Each outer cell gets a 4px-wide
    sprite of height `heights[name]` (default 4), flush with the cell's bottom row.
    """
    img = Image.new("RGBA", (CELL * 3, CELL * 3), BG)
    layout = [
        (0, 0, "south_west"),
        (0, 1, "south"),
        (0, 2, "south_east"),
        (1, 0, "west"),
        (1, 2, "east"),
        (2, 0, "north_west"),
        (2, 1, "north"),
        (2, 2, "north_east"),
    ]
    for row, col, name in layout:
        h = (heights or {}).get(name, 4)
        x0 = col * CELL + 3
        y1 = row * CELL + CELL  # one past the cell's last row
        _fill_rect(img, x0, y1 - h, 4, h, FG)
    return img


# --- compass8 layout / naming --------------------------------------------------------------


def test_compass8_produces_eight_directions_with_documented_names() -> None:
    sheet = _compass8_sheet()
    report = slice_sheet(
        sheet, SheetImportOptions(grid=(3, 3), layout="compass8", canvas=20, baseline=15)
    )
    assert report.directions == (
        "south_west",
        "south",
        "south_east",
        "west",
        "east",
        "north_west",
        "north",
        "north_east",
    )
    assert report.cells_total == 9
    assert report.cells_skipped == 1
    assert len(report.frames) == 8


def test_compass8_flipped_inverts_the_vertical_mapping() -> None:
    sheet = _compass8_sheet()
    report = slice_sheet(
        sheet,
        SheetImportOptions(grid=(3, 3), layout="compass8-flipped", canvas=20, baseline=15),
    )
    assert report.directions == (
        "north_west",
        "north",
        "north_east",
        "west",
        "east",
        "south_west",
        "south",
        "south_east",
    )


# --- baseline alignment ----------------------------------------------------------------------


def test_every_direction_lands_its_lowest_opaque_row_on_the_baseline() -> None:
    sheet = _compass8_sheet(heights={"south": 2, "north": 8, "west": 5, "east": 1, "south_west": 6})
    baseline = 15
    report = slice_sheet(
        sheet, SheetImportOptions(grid=(3, 3), layout="compass8", canvas=20, baseline=baseline)
    )
    assert len(report.frames) == 8
    for frame in report.frames:
        bbox = frame.canvas.bbox()
        assert bbox is not None, frame.direction
        assert bbox[3] - 1 == baseline, f"{frame.direction}: {bbox}"


def test_frames_are_horizontally_centred_on_the_crop_width() -> None:
    sheet = _compass8_sheet()
    canvas_size = 20
    report = slice_sheet(
        sheet,
        SheetImportOptions(grid=(3, 3), layout="compass8", canvas=canvas_size, baseline=15),
    )
    for frame in report.frames:
        bbox = frame.canvas.bbox()
        assert bbox is not None
        x0, _, x1, _ = bbox
        centre = (x0 + x1) / 2
        assert abs(centre - canvas_size / 2) <= 1, f"{frame.direction}: {bbox}"


# --- scale ------------------------------------------------------------------------------------


def test_scale_round_trips_to_the_original_pixels() -> None:
    base = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
    _fill_rect(base, 1, 1, 2, 2, (10, 20, 30, 255))
    base.putpixel((4, 4), (40, 50, 60, 255))
    sheet = base.resize((18, 18), Image.Resampling.NEAREST)

    canvas_size, baseline = 20, 15
    report = slice_sheet(
        sheet,
        SheetImportOptions(
            grid=(1, 1),
            directions=("only",),
            scale=3,
            canvas=canvas_size,
            baseline=baseline,
            background="transparent",
        ),
    )
    assert len(report.frames) == 1
    canvas = report.frames[0].canvas

    base_bbox = (1, 1, 5, 5)  # (1,1)-(3,3) block plus the (4,4) pixel
    x0, y0, x1, y1 = base_bbox
    bbox_w, bbox_h = x1 - x0, y1 - y0
    dest_x = (canvas_size - bbox_w) // 2
    dest_y = baseline - bbox_h + 1
    for y in range(y0, y1):
        for x in range(x0, x1):
            expected = base.getpixel((x, y))
            actual = canvas.get_pixel(dest_x + (x - x0), dest_y + (y - y0))
            assert actual == expected, (x, y)


def test_a_non_uniform_block_raises_naming_the_first_offending_block() -> None:
    base = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    sheet = base.resize((12, 12), Image.Resampling.NEAREST)
    # Corrupt one pixel inside the block at (col=0, row=1): source pixel (0,1) covers
    # sheet rows 3-5, cols 0-2 -- touch (0, 4) without touching (0, 1)'s own block.
    sheet.putpixel((0, 4), (9, 9, 9, 255))
    with pytest.raises(ForgeError, match=r"col=0, row=1"):
        slice_sheet(
            sheet,
            SheetImportOptions(grid=(1, 1), directions=("only",), scale=3, canvas=20, baseline=15),
        )


# --- background --------------------------------------------------------------------------------


def test_auto_background_picks_the_dominant_colour() -> None:
    sheet = Image.new("RGBA", (10, 10), (200, 200, 200, 255))
    _fill_rect(sheet, 3, 3, 3, 3, (10, 20, 30, 255))
    report = slice_sheet(
        sheet,
        SheetImportOptions(
            grid=(1, 1), directions=("only",), canvas=20, baseline=15, background="auto"
        ),
    )
    canvas = report.frames[0].canvas
    assert canvas.opaque_count() == 9


def test_explicit_hex_background_overrides_the_majority_colour() -> None:
    sheet = Image.new("RGBA", (10, 10), (200, 200, 200, 255))  # majority: 60 px
    _fill_rect(sheet, 0, 0, 4, 10, (10, 20, 30, 255))  # 40 px, the *intended* background
    report = slice_sheet(
        sheet,
        SheetImportOptions(
            grid=(1, 1),
            directions=("only",),
            canvas=20,
            baseline=15,
            background="#0a141e",
        ),
    )
    canvas = report.frames[0].canvas
    assert canvas.opaque_count() == 60


def test_transparent_background_leaves_existing_alpha_alone() -> None:
    sheet = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    _fill_rect(sheet, 2, 2, 3, 3, (5, 6, 7, 255))
    report = slice_sheet(
        sheet,
        SheetImportOptions(
            grid=(1, 1), directions=("only",), canvas=20, baseline=15, background="transparent"
        ),
    )
    canvas = report.frames[0].canvas
    assert canvas.opaque_count() == 9


# --- grid / directions error paths -------------------------------------------------------------


def test_a_grid_that_does_not_divide_evenly_raises_naming_the_dimensions() -> None:
    sheet = Image.new("RGBA", (10, 7), BG)
    with pytest.raises(ForgeError, match=r"10x7.*3x3"):
        slice_sheet(
            sheet, SheetImportOptions(grid=(3, 3), layout="compass8", canvas=20, baseline=15)
        )


def test_directions_count_mismatch_raises_naming_both_counts() -> None:
    sheet = Image.new("RGBA", (CELL * 2, CELL), BG)
    _fill_rect(sheet, 1, 1, 2, 2, FG)
    _fill_rect(sheet, CELL + 1, 1, 2, 2, FG)
    with pytest.raises(ForgeError, match=r"1 name.*2 non-empty"):
        slice_sheet(
            sheet, SheetImportOptions(grid=(2, 1), directions=("only",), canvas=20, baseline=15)
        )


def test_exceeding_the_palette_limit_raises_naming_count_and_limit() -> None:
    sheet = Image.new("RGBA", (CELL, CELL), BG)
    _fill_rect(sheet, 1, 1, 1, 1, (1, 1, 1, 255))
    _fill_rect(sheet, 2, 1, 1, 1, (2, 2, 2, 255))
    _fill_rect(sheet, 3, 1, 1, 1, (3, 3, 3, 255))
    with pytest.raises(ForgeError, match=r"3 colour.*--palette-limit 2"):
        slice_sheet(
            sheet,
            SheetImportOptions(
                grid=(1, 1), directions=("only",), canvas=20, baseline=15, palette_limit=2
            ),
        )


# --- frames_per_cell ---------------------------------------------------------------------------


def test_frames_per_cell_produces_indices_0_to_n_minus_1_aligned_as_a_group() -> None:
    cell_w, cell_h = 12, 10
    sheet = Image.new("RGBA", (cell_w, cell_h), BG)
    # Strip 0 (x 0-3): rect at local y5-8. Strip 1 (x 4-7): rect at local y1-2.
    # Strip 2 (x 8-11): rect at local y7. Union bbox local y range is [1, 9).
    _fill_rect(sheet, 1, 5, 2, 4, FG)
    _fill_rect(sheet, 5, 1, 2, 2, FG)
    _fill_rect(sheet, 9, 7, 2, 1, FG)

    canvas_size, baseline = 24, 15
    report = slice_sheet(
        sheet,
        SheetImportOptions(
            grid=(1, 1),
            directions=("only",),
            canvas=canvas_size,
            baseline=baseline,
            frames_per_cell=3,
        ),
    )
    assert [f.index for f in report.frames] == [0, 1, 2]
    assert {f.direction for f in report.frames} == {"only"}

    union_bbox_w, union_bbox_h = 2, 8  # x in [1,3), y in [1,9) local to each strip
    dest_x = (canvas_size - union_bbox_w) // 2
    dest_y = baseline - union_bbox_h + 1

    expected_local_y_ranges = [(4, 8), (0, 2), (6, 7)]  # relative to the union crop's y0=1
    for frame, (ly0, ly1) in zip(report.frames, expected_local_y_ranges, strict=True):
        bbox = frame.canvas.bbox()
        assert bbox == (dest_x, dest_y + ly0, dest_x + 2, dest_y + ly1), frame.index


# --- option validation ---------------------------------------------------------------------------


def test_grid_and_cell_are_mutually_exclusive() -> None:
    with pytest.raises(ForgeError, match="exactly one of --grid or --cell"):
        SheetImportOptions(grid=(1, 1), cell=(4, 4), layout="compass8")


def test_layout_and_directions_are_mutually_exclusive() -> None:
    with pytest.raises(ForgeError, match="exactly one of --layout or --directions"):
        SheetImportOptions(grid=(1, 1), layout="compass8", directions=("a",))
