"""Integration tests for `api.import_layered`: the layered-art importer.

Covers spec synthesis (regions/anchors/palette), the byte-exact round-trip of
the composited front view, back-layer storage as hidden `back_*` regions,
input validation, dry-run, determinism, and the revision log. Also owns the
checked-in fixture PNGs under `tests/fixtures/layered/` (regenerated on every
run by the autouse session fixture, so they always exist on disk) and the
preview artifacts under `.progress/pieces/importer/` for critic review.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_forge import api
from pixel_forge.animation.cycles import _discover_roles, generate_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import ForgeError, PathSecurityError
from pixel_forge.rendering import render_asset_frames
from pixel_forge.rendering.direction import project_directions

TS = "2026-08-06T00:00:00Z"

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "layered"
_PREVIEW_DIR = Path(__file__).resolve().parent.parent.parent / ".progress" / "pieces" / "importer"

_SIZE = (24, 32)
SKIN = (240, 200, 150, 255)
SHIRT = (60, 140, 80, 255)
PANTS = (60, 80, 180, 255)
HAIR = (110, 70, 40, 255)
EYE = (30, 30, 40, 255)
BLADE = (200, 200, 210, 255)
HILT = (130, 90, 50, 255)
SHADOW = (20, 20, 30, 255)

# Draw order, bottom to top — must mirror api._FRONT_LAYER_Z.
_FRONT_ORDER = (
    "shadow",
    "leg_left",
    "leg_right",
    "torso",
    "arm_left",
    "arm_right",
    "head",
    "face",
    "hair",
    "weapon",
)


def _fill(img: Image.Image, x0: int, y0: int, x1: int, y1: int, rgba: tuple[int, ...]) -> None:
    """Fill the half-open rect [x0, x1) x [y0, y1)."""
    for y in range(y0, y1):
        for x in range(x0, x1):
            img.putpixel((x, y), rgba)


def _blank() -> Image.Image:
    return Image.new("RGBA", _SIZE, (0, 0, 0, 0))


def draw_front_layers() -> dict[str, Image.Image]:
    """A ~24x32 front-view character, one RGBA PNG-sized layer per body part.

    Every layer is a full-canvas image with the part drawn at its final
    position, the way a drawing app exports layers. Geometry (half-open):
    hair x8-15 y3-5; head x8-15 y4-13; face (front-only eyes) at (10, 8)/(13, 8);
    torso x8-15 y14-23; arms x5-7 / x16-18 y15-23 (hands y22-23);
    legs x9-10 / x13-14 y24-29; weapon x18-21 y12-24; shadow x6-17 y30-31.
    """
    layers = {name: _blank() for name in _FRONT_ORDER}

    _fill(layers["head"], 8, 4, 16, 14, SKIN)

    face = layers["face"]
    face.putpixel((10, 8), EYE)
    face.putpixel((13, 8), EYE)

    _fill(layers["hair"], 8, 3, 16, 6, HAIR)
    _fill(layers["torso"], 8, 14, 16, 24, SHIRT)

    for name, x0 in (("arm_left", 5), ("arm_right", 16)):
        _fill(layers[name], x0, 15, x0 + 3, 22, SHIRT)
        _fill(layers[name], x0, 22, x0 + 3, 24, SKIN)

    for name, x0 in (("leg_left", 9), ("leg_right", 13)):
        _fill(layers[name], x0, 24, x0 + 2, 30, PANTS)

    weapon = layers["weapon"]
    _fill(weapon, 19, 12, 21, 24, BLADE)
    _fill(weapon, 18, 24, 22, 25, HILT)

    _fill(layers["shadow"], 6, 30, 18, 32, SHADOW)
    return layers


def draw_back_layers() -> dict[str, Image.Image]:
    """The matching back view: same silhouette, no eyes, hair covers the back
    of the head, no weapon or shadow (both are front-only here on purpose, to
    exercise partially-supplied optional layers)."""
    layers = {
        name: _blank()
        for name in ("torso", "head", "arm_left", "arm_right", "leg_left", "leg_right", "hair")
    }
    _fill(layers["head"], 8, 4, 16, 14, SKIN)
    _fill(layers["hair"], 8, 3, 16, 8, HAIR)
    _fill(layers["torso"], 8, 14, 16, 24, SHIRT)
    for name, x0 in (("arm_left", 5), ("arm_right", 16)):
        _fill(layers[name], x0, 15, x0 + 3, 22, SHIRT)
        _fill(layers[name], x0, 22, x0 + 3, 24, SKIN)
    for name, x0 in (("leg_left", 9), ("leg_right", 13)):
        _fill(layers[name], x0, 24, x0 + 2, 30, PANTS)
    return layers


def ensure_layered_fixtures(fixture_dir: Path = _FIXTURE_DIR) -> Path:
    """(Re)write the checked-in fixture PNGs. Deterministic and idempotent, so
    running the tests always leaves the fixtures on disk for review."""
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for name, img in draw_front_layers().items():
        img.save(fixture_dir / f"front_{name}.png")
    for name, img in draw_back_layers().items():
        img.save(fixture_dir / f"back_{name}.png")
    return fixture_dir


@pytest.fixture(scope="session", autouse=True)
def layered_fixtures() -> Path:
    return ensure_layered_fixtures()


def _reference_composite(
    layers: dict[str, Image.Image], canvas: tuple[int, int], offset: tuple[int, int]
) -> np.ndarray:
    """Painter's-algorithm composite in draw order with binary alpha — the
    renderer's exact semantics, re-derived independently of api.py."""
    dx, dy = offset
    out = np.zeros((canvas[1], canvas[0], 4), dtype=np.uint8)
    for name in _FRONT_ORDER:
        if name not in layers:
            continue
        arr = np.array(layers[name], dtype=np.uint8)
        mask = arr[..., 3] >= 128
        x0, y0 = max(dx, 0), max(dy, 0)
        x1 = min(dx + arr.shape[1], canvas[0])
        y1 = min(dy + arr.shape[0], canvas[1])
        sub = mask[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
        target = out[y0:y1, x0:x1]
        src = arr[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
        target[sub] = src[sub]
        target[..., 3] = np.where(sub, 255, target[..., 3])
    return out


def _front_union_offset() -> tuple[tuple[int, int], tuple[int, int]]:
    """(canvas, offset) the importer derives for the fixture geometry."""
    return (17, 29), (-5, -3)


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    api.init_project(root, name)
    return root


def _stage(
    root: Path,
    front: dict[str, Image.Image] | None = None,
    back: dict[str, Image.Image] | None = None,
) -> tuple[dict[str, Path], dict[str, Path] | None]:
    """Write layer PNGs into the project and return relative-path maps."""
    layer_dir = root / "layers"
    layer_dir.mkdir(exist_ok=True)
    front = draw_front_layers() if front is None else front
    front_paths = {}
    for name, img in front.items():
        img.save(layer_dir / f"front_{name}.png")
        front_paths[name] = Path(f"layers/front_{name}.png")
    back_paths = None
    if back is not None:
        back_paths = {}
        for name, img in back.items():
            img.save(layer_dir / f"back_{name}.png")
            back_paths[name] = Path(f"layers/back_{name}.png")
    return front_paths, back_paths


# --- happy path: spec contents -----------------------------------------------------------------


def test_import_layered_builds_the_expected_spec(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)

    result = api.import_layered(root, "hero", front, timestamp=TS)

    assert result.asset_id == "hero"
    assert result.canvas == (17, 29)
    assert result.regions == list(_FRONT_ORDER)
    assert result.back_regions == []
    assert result.palette_id == "hero_palette"
    assert result.palette_size == 8
    assert result.spec_path == "assets/hero/hero.yaml"
    assert result.revision is not None
    assert not result.dry_run
    # Optional layers were all supplied, the canvas is small, the palette fits.
    assert result.warnings == []


def test_face_layer_becomes_a_region_and_is_stripped_from_back_views(
    tmp_path: Path,
) -> None:
    """An optional `face` layer imports as a region; the direction projection
    classifies it as face detail (name token `face`) and strips it from
    back-facing views while the front keeps it."""
    root = _init(tmp_path)
    front, _ = _stage(root)

    result = api.import_layered(root, "hero", front, timestamp=TS)
    assert "face" in result.regions
    assert result.warnings == []

    doc = api.get_asset(root, "hero")
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    eye_rgba = EYE  # EYE is already the RGBA (30, 30, 40, 255)

    def has_eye_px(c) -> bool:
        a = c.array
        for y in range(a.shape[0]):
            for x in range(a.shape[1]):
                if tuple(a[y, x]) == eye_rgba:
                    return True
        return False

    assert has_eye_px(views["south"].composite(doc.asset.canvas))  # front keeps face
    assert not has_eye_px(views["north"].composite(doc.asset.canvas))  # back strips it
    assert not has_eye_px(views["north_east"].composite(doc.asset.canvas))

    doc = api.get_asset(root, "hero")
    assert doc.export.polish is False
    assert doc.directions == ["south"]
    # Baseline is the lowest opaque row of the rendered frame — the bottom of
    # the front union, which includes the ground shadow (its bottom row is at
    # source y=31, shifted by dy=-3 -> 28). ANI001 measures exactly this row,
    # so declaring the feet line (26) would be a blocking baseline drift.
    assert doc.asset.baseline_y == 28
    assert set(doc.regions) == set(_FRONT_ORDER)
    assert doc.regions["shadow"].layer < doc.regions["leg_left"].layer
    assert doc.regions["leg_right"].layer < doc.regions["torso"].layer
    assert doc.regions["torso"].layer < doc.regions["arm_left"].layer
    assert doc.regions["arm_right"].layer < doc.regions["head"].layer
    assert doc.regions["head"].layer < doc.regions["hair"].layer
    assert doc.regions["hair"].layer < doc.regions["weapon"].layer
    for name in _FRONT_ORDER:
        shapes = doc.regions[name].shapes
        assert len(shapes) == 1 and shapes[0].op == "bitmap"

    report = api.validate_asset(root, "hero")
    assert not report.blocking, [f.message for f in report.findings if f.severity == "error"]


def test_anchors_are_derived_from_the_layer_bboxes(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    result = api.import_layered(root, "hero", front, timestamp=TS)
    assert result.anchors == {
        "root": (7, 16),
        "feet": (7, 26),
        "head_top": (7, 1),
        "shoulder_left": (2, 12),
        "shoulder_right": (11, 12),
        "hip_left": (5, 21),
        "hip_right": (9, 21),
    }
    doc = api.get_asset(root, "hero")
    assert doc.anchors == result.anchors
    # Region -> anchor wiring: limbs on their joints, shadow on feet (static).
    assert doc.regions["arm_left"].anchor == "shoulder_left"
    assert doc.regions["arm_right"].anchor == "shoulder_right"
    assert doc.regions["leg_left"].anchor == "hip_left"
    assert doc.regions["leg_right"].anchor == "hip_right"
    assert doc.regions["head"].anchor == "head_top"
    assert doc.regions["hair"].anchor == "head_top"
    assert doc.regions["torso"].anchor == "root"
    assert doc.regions["weapon"].anchor == "shoulder_right"
    assert doc.regions["shadow"].anchor == "feet"


# --- round-trip fidelity -------------------------------------------------------------------------


def test_rendered_front_frame_equals_the_composited_source_layers(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    api.import_layered(root, "hero", front, timestamp=TS)

    doc = api.get_asset(root, "hero")
    frames = render_asset_frames(doc, art_direction=None)
    rendered = frames[("idle", "south", 0)].array

    canvas, offset = _front_union_offset()
    expected = _reference_composite(draw_front_layers(), canvas, offset)
    assert rendered.shape == expected.shape
    assert np.array_equal(rendered, expected), (
        f"round-trip mismatch at {np.argwhere(rendered != expected)[:10]}"
    )


def test_back_layers_are_stored_hidden_and_do_not_change_the_front(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, back = _stage(root, back=draw_back_layers())
    result = api.import_layered(root, "hero", front, back_layers=back, timestamp=TS)

    assert result.back_regions == [
        "back_arm_left",
        "back_arm_right",
        "back_leg_left",
        "back_leg_right",
        "back_torso",
        "back_head",
        "back_hair",
    ]
    doc = api.get_asset(root, "hero")
    frame = doc.animations["idle"].frames[0]
    for region in result.back_regions:
        assert frame.transforms[region].visible is False
    # Back arms/legs sit behind the back torso (see api._BACK_LAYER_Z).
    assert doc.regions["back_arm_left"].layer < doc.regions["back_torso"].layer
    assert doc.regions["back_leg_left"].layer < doc.regions["back_torso"].layer

    frames = render_asset_frames(doc, art_direction=None)
    canvas, offset = _front_union_offset()
    expected = _reference_composite(draw_front_layers(), canvas, offset)
    assert np.array_equal(frames[("idle", "south", 0)].array, expected)


# --- walk/pose machinery works on the imported asset immediately --------------------------------


def test_role_discovery_and_walk_cycle_work_on_the_import(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    api.import_layered(root, "hero", front, timestamp=TS)
    doc = api.get_asset(root, "hero")

    roles = _discover_roles(doc)
    assert roles.head == "head"
    assert roles.arm_left == "arm_left"
    assert roles.arm_right == "arm_right"
    assert roles.leg_left == "leg_left"
    assert roles.leg_right == "leg_right"
    assert "shadow" in roles.static

    frames = generate_walk_cycle(doc, {})
    assert len(frames) == 8
    moved = set().union(*(set(f.transforms) for f in frames))
    assert {"arm_left", "arm_right", "leg_left", "leg_right", "head"} <= moved
    assert "shadow" not in moved


# --- revision log ---------------------------------------------------------------------------------


def test_the_import_is_logged_as_exactly_one_revision(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    result = api.import_layered(root, "hero", front, timestamp=TS)

    revisions = api.list_asset_revisions(root, "hero")
    assert len(revisions) == 1
    record = revisions[0]
    assert record.operation.name == "replace_spec"
    assert record.timestamp == TS
    assert result.revision is not None
    assert result.revision.revision_id == record.revision_id


# --- input validation ----------------------------------------------------------------


def test_missing_required_front_layer_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    layers = draw_front_layers()
    del layers["head"]
    front, _ = _stage(root, front=layers)
    with pytest.raises(ForgeError, match="missing required front layer"):
        api.import_layered(root, "hero", front, timestamp=TS)


def test_unknown_layer_name_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    layers = draw_front_layers()
    layers["cape"] = _blank()
    front, _ = _stage(root, front=layers)
    with pytest.raises(ForgeError, match="unknown front layer name"):
        api.import_layered(root, "hero", front, timestamp=TS)


def test_unknown_back_layer_name_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    back = draw_back_layers()
    back["cape"] = _blank()
    front, back_paths = _stage(root, back=back)
    with pytest.raises(ForgeError, match="unknown back layer name"):
        api.import_layered(root, "hero", front, back_layers=back_paths, timestamp=TS)


def test_fully_transparent_layer_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    layers = draw_front_layers()
    layers["weapon"] = _blank()
    front, _ = _stage(root, front=layers)
    with pytest.raises(ForgeError, match="fully transparent"):
        api.import_layered(root, "hero", front, timestamp=TS)


def test_layer_path_outside_the_project_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    draw_front_layers()["head"].save(tmp_path / "outside.png")
    front["head"] = Path("../outside.png")
    with pytest.raises(PathSecurityError):
        api.import_layered(root, "hero", front, timestamp=TS)


def test_explicit_canvas_smaller_than_the_art_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    with pytest.raises(ForgeError, match="exceeds the explicit canvas"):
        api.import_layered(root, "hero", front, canvas=(10, 10), timestamp=TS)


# --- replace / dry-run / determinism -------------------------------------------------


def test_existing_asset_requires_replace(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    api.import_layered(root, "hero", front, timestamp=TS)
    with pytest.raises(ForgeError, match="already exists"):
        api.import_layered(root, "hero", front, timestamp=TS)
    again = api.import_layered(root, "hero", front, replace=True, timestamp=TS)
    assert again.canvas == (17, 29)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    result = api.import_layered(root, "hero", front, timestamp=TS, dry_run=True)
    assert result.dry_run
    assert result.revision is None
    assert result.canvas == (17, 29)
    assert result.regions == list(_FRONT_ORDER)
    assert not (root / "assets" / "hero").exists()
    assert api.list_assets(root) == []


def test_importing_twice_is_byte_identical(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    api.import_layered(root, "hero", front, timestamp=TS)
    spec_path = root / "assets" / "hero" / "hero.yaml"
    first = spec_path.read_bytes()
    api.import_layered(root, "hero", front, replace=True, timestamp=TS)
    assert spec_path.read_bytes() == first


# --- warnings -------------------------------------------------------------------------------------


def test_max_colors_cap_warns_and_drops_colours(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    result = api.import_layered(root, "hero", front, max_colors=2, timestamp=TS)
    assert any("unique colours" in w for w in result.warnings)
    assert result.palette_size == 2


def test_missing_optional_layers_are_reported(tmp_path: Path) -> None:
    root = _init(tmp_path)
    layers = draw_front_layers()
    for name in ("weapon", "hair", "shadow"):
        del layers[name]
    front, _ = _stage(root, front=layers)
    result = api.import_layered(root, "hero", front, timestamp=TS)
    assert result.warnings == ["optional front layer(s) not supplied: ['hair', 'shadow', 'weapon']"]


def test_oversized_canvas_warns(tmp_path: Path) -> None:
    root = _init(tmp_path)
    front, _ = _stage(root)
    result = api.import_layered(root, "hero", front, canvas=(200, 200), timestamp=TS)
    assert any("larger than 128px" in w for w in result.warnings)


# --- preview artifacts for critic review ----------------------------------------------------------


def test_preview_artifacts(tmp_path: Path, layered_fixtures: Path) -> None:
    """Regenerate the critic-facing artifacts under .progress/pieces/importer/:
    the source layers, the composited front, the engine-rendered front frame
    (x4), a diff image, and a text assertion of round-trip equality."""
    root = _init(tmp_path)
    front, _ = _stage(root)
    api.import_layered(root, "hero", front, timestamp=TS)

    doc = api.get_asset(root, "hero")
    rendered = render_asset_frames(doc, art_direction=None)[("idle", "south", 0)].array
    canvas, offset = _front_union_offset()
    expected = _reference_composite(draw_front_layers(), canvas, offset)

    out = _PREVIEW_DIR
    shutil.rmtree(out, ignore_errors=True)
    (out / "layers").mkdir(parents=True)
    for png in sorted(layered_fixtures.glob("*.png")):
        shutil.copy(png, out / "layers" / png.name)

    def save_scaled(arr: np.ndarray, path: Path) -> None:
        img = Image.fromarray(arr).resize((arr.shape[1] * 4, arr.shape[0] * 4), Image.NEAREST)
        img.save(path)

    save_scaled(expected, out / "front_composite_x4.png")
    save_scaled(rendered, out / "engine_rendered_front_x4.png")

    diff = np.abs(rendered.astype(np.int16) - expected.astype(np.int16)).sum(axis=2)
    diff_img = np.clip(diff * 16, 0, 255).astype(np.uint8)
    save_scaled(
        np.stack([diff_img] * 3 + [np.full_like(diff_img, 255)], axis=-1),
        out / "roundtrip_diff_x4.png",
    )

    equal = bool(np.array_equal(rendered, expected))
    rendered_sha = hashlib.sha256(rendered.tobytes()).hexdigest()
    expected_sha = hashlib.sha256(expected.tobytes()).hexdigest()
    verdict = (
        "EQUAL"
        if equal
        else (f"MISMATCH ({int((rendered != expected).any(axis=-1).sum())} pixels)")
    )
    (out / "roundtrip_assertion.txt").write_text(
        "round-trip: engine-rendered idle/south/0 vs source front layers "
        "composited in draw order\n"
        f"canvas: {canvas[0]}x{canvas[1]}\n"
        f"engine  sha256(rgba): {rendered_sha}\n"
        f"source  sha256(rgba): {expected_sha}\n"
        f"result: {verdict}\n"
    )
    assert equal
