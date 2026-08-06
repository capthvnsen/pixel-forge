"""SRC001/SRC002: source-backed assets ignoring geometry the DSL would use."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.schemas import CharacterAsset, parse_asset_doc
from pixel_forge.validation.engine import RuleContext, run_validation

CANVAS = (8, 8)


def _doc(
    *,
    directions: list[str] | None = None,
    mirror: dict[str, str] | None = None,
    regions: dict[str, Any] | None = None,
    direction_overrides: dict[str, Any] | None = None,
    animations: dict[str, Any] | None = None,
) -> CharacterAsset:
    data = {
        "schema_version": 1,
        "asset": {"id": "hero", "type": "character", "canvas": list(CANVAS)},
        "palette": {"id": "p", "colors": [{"id": "red", "hex": "#ff0000"}]},
        "directions": directions or ["south"],
        "mirror": mirror or {},
        "source": {"frames_dir": "frames", "pattern": "{animation}_{direction}_{index}.png"},
        "anchors": {"root": [0, 0]},
        "regions": regions if regions is not None else {},
        "direction_overrides": direction_overrides or {},
        "animations": animations
        or {"idle": {"loop": True, "frames": [{"duration_ms": 100, "transforms": {}}]}},
        "export": {},
        "validation": {},
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, CharacterAsset)
    return doc


def _ctx(doc: CharacterAsset, asset_dir: Path | None = None) -> RuleContext:
    return RuleContext(
        doc=doc,
        palette=resolve_palette(doc.palette),
        frames={},
        resolved=resolve_frames(doc),
        tiles={},
        asset_dir=asset_dir,
    )


# ---- SRC001: source: asset declares geometry the backend ignores -----------------


def test_src001_fires_on_regions() -> None:
    doc = _doc(regions={"body": {"anchor": "root", "layer": 0, "shapes": []}})
    report = run_validation(_ctx(doc), only=["SRC001"])
    assert len(report.findings) == 1
    assert report.findings[0].region == "body"
    assert "regions" in report.findings[0].message


_BODY_REGION = {"body": {"anchor": "root", "layer": 0, "shapes": []}}


def test_src001_fires_on_direction_overrides() -> None:
    # direction_overrides must reference a real region, so `regions` is non-empty too
    # here -- filter down to the direction_overrides finding specifically.
    doc = _doc(regions=_BODY_REGION, direction_overrides={"south": {"body": {"offset": [1, 0]}}})
    report = run_validation(_ctx(doc), only=["SRC001"])
    findings = [f for f in report.findings if f.measurements["kind"] == "direction_overrides"]
    assert len(findings) == 1
    assert findings[0].direction == "south"
    assert findings[0].region == "body"
    assert "direction_overrides" in findings[0].message


def test_src001_fires_on_frame_transforms() -> None:
    # frame transforms must reference a real region too -- same filtering as above.
    doc = _doc(
        regions=_BODY_REGION,
        animations={
            "idle": {
                "loop": True,
                "frames": [{"duration_ms": 100, "transforms": {"body": {"offset": [1, 0]}}}],
            }
        },
    )
    report = run_validation(_ctx(doc), only=["SRC001"])
    findings = [f for f in report.findings if f.measurements["kind"] == "transforms"]
    assert len(findings) == 1
    assert findings[0].animation == "idle"
    assert findings[0].frame == 0
    assert findings[0].region == "body"
    assert "transforms" in findings[0].message


def test_src001_does_not_fire_on_a_clean_source_asset() -> None:
    doc = _doc()
    report = run_validation(_ctx(doc), only=["SRC001"])
    assert report.findings == []


# ---- SRC002: a mirrored direction also has its own file on disk ------------------


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", CANVAS, (0, 0, 0, 0)).save(path)


def test_src002_fires_when_a_mirrored_direction_has_its_own_file(tmp_path: Path) -> None:
    doc = _doc(directions=["e", "w"], mirror={"w": "e"})
    _png(tmp_path / "frames" / "idle_w_0.png")
    report = run_validation(_ctx(doc, asset_dir=tmp_path), only=["SRC002"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.direction == "w"
    assert finding.animation == "idle"
    assert finding.frame == 0
    assert "idle_w_0.png" in finding.message


def test_src002_does_not_fire_when_no_stray_file_exists(tmp_path: Path) -> None:
    doc = _doc(directions=["e", "w"], mirror={"w": "e"})
    report = run_validation(_ctx(doc, asset_dir=tmp_path), only=["SRC002"])
    assert report.findings == []


def test_src002_does_not_fire_without_mirroring(tmp_path: Path) -> None:
    doc = _doc(directions=["e"])
    _png(tmp_path / "frames" / "idle_e_0.png")
    report = run_validation(_ctx(doc, asset_dir=tmp_path), only=["SRC002"])
    assert report.findings == []
