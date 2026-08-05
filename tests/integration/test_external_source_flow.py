"""External-source assets end to end through `api.py`: render, pin, cache, export."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from PIL import Image

from pixel_forge import api
from pixel_forge.errors import ForgeError, RenderError

CANVAS = (16, 16)
TS = "2026-08-05T12:00:00+00:00"

SPEC = {
    "schema_version": 1,
    "asset": {"id": "hero", "type": "character", "canvas": list(CANVAS), "baseline_y": 12},
    "palette": {"id": "p", "colors": [{"id": "body", "hex": "#3a5a78"}]},
    "directions": ["e", "w"],
    "mirror": {"w": "e"},
    "source": {"frames_dir": "frames", "pattern": "{animation}_{direction}_{index}.png"},
    "anchors": {"feet": [8, 12]},
    "regions": {},
    "animations": {"idle": {"loop": True, "frames": [{"duration_ms": 120}, {"duration_ms": 120}]}},
    "export": {},
    "validation": {},
}


def _frame(path: Path, *, shift: int = 0) -> None:
    """A small blue block standing on row 12, so baseline/pivot rules have something real."""
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    for y in range(8, 13):
        for x in range(6 + shift, 10 + shift):
            img.putpixel((x, y), (58, 90, 120, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    api.init_project(root, "proj")
    asset_dir = root / "assets" / "hero"
    asset_dir.mkdir(parents=True)
    (asset_dir / "hero.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False))
    for i in range(2):
        _frame(asset_dir / "frames" / f"idle_e_{i}.png")
    return root


def test_renders_from_files_and_validates_without_regions(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = api.render_asset(root, "hero")
    assert result.frames_written == 4  # 2 directions x 2 frames
    report = api.validate_asset(root, "hero")
    assert not report.blocking, [f.message for f in report.findings if f.severity == "error"]


def test_mirrored_direction_is_produced_without_its_own_file(tmp_path: Path) -> None:
    root = _project(tmp_path)
    api.render_asset(root, "hero")
    frames = root / "build" / "hero" / "frames"
    assert (frames / "idle_w_0.png").is_file()
    assert not (root / "assets" / "hero" / "frames" / "idle_w_0.png").exists()
    east = Image.open(frames / "idle_e_0.png").convert("RGBA")
    west = Image.open(frames / "idle_w_0.png").convert("RGBA")
    assert west.transpose(Image.FLIP_LEFT_RIGHT).tobytes() == east.tobytes()


def test_pinning_records_a_revision_and_changes_the_spec_hash(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = api.inspect_asset(root, "hero").spec_hash
    record = api.pin_asset_source(root, "hero", timestamp=TS)
    assert record.operation.name == "replace_spec"
    after = api.inspect_asset(root, "hero").spec_hash
    assert before != after, "pinning must move the document hash, or caching cannot notice art"
    spec = yaml.safe_load((root / "assets" / "hero" / "hero.yaml").read_text())
    assert set(spec["source"]["pins"]) == {"idle_e_0", "idle_e_1"}


def test_replacing_art_under_a_pin_fails_the_next_render(tmp_path: Path) -> None:
    root = _project(tmp_path)
    api.pin_asset_source(root, "hero", timestamp=TS)
    _frame(root / "assets" / "hero" / "frames" / "idle_e_0.png", shift=2)
    with pytest.raises(RenderError, match="does not match its pin"):
        api.render_asset(root, "hero", force=True)


def test_repinning_accepts_new_art_and_re_renders(tmp_path: Path) -> None:
    root = _project(tmp_path)
    api.pin_asset_source(root, "hero", timestamp=TS)
    _frame(root / "assets" / "hero" / "frames" / "idle_e_0.png", shift=2)
    api.pin_asset_source(root, "hero", timestamp="2026-08-05T13:00:00+00:00")
    assert api.render_asset(root, "hero", force=True).frames_written == 4
    revisions = api.list_asset_revisions(root, "hero")
    assert [r.operation.name for r in revisions] == ["replace_spec", "replace_spec"]


def test_render_is_byte_identical_across_runs(tmp_path: Path) -> None:
    root = _project(tmp_path)
    api.pin_asset_source(root, "hero", timestamp=TS)
    sheet = root / "build" / "hero" / "hero_sheet.png"
    api.render_asset(root, "hero", force=True)
    first = sheet.read_bytes()
    api.render_asset(root, "hero", force=True)
    assert sheet.read_bytes() == first


def test_build_exports_a_godot_manifest(tmp_path: Path) -> None:
    root = _project(tmp_path)
    manifest = api.build_asset(root, "hero", force=True)
    godot = root / "build" / "godot" / "hero.forge.json"
    assert godot.is_file()
    assert manifest.output_paths.get("godot")


def test_pinning_a_shape_dsl_asset_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj2"
    api.init_project(root, "proj2")
    api.new_asset(root, "character", "drawn")
    with pytest.raises(ForgeError, match="no `source:` block"):
        api.pin_asset_source(root, "drawn", timestamp=TS)
