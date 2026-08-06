"""Integration tests for `api.import_sheet`: slicing a grid sheet into a new
`source:`-backed asset, writing frames, pinning them, and validating end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge import api
from pixel_forge.errors import ForgeError, PathSecurityError

BG = (30, 30, 30, 255)
FG = (255, 0, 0, 255)
CELL = 10


def _fill_rect(img: Image.Image, x0: int, y0: int, w: int, h: int, rgba: tuple[int, ...]) -> None:
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            img.putpixel((x, y), rgba)


def _compass8_sheet() -> Image.Image:
    img = Image.new("RGBA", (CELL * 3, CELL * 3), BG)
    layout = [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 2),
        (2, 0),
        (2, 1),
        (2, 2),
    ]
    for row, col in layout:
        _fill_rect(img, col * CELL + 3, row * CELL + 6, 4, 4, FG)
    return img


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    api.init_project(root, name)
    return root


def _write_sheet(root: Path, name: str = "sheet.png") -> None:
    _compass8_sheet().save(root / name)


# --- happy path: writes frames, pins them, validates and renders cleanly --------------------


def test_import_sheet_writes_a_pinned_asset_that_renders_with_zero_blocking_findings(
    tmp_path: Path,
) -> None:
    root = _init(tmp_path)
    _write_sheet(root)

    result = api.import_sheet(
        root, "trooper", "sheet.png", grid=(3, 3), layout="compass8", canvas=20, baseline=15
    )
    assert result.asset_id == "trooper"
    assert result.directions == [
        "south_west",
        "south",
        "south_east",
        "west",
        "east",
        "north_west",
        "north",
        "north_east",
    ]
    assert result.cells_total == 9
    assert result.cells_skipped == 1
    assert result.canvas == 20
    assert result.baseline == 15
    assert not result.dry_run
    assert len(result.frame_paths) == 8
    for rel in result.frame_paths:
        assert (root / rel).is_file()

    doc = api.get_asset(root, "trooper")
    assert doc.source is not None
    assert set(doc.source.pins) == {
        f"idle_{d}_0"
        for d in (
            "south_west",
            "south",
            "south_east",
            "west",
            "east",
            "north_west",
            "north",
            "north_east",
        )
    }

    report = api.validate_asset(root, "trooper")
    assert not report.blocking, [f.message for f in report.findings if f.severity == "error"]

    render = api.render_asset(root, "trooper")
    assert render.frames_written == 8
    assert (root / render.sheet_path).is_file()


def test_import_sheet_pins_verify_against_the_written_files(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _write_sheet(root)
    api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8")
    # A second render must succeed and be cached, proving the pins match the files
    # slice_sheet actually wrote (compute_source_pins hashed the real files on disk).
    first = api.render_asset(root, "trooper")
    assert not first.skipped
    second = api.render_asset(root, "trooper")
    assert second.skipped


# --- dry run --------------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _write_sheet(root)

    result = api.import_sheet(
        root, "trooper", "sheet.png", grid=(3, 3), layout="compass8", dry_run=True
    )
    assert result.dry_run
    assert len(result.frame_paths) == 8
    assert not (root / "assets" / "trooper").exists()
    assert api.list_assets(root) == []


# --- path safety ------------------------------------------------------------------------------


def test_sheet_path_outside_the_project_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    outside = tmp_path / "outside.png"
    _compass8_sheet().save(outside)
    with pytest.raises(PathSecurityError):
        api.import_sheet(root, "trooper", "../outside.png", grid=(3, 3), layout="compass8")


# --- replace ------------------------------------------------------------------------------------


def test_refuses_to_overwrite_an_existing_asset_without_replace(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _write_sheet(root)
    api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8")
    with pytest.raises(ForgeError, match="already exists"):
        api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8")


def test_replace_true_overwrites_an_existing_asset(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _write_sheet(root)
    api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8")
    result = api.import_sheet(
        root, "trooper", "sheet.png", grid=(3, 3), layout="compass8", replace=True
    )
    assert not result.dry_run
    assert len(result.directions) == 8


# --- determinism ------------------------------------------------------------------------------


def test_importing_the_same_sheet_twice_is_byte_identical(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _write_sheet(root)
    frames_dir = root / "assets" / "trooper" / "frames"
    api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8")
    first_frames = {p.name: p.read_bytes() for p in frames_dir.iterdir()}
    first_spec = (root / "assets" / "trooper" / "trooper.yaml").read_bytes()

    api.import_sheet(root, "trooper", "sheet.png", grid=(3, 3), layout="compass8", replace=True)
    second_frames = {p.name: p.read_bytes() for p in frames_dir.iterdir()}
    second_spec = (root / "assets" / "trooper" / "trooper.yaml").read_bytes()

    assert first_frames == second_frames
    assert first_spec == second_spec
