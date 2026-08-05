"""Starter asset-spec templates for `api.new_asset`.

Every template is deliberately minimal but must render and pass `validate_asset`
with zero blocking findings — see `tests/integration/test_api.py` for the assertion.
Sprite templates use a single anchor/region/direction and a 2-frame idle loop with
no offsets, which keeps every animation-integrity rule (`ANI00x`) a structural
no-op: nothing moves between frames, so drift checks have nothing to flag, and the
anchor is deliberately not named `feet`/`*shadow*`/`*body*` so the foot-anchor
stability check (`ANI002`) doesn't even engage. `asset.baseline_y` is left unset so
the baseline-drift check (`ANI001`) is skipped rather than requiring the shape to
land on a specific row. Terrain tiles are left empty (no regions), which trivially
satisfies the seam check (`TIL003`): a blank tile tiles seamlessly against itself.
"""

from __future__ import annotations

from typing import Any

from pixel_forge.schemas import AssetType


def asset_template(asset_type: AssetType, asset_id: str) -> dict[str, Any]:
    if asset_type == "terrain":
        return _terrain_template(asset_id)
    return _sprite_template(asset_type, asset_id)


def _sprite_template(asset_type: AssetType, asset_id: str) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": 1,
        "asset": {"id": asset_id, "type": asset_type, "canvas": [32, 32]},
        "palette": {"id": "starter", "colors": [{"id": "ink", "hex": "#202020"}]},
        "directions": ["south"],
        "anchors": {"root": [16, 16]},
        "regions": {
            "block": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "ink", "at": [-4, -4], "size": [8, 8]}],
            }
        },
        "animations": {
            "idle": {
                "loop": True,
                "frames": [{"duration_ms": 200}, {"duration_ms": 200}],
            }
        },
        "export": {},
        "validation": {},
    }
    if asset_type == "enemy":
        doc["combat"] = {}
    return doc


def _terrain_template(asset_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset": {"id": asset_id, "type": "terrain", "canvas": [16, 16]},
        "palette": {"id": "starter", "colors": [{"id": "ink", "hex": "#202020"}]},
        "tiles": {
            "tile_a": {"size": [16, 16], "regions": {}, "anchors": {}},
            "tile_b": {"size": [16, 16], "regions": {}, "anchors": {}},
        },
        "export": {},
        "validation": {},
    }
