"""Integration tests for `api.import_region`: PNG-to-bitmap import, palette extension,
unmatched-pixel reporting, path safety, determinism, and revision recording.

Round-trip pixel fidelity (`test_import_region_round_trip_matches_source_pixels`) is the
single most important test here: it proves a PNG imported through `import_region` renders
back out pixel-for-pixel identical to the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge import api
from pixel_forge.errors import ForgeError, PathSecurityError

_INK_RGBA = (0x20, 0x20, 0x20, 255)


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    api.init_project(root, name)
    api.new_asset(root, "character", "hero")
    return root


def _write_png(
    path: Path, pixels: dict[tuple[int, int], tuple[int, int, int, int]], size: tuple[int, int]
) -> None:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for (x, y), rgba in pixels.items():
        img.putpixel((x, y), rgba)
    img.save(path)


def _solid_matching_png(path: Path, size: tuple[int, int] = (2, 2)) -> None:
    Image.new("RGBA", size, _INK_RGBA).save(path)


# --- round-trip fidelity ------------------------------------------------------------------------


def test_import_region_round_trip_matches_source_pixels(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"

    # 6x6 canvas, 1px transparent border, a 4x4 interior of four distinct 2x2 colour
    # quadrants -- exercises both trimming (the border) and multi-colour ingest.
    colors = {
        "red": (255, 0, 0, 255),
        "green": (0, 255, 0, 255),
        "blue": (0, 0, 255, 255),
        "yellow": (255, 255, 0, 255),
    }
    pixels: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for y in range(1, 5):
        for x in range(1, 5):
            if x < 3 and y < 3:
                pixels[(x, y)] = colors["red"]
            elif x >= 3 and y < 3:
                pixels[(x, y)] = colors["green"]
            elif x < 3 and y >= 3:
                pixels[(x, y)] = colors["blue"]
            else:
                pixels[(x, y)] = colors["yellow"]
    _write_png(png_path, pixels, (6, 6))

    result = api.import_region(
        root, "hero", "block", "sprite.png", extend_palette=True, timestamp="2026-08-05T00:00:00Z"
    )
    assert result.unmatched == {}
    assert result.matched == 16  # re-ingested against the extended palette: nothing left unmatched
    assert sorted(result.added_colors) == [
        "import_0000ff",
        "import_00ff00",
        "import_ff0000",
        "import_ffff00",
    ]

    render = api.render_asset(root, "hero", force=True)
    rendered = Image.open(root / render.frame_paths[0]).convert("RGBA")

    # anchor "root" is at (16, 16); with no `at` override the bitmap's world position
    # equals anchor + source coordinate, regardless of the trim offset.
    ax, ay = 16, 16
    for (x, y), rgba in pixels.items():
        assert rendered.getpixel((ax + x, ay + y)) == rgba
    # nothing outside the imported region should have been painted.
    assert rendered.getpixel((ax, ay)) == (0, 0, 0, 0)


def test_import_region_is_deterministic(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)
    spec_path = root / "assets" / "hero" / "hero.yaml"

    api.import_region(root, "hero", "block", "sprite.png", timestamp="2026-08-05T00:00:00Z")
    first = spec_path.read_bytes()

    api.import_region(root, "hero", "block", "sprite.png", timestamp="2026-08-05T01:00:00Z")
    second = spec_path.read_bytes()

    assert first == second


# --- palette extension ---------------------------------------------------------------------


def test_import_region_extend_palette_adds_missing_colours_and_matches_everything(
    tmp_path: Path,
) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _write_png(png_path, {(0, 0): (255, 0, 0, 255), (1, 0): _INK_RGBA}, (2, 1))

    result = api.import_region(
        root, "hero", "block", "sprite.png", extend_palette=True, timestamp="t"
    )
    assert result.added_colors == ["import_ff0000"]
    assert result.unmatched == {}
    assert result.matched == 2  # both pixels match once the palette is extended

    doc = api.get_asset(root, "hero")
    assert {c.id for c in doc.palette.colors} == {"ink", "import_ff0000"}


def test_import_region_extend_palette_exceeding_limit_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    spec = api.get_asset(root, "hero").model_dump(mode="json")
    spec["validation"]["palette_limit"] = 1  # already at capacity with just "ink"
    api.update_asset_spec(root, "hero", spec, timestamp="t")

    png_path = root / "sprite.png"
    _write_png(png_path, {(0, 0): (255, 0, 0, 255)}, (1, 1))

    with pytest.raises(ForgeError, match="palette_limit of 1"):
        api.import_region(root, "hero", "block", "sprite.png", extend_palette=True, timestamp="t")


# --- unmatched pixels ------------------------------------------------------------------------


def test_import_region_unmatched_pixels_reported_without_snap_or_extend(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    # 3 pixels match "ink" exactly, 1 does not -- 25% unmatched, below the raise threshold.
    _write_png(
        png_path,
        {
            (0, 0): _INK_RGBA,
            (1, 0): _INK_RGBA,
            (0, 1): _INK_RGBA,
            (1, 1): (255, 0, 0, 255),
        },
        (2, 2),
    )

    result = api.import_region(root, "hero", "block", "sprite.png", timestamp="t")
    assert result.matched == 3
    assert result.unmatched == {"#ff0000": 1}
    assert result.added_colors == []


def test_import_region_mostly_unmatched_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    # Only 1 of 4 pixels matches the palette -- 75% unmatched, over the raise threshold.
    _write_png(
        png_path,
        {
            (0, 0): _INK_RGBA,
            (1, 0): (255, 0, 0, 255),
            (0, 1): (0, 255, 0, 255),
            (1, 1): (0, 0, 255, 255),
        },
        (2, 2),
    )

    with pytest.raises(ForgeError, match="no palette colour"):
        api.import_region(root, "hero", "block", "sprite.png", timestamp="t")


# --- path safety -----------------------------------------------------------------------------


def test_import_region_hostile_png_path_raises_path_security_error(tmp_path: Path) -> None:
    root = _init(tmp_path)
    outside = tmp_path / "outside.png"
    _solid_matching_png(outside)

    with pytest.raises(PathSecurityError):
        api.import_region(root, "hero", "block", "../outside.png", timestamp="t")
    with pytest.raises(PathSecurityError):
        api.import_region(root, "hero", "block", str(outside), timestamp="t")


# --- dry run / revisions -----------------------------------------------------------------------


def test_import_region_dry_run_leaves_spec_untouched(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)
    spec_path = root / "assets" / "hero" / "hero.yaml"
    before = spec_path.read_bytes()

    result = api.import_region(root, "hero", "block", "sprite.png", timestamp="t", dry_run=True)
    assert result.dry_run is True
    assert spec_path.read_bytes() == before
    assert api.list_asset_revisions(root, "hero") == []


def test_import_region_is_recorded_as_a_revision(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)

    result = api.import_region(root, "hero", "block", "sprite.png", timestamp="t")
    assert result.revision.operation.name == "replace_spec"

    revisions = api.list_asset_revisions(root, "hero")
    assert [r.revision_id for r in revisions] == [result.revision.revision_id]


# --- validation of `region` / `direction` -----------------------------------------------------


def test_import_region_unknown_region_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)

    with pytest.raises(ForgeError, match="unknown region"):
        api.import_region(root, "hero", "does_not_exist", "sprite.png", timestamp="t")


def test_import_region_direction_is_not_supported(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)

    with pytest.raises(ForgeError, match="direction"):
        api.import_region(root, "hero", "block", "sprite.png", direction="south", timestamp="t")


def test_import_region_append_keeps_existing_shapes(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "sprite.png"
    _solid_matching_png(png_path)

    before = api.get_asset(root, "hero")
    shape_count_before = len(before.regions["block"].shapes)  # type: ignore[union-attr]

    api.import_region(root, "hero", "block", "sprite.png", replace=False, timestamp="t")

    after = api.get_asset(root, "hero")
    assert len(after.regions["block"].shapes) == shape_count_before + 1  # type: ignore[union-attr]
