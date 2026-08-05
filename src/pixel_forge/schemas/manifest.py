"""Build manifests: sprite sheet layout, per-asset build summary, Godot import payload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pixel_forge.schemas.animation import ProceduralAnimationSpec
from pixel_forge.schemas.asset import AssetType, TransitionSpec
from pixel_forge.schemas.common import Vec2

# --- sprite sheet layout ---------------------------------------------------


class SheetCellManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str
    animation: str
    index: int
    x: int
    y: int
    w: int
    h: int


class SheetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_path: str
    columns: int
    rows: int
    cell_size: Vec2
    cells: list[SheetCellManifest] = Field(default_factory=list)


# --- per-asset build summary -------------------------------------------------


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocking: bool
    error_count: int
    warning_count: int
    finding_count: int


class AssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_type: AssetType
    spec_hash: str
    output_paths: dict[str, str] = Field(default_factory=dict)  # logical name -> path
    sheet: SheetManifest | None = None
    preview_paths: dict[str, str] = Field(default_factory=dict)  # logical name -> path
    validation_summary: ValidationSummary


# --- Godot import manifest ---------------------------------------------------


class AtlasRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    w: int
    h: int


class SpriteFrameEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rect: AtlasRect
    duration_ms: int


class SpriteFramesAnimation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop: bool
    frames: list[SpriteFrameEntry]


class GodotTileCoord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tile_id: str
    x: int
    y: int


class GodotTerrainSetExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["corners", "edges", "corners_and_edges"]
    tiles: list[str] = Field(default_factory=list)


class GodotTileSetExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atlas_source: str
    tile_size: Vec2
    tiles: list[GodotTileCoord] = Field(default_factory=list)
    terrain_sets: dict[str, GodotTerrainSetExport] = Field(default_factory=dict)
    transitions: list[TransitionSpec] = Field(default_factory=list)
    collision_tiles: list[str] = Field(default_factory=list)
    navigation_tiles: list[str] = Field(default_factory=list)
    occlusion_tiles: list[str] = Field(default_factory=list)


class AnimationKeyframe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_ms: int
    value: float | int | str | bool | Vec2


class AnimationPlayerTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_path: str
    property: str
    keyframes: list[AnimationKeyframe] = Field(default_factory=list)


class AnimationPlayerExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracks: list[AnimationPlayerTrack] = Field(default_factory=list)


class GodotImportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter: Literal["nearest"] = "nearest"
    mipmaps: bool = False


class GodotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1] = 1
    asset_id: str
    asset_type: AssetType
    textures: dict[str, str] = Field(default_factory=dict)  # logical name -> relative path
    sprite_frames: dict[str, SpriteFramesAnimation] = Field(default_factory=dict)
    pivots: dict[str, Vec2] = Field(default_factory=dict)
    baseline_y: int | None = None
    events: dict[str, list[list[str]]] = Field(default_factory=dict)  # per-frame event lists
    tileset: GodotTileSetExport | None = None
    animation_player: AnimationPlayerExport | None = None
    procedural: dict[str, ProceduralAnimationSpec] = Field(default_factory=dict)
    import_settings: GodotImportSettings = Field(default_factory=GodotImportSettings)
