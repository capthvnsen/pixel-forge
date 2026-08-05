"""Asset documents: the top-level spec pydantic parses from an asset's YAML file."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from pixel_forge.errors import SchemaError
from pixel_forge.schemas.animation import AnimationSpec
from pixel_forge.schemas.common import Region, RegionTransform, Vec2
from pixel_forge.schemas.palette import PaletteRef
from pixel_forge.schemas.source import ExternalSource

AssetType = Literal["character", "enemy", "prop", "terrain"]


class AssetHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: AssetType
    canvas: Vec2
    perspective: str = "three_quarter_top_down"
    logical_pixel_scale: int = 1
    baseline_y: int | None = None


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_columns: int | None = None
    preview_format: Literal["gif", "webp"] = "gif"
    preview_loop: bool = True
    godot: bool = True


class ValidationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    palette_limit: int = 24
    require_stable_baseline: bool = True
    require_stable_anchors: bool = True
    allow_antialiasing: bool = False
    max_seam_mismatch: int = 0
    max_repeat_ratio: float = 0.6


class BaseAssetDoc(BaseModel):
    """Fields shared by every asset kind.

    `kind` mirrors `asset.type` at the top level. Pydantic's discriminated-union
    machinery can only discriminate on a field of the model being validated, not on
    a field nested inside `asset`, so `kind` exists purely to give the `AssetDoc`
    union something to switch on. A model validator below enforces that the two
    always agree; callers should use `parse_asset_doc`, which populates `kind` from
    `asset.type` automatically so hand-authored YAML never needs to specify it.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    kind: AssetType
    asset: AssetHeader
    palette: PaletteRef
    export: ExportOptions
    validation: ValidationOptions

    @model_validator(mode="after")
    def _check_kind_matches_asset_type(self) -> BaseAssetDoc:
        if self.kind != self.asset.type:
            raise ValueError(f"kind {self.kind!r} does not match asset.type {self.asset.type!r}")
        return self


class SpriteAssetBase(BaseAssetDoc):
    """Shared shape for the three directional/animated sprite asset kinds.

    `source` is optional and mutually exclusive with drawing: when it is set the
    pixels come from pinned PNGs (`rendering.external.ExternalFrameBackend`) and
    `regions` is expected to be empty, since nothing composites it. Everything else
    on the document -- directions, mirroring, animations, anchors, palette -- means
    exactly the same thing either way.
    """

    directions: list[str]
    source: ExternalSource | None = None
    mirror: dict[str, str] = Field(default_factory=dict)  # dst direction -> src direction
    anchors: dict[str, Vec2]
    regions: dict[str, Region]
    direction_overrides: dict[str, dict[str, RegionTransform]] = Field(default_factory=dict)
    animations: dict[str, AnimationSpec]


class CharacterAsset(SpriteAssetBase):
    kind: Literal["character"] = "character"


class EnemyCombat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegraph_animation: str | None = None
    death_animation: str | None = None
    hit_frames: dict[str, list[int]] = Field(default_factory=dict)


class EnemyAsset(SpriteAssetBase):
    kind: Literal["enemy"] = "enemy"
    combat: EnemyCombat


class PropAsset(SpriteAssetBase):
    kind: Literal["prop"] = "prop"
    moving_regions: list[str] = Field(default_factory=list)
    procedural_regions: list[str] = Field(default_factory=list)


class TileSpec(BaseModel):
    """A single terrain tile, built from the same Region+shape DSL sprites use."""

    model_config = ConfigDict(extra="forbid")

    size: Vec2
    regions: dict[str, Region] = Field(default_factory=dict)
    anchors: dict[str, Vec2] = Field(default_factory=dict)
    terrain: str | None = None
    collision: str | None = None
    navigation: bool = False
    occlusion: bool = False


class TerrainSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["corners", "edges", "corners_and_edges"] = "corners_and_edges"
    tiles: list[str] = Field(default_factory=list)  # member tile ids


class TransitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_terrain: str
    to_terrain: str
    tile_id: str
    mask: str  # edge/corner code, e.g. "N", "NE", "NW"


class AnimatedTileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames: list[str]  # ordered tile ids to cycle through
    frame_duration_ms: int = 200
    loop: bool = True

    @model_validator(mode="after")
    def _check_frames(self) -> AnimatedTileSpec:
        if len(self.frames) < 1:
            raise ValueError("an animated tile must have at least 1 frame")
        if self.frame_duration_ms <= 0:
            raise ValueError(f"frame_duration_ms must be > 0, got {self.frame_duration_ms}")
        return self


class SampleMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: Vec2
    layers: dict[str, list[list[str]]] = Field(default_factory=dict)  # rows of tile ids


class TerrainAsset(BaseAssetDoc):
    """Terrain assets are tile-based rather than region/animation-based."""

    kind: Literal["terrain"] = "terrain"
    tiles: dict[str, TileSpec]
    terrain_sets: dict[str, TerrainSet] = Field(default_factory=dict)
    transitions: list[TransitionSpec] = Field(default_factory=list)
    animated_tiles: dict[str, AnimatedTileSpec] = Field(default_factory=dict)
    sample_map: SampleMap | None = None


AssetDocUnion = CharacterAsset | EnemyAsset | PropAsset | TerrainAsset
AssetDoc = Annotated[AssetDocUnion, Field(discriminator="kind")]

_asset_doc_adapter: TypeAdapter[AssetDocUnion] = TypeAdapter(AssetDoc)


def parse_asset_doc(data: dict[str, Any]) -> AssetDocUnion:
    """Parse a raw dict (as loaded from YAML) into the correct `AssetDoc` variant.

    Validates `schema_version == 1` up front (raising `SchemaError` with the
    offending version otherwise), then injects a top-level `kind` field mirroring
    `asset.type` so the discriminated union can select the right model — see
    `BaseAssetDoc` for why that indirection exists.
    """
    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise SchemaError(f"unsupported schema_version: {schema_version!r} (expected 1)")
    asset = data.get("asset")
    if not isinstance(asset, dict) or "type" not in asset:
        raise SchemaError("asset document is missing 'asset.type'")
    payload = {**data, "kind": asset["type"]}
    return _asset_doc_adapter.validate_python(payload)
