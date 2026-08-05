from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.errors import PaletteError, SchemaError
from pixel_forge.schemas import (
    CharacterAsset,
    Palette,
    PaletteColor,
    TerrainAsset,
    export_json_schemas,
    parse_asset_doc,
)


def _character_doc() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset": {
            "id": "engineer",
            "type": "character",
            "canvas": [64, 64],
            "baseline_y": 60,
        },
        "palette": {
            "id": "engineer_palette",
            "colors": [
                {"id": "skin", "hex": "#e8b58c"},
                {"id": "outline", "hex": "#101010"},
            ],
        },
        "directions": ["south", "north"],
        "mirror": {},
        "anchors": {"root": [0, 0]},
        "regions": {
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [
                    {"op": "pixel", "color": "skin", "at": [0, 0]},
                    {"op": "rect", "color": "outline", "at": [1, 1], "size": [2, 2]},
                ],
            }
        },
        "direction_overrides": {},
        "animations": {
            "idle": {
                "loop": True,
                "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
            }
        },
        "export": {},
        "validation": {},
    }


def _terrain_doc() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset": {
            "id": "forest",
            "type": "terrain",
            "canvas": [16, 16],
        },
        "palette": {
            "id": "terrain_palette",
            "colors": [{"id": "grass", "hex": "#3a9b3a"}],
        },
        "export": {},
        "validation": {},
        "tiles": {
            "grass_flat": {
                "size": [16, 16],
                "regions": {},
                "anchors": {},
                "terrain": "grass",
            }
        },
        "terrain_sets": {"ground": {"mode": "corners", "tiles": ["grass_flat"]}},
        "transitions": [
            {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "grass_flat", "mask": "N"}
        ],
        "animated_tiles": {},
        "sample_map": {"size": [2, 2], "layers": {"base": [["grass_flat", "grass_flat"]]}},
    }


def test_valid_character_doc_parses() -> None:
    doc = parse_asset_doc(_character_doc())
    assert isinstance(doc, CharacterAsset)
    assert doc.asset.id == "engineer"
    assert doc.kind == "character"


def test_terrain_doc_parses() -> None:
    doc = parse_asset_doc(_terrain_doc())
    assert isinstance(doc, TerrainAsset)
    assert "grass_flat" in doc.tiles
    assert doc.transitions[0].mask == "N"


def test_unknown_schema_version_raises_schema_error() -> None:
    data = _character_doc()
    data["schema_version"] = 2
    with pytest.raises(SchemaError, match="2"):
        parse_asset_doc(data)


def test_kind_type_mismatch_raises() -> None:
    # `kind` defaults to "character" on CharacterAsset; asset.type disagreeing
    # must still fail the cross-field validator even without going through
    # parse_asset_doc's injection.
    data = _character_doc()
    data["asset"]["type"] = "enemy"
    with pytest.raises(ValidationError, match="does not match"):
        CharacterAsset.model_validate(data)


def test_unknown_shape_op_raises() -> None:
    data = _character_doc()
    data["regions"]["body"]["shapes"] = [{"op": "triangle", "color": "skin", "at": [0, 0]}]
    with pytest.raises(ValidationError):
        parse_asset_doc(data)


def test_extra_field_forbidden() -> None:
    data = _character_doc()
    data["asset"]["typo_field"] = "oops"
    with pytest.raises(ValidationError):
        parse_asset_doc(data)


def test_duplicate_palette_color_ids_raise() -> None:
    with pytest.raises(ValidationError):
        Palette(
            id="p",
            colors=[
                PaletteColor(id="a", hex="#ffffff"),
                PaletteColor(id="a", hex="#000000"),
            ],
        )


def test_to_rgba_unknown_id_raises_palette_error() -> None:
    palette = Palette(id="p", colors=[PaletteColor(id="a", hex="#ff0000")])
    assert palette.to_rgba("a") == (255, 0, 0, 255)
    with pytest.raises(PaletteError):
        palette.to_rgba("missing")


def test_export_json_schemas_writes_files_and_is_byte_stable(tmp_path: Path) -> None:
    out_dir_a = tmp_path / "a"
    out_dir_b = tmp_path / "b"
    paths_a = export_json_schemas(out_dir_a)
    paths_b = export_json_schemas(out_dir_b)

    assert len(paths_a) == 9
    for path in paths_a:
        assert path.exists()
        assert path.read_text().endswith("\n")

    for path_a, path_b in zip(sorted(paths_a), sorted(paths_b), strict=True):
        assert path_a.read_bytes() == path_b.read_bytes()
