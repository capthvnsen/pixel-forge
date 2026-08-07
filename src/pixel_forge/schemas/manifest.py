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


class GodotAnimatedTileExport(BaseModel):
    """An animated tile as Godot consumes it: the atlas coords of each frame, in order."""

    model_config = ConfigDict(extra="forbid")

    frames: list[GodotTileCoord] = Field(default_factory=list)
    frame_duration_ms: int
    loop: bool = True


class GodotSampleMapExport(BaseModel):
    """A demo TileMapLayer: each row holds one `[col, row]` atlas coord per cell."""

    model_config = ConfigDict(extra="forbid")

    size: Vec2
    layers: dict[str, list[list[Vec2]]] = Field(default_factory=dict)


class GodotTileSetExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atlas_source: str
    tile_size: Vec2
    tiles: list[GodotTileCoord] = Field(default_factory=list)
    terrain_sets: dict[str, GodotTerrainSetExport] = Field(default_factory=dict)
    transitions: list[TransitionSpec] = Field(default_factory=list)
    # tile id -> Godot 4 peering-bit name -> terrain name. Pre-resolved from `transitions`
    # so the plugin never has to reimplement the edge-mask mapping table.
    terrain_bits: dict[str, dict[str, str]] = Field(default_factory=dict)
    animated_tiles: dict[str, GodotAnimatedTileExport] = Field(default_factory=dict)
    sample_map: GodotSampleMapExport | None = None
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


class AnimationPlayerAnimation(BaseModel):
    """Per-animation metadata for the plugin's `Animation` resources.

    `total_duration_ms` is the exact integer sum of the animation's `FrameSpec`
    durations — the true end-to-end length *including the last frame's hold*.
    The plugin sets `Animation.length` from this; without it the plugin could
    only infer the last keyframe's *start* time, silently dropping the final
    frame's duration (e.g. a 90/90/90/220ms opening is 490ms, not 270ms).
    """

    model_config = ConfigDict(extra="forbid")

    total_duration_ms: int = Field(ge=1)
    loop: bool = True


class AnimationPlayerExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracks: list[AnimationPlayerTrack] = Field(default_factory=list)
    #: animation name -> duration/loop metadata. This dict is the authoritative
    #: list of `Animation` resources to build: an animation whose regions only
    #: colour-swap (or never change) has no keyframe track, and without this
    #: entry it would silently produce no resource at all.
    animations: dict[str, AnimationPlayerAnimation] = Field(default_factory=dict)


class GodotImportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Godot 4 has no per-texture import-time filter flag — filtering is draw-time
    #: (`CanvasItem.texture_filter` / the project setting
    #: `rendering/textures/canvas_textures/default_texture_filter`). The sample
    #: project ships `default_texture_filter=0` (nearest), and the plugin sets
    #: `TEXTURE_FILTER_NEAREST` on the CanvasItems it constructs; this field exists
    #: so the manifest states the intent explicitly.
    filter: Literal["nearest"] = "nearest"
    mipmaps: bool = False
    compress_mode: Literal["lossless"] = "lossless"
    fix_alpha_border: bool = False


class GodotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1] = 1
    asset_id: str
    asset_type: AssetType
    # Hash of the source spec this manifest was built from. The Godot plugin stores it
    # alongside the generated resources so a reimport can tell whether anything changed.
    spec_hash: str = ""
    textures: dict[str, str] = Field(default_factory=dict)  # logical name -> relative path
    sprite_frames: dict[str, SpriteFramesAnimation] = Field(default_factory=dict)
    pivots: dict[str, Vec2] = Field(default_factory=dict)
    baseline_y: int | None = None
    events: dict[str, list[list[str]]] = Field(default_factory=dict)  # per-frame event lists
    tileset: GodotTileSetExport | None = None
    animation_player: AnimationPlayerExport | None = None
    #: animation name -> procedural shader spec. Reserved for future use: the
    #: plugin does not build shader materials yet — it surfaces this payload as
    #: metadata on the generated meta resource so game code can read it, and a
    #: later round may construct `ShaderMaterial` tracks from it. Today it is
    #: informational; frames are still baked, never shader-driven.
    procedural: dict[str, ProceduralAnimationSpec] = Field(default_factory=dict)
    import_settings: GodotImportSettings = Field(default_factory=GodotImportSettings)
