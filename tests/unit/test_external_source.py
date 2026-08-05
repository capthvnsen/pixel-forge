"""`ExternalFrameBackend` and the pin mechanism."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import PathSecurityError, RenderError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.external import ExternalFrameBackend, compute_source_pins
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.source import ExternalSource

CANVAS = (8, 8)


def _png(path: Path, rgb: tuple[int, int, int], *, at: tuple[int, int] = (2, 2)) -> None:
    """One opaque pixel of `rgb` at `at` on a transparent 8x8 canvas."""
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    img.putpixel(at, (*rgb, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _doc(tmp_path: Path, *, mirror: bool = False, pins: dict[str, str] | None = None) -> object:
    spec: dict = {
        "schema_version": 1,
        "asset": {"id": "hero", "type": "character", "canvas": list(CANVAS)},
        "palette": {"id": "p", "colors": [{"id": "red", "hex": "#ff0000"}]},
        "directions": ["e", "w"] if mirror else ["e"],
        "source": {"frames_dir": "frames", "pattern": "{animation}_{direction}_{index}.png"},
        "anchors": {"feet": [4, 7]},
        "regions": {},
        "animations": {"idle": {"loop": True, "frames": [{"duration_ms": 100}]}},
        "export": {},
        "validation": {},
    }
    if mirror:
        spec["mirror"] = {"w": "e"}
    if pins is not None:
        spec["source"]["pins"] = pins
    return parse_asset_doc(spec)


def _render(doc, asset_dir: Path) -> dict[tuple[str, str, int], Canvas]:
    backend = ExternalFrameBackend(asset_dir)
    palette = resolve_palette(doc.palette)
    return {
        (f.animation, f.direction, f.index): backend.render_frame(doc, f, palette)
        for f in resolve_frames(doc)
    }


# --- pattern validation ------------------------------------------------------------------------


def test_pattern_must_reference_every_frame_coordinate() -> None:
    with pytest.raises(ValueError, match=r"\{index\}"):
        ExternalSource(pattern="{animation}_{direction}.png")


def test_pattern_rejects_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        ExternalSource(pattern="{animation}_{direction}_{index}_{frame}.png")


def test_pattern_rejects_a_path_rather_than_a_filename() -> None:
    with pytest.raises(ValueError, match="bare filename"):
        ExternalSource(pattern="{animation}/{direction}_{index}.png")


# --- loading -----------------------------------------------------------------------------------


def test_renders_the_pixels_from_the_file(tmp_path: Path) -> None:
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    frames = _render(_doc(tmp_path), tmp_path)
    assert frames[("idle", "e", 0)].get_pixel(2, 2) == (255, 0, 0, 255)


def test_missing_file_names_the_expected_location(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match=r"frames/idle_e_0\.png"):
        _render(_doc(tmp_path), tmp_path)


def test_wrong_size_file_is_rejected_against_the_declared_canvas(tmp_path: Path) -> None:
    path = tmp_path / "frames" / "idle_e_0.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(path)
    with pytest.raises(RenderError, match=r"16x16.*canvas 8x8"):
        _render(_doc(tmp_path), tmp_path)


def test_frames_dir_cannot_escape_the_asset_directory(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    doc.source.frames_dir = "../../elsewhere"
    with pytest.raises(PathSecurityError):
        _render(doc, tmp_path / "assets" / "hero")


def test_a_returned_canvas_is_not_the_cached_one(tmp_path: Path) -> None:
    """Two renders of one file must not alias: mutating the first must not poison
    the cache the second reads from."""
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    doc = _doc(tmp_path)
    backend = ExternalFrameBackend(tmp_path)
    palette = resolve_palette(doc.palette)
    frame = next(iter(resolve_frames(doc)))
    first = backend.render_frame(doc, frame, palette)
    first.set_pixel(2, 2, (0, 255, 0, 255))
    second = backend.render_frame(doc, frame, palette)
    assert second.get_pixel(2, 2) == (255, 0, 0, 255)


# --- mirroring ---------------------------------------------------------------------------------


def test_a_mirrored_direction_flips_its_source_file(tmp_path: Path) -> None:
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0), at=(1, 3))
    frames = _render(_doc(tmp_path, mirror=True), tmp_path)
    assert frames[("idle", "e", 0)].get_pixel(1, 3) == (255, 0, 0, 255)
    # x' = width - 1 - x  ->  1 becomes 6 on an 8px canvas
    assert frames[("idle", "w", 0)].get_pixel(6, 3) == (255, 0, 0, 255)
    assert frames[("idle", "w", 0)].get_pixel(1, 3) == (0, 0, 0, 0)


def test_a_mirrored_direction_needs_no_file_of_its_own(tmp_path: Path) -> None:
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    assert not (tmp_path / "frames" / "idle_w_0.png").exists()
    frames = _render(_doc(tmp_path, mirror=True), tmp_path)
    assert frames[("idle", "w", 0)].opaque_count() == 1


# --- pinning -----------------------------------------------------------------------------------


def test_pins_cover_authored_frames_only(tmp_path: Path) -> None:
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    pins = compute_source_pins(_doc(tmp_path, mirror=True), tmp_path)
    assert set(pins) == {"idle_e_0"}


def test_pinning_raises_rather_than_silently_skipping_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="cannot pin"):
        compute_source_pins(_doc(tmp_path), tmp_path)


def test_a_matching_pin_renders(tmp_path: Path) -> None:
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    pins = compute_source_pins(_doc(tmp_path), tmp_path)
    frames = _render(_doc(tmp_path, pins=pins), tmp_path)
    assert frames[("idle", "e", 0)].get_pixel(2, 2) == (255, 0, 0, 255)


def test_art_changing_under_a_pin_is_a_loud_error(tmp_path: Path) -> None:
    """The whole point of pinning: pixels that change without a re-pin must not
    render silently, because the document hash would not have moved."""
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    pins = compute_source_pins(_doc(tmp_path), tmp_path)
    _png(tmp_path / "frames" / "idle_e_0.png", (0, 0, 255))
    with pytest.raises(RenderError, match="does not match its pin"):
        _render(_doc(tmp_path, pins=pins), tmp_path)


def test_an_unpinned_source_still_renders(tmp_path: Path) -> None:
    """Pinning is opt-in, so an author can iterate on art before locking it."""
    _png(tmp_path / "frames" / "idle_e_0.png", (255, 0, 0))
    frames = _render(_doc(tmp_path, pins={}), tmp_path)
    assert frames[("idle", "e", 0)].opaque_count() == 1
