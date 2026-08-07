"""Style profiles distilled from reference art (Task 11), plus the deterministic
art-direction knobs for the render-polish post-processing pass."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProvenanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    role: Literal["approved", "inspiration", "palette", "animation", "rejected"]
    notes: str = ""


class StyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    perspective: str = ""
    pixel_density: str = ""
    palette_tendencies: str = ""
    outline_style: str = ""
    light_direction: str = ""
    material_treatment: str = ""
    silhouette_complexity: str = ""
    texture_density: str = ""
    animation_timing: str = ""
    shape_language: str = ""
    environmental_hierarchy: str = ""
    provenance: list[ProvenanceEntry] = Field(default_factory=list)


class ArtDirection(BaseModel):
    """Deterministic art-direction knobs for the render-polish post-processing pass
    (`pixel_forge.rendering.effects.polish_canvas`).

    Every knob is an exact integer or a literal hex string: the pass performs pure
    integer arithmetic on the input pixels, so a given spec + `ArtDirection` always
    produces byte-identical PNGs (the render backend's determinism contract).

    Defaults target the reference look distilled from CraftPix-style top-down RPG
    art: a dark (not pure black) ink outline, 2-3 tone ramps per material lit from
    the top-left, subtle ambient occlusion at inner edges, and a soft contact
    shadow under the sprite's feet.
    """

    model_config = ConfigDict(extra="forbid")

    outline_color: str = "#1a1a1f"
    """Ink outline colour as `#rrggbb`; dark charcoal rather than pure black so the
    silhouette reads as drawn ink, not as a vector stroke."""

    outline_width: int = Field(default=1, ge=0, le=3)
    """How many pixel rings of the silhouette boundary are inked (0 disables the outline)."""

    ramp_steps: int = Field(default=3, ge=2, le=7)
    """How many discrete tone levels the light factor quantises to. 3 gives the classic
    shadow/mid/highlight ramp; higher values produce a finer gradient (more banding
    risk at the same light radius)."""

    light_angle_deg: int = Field(default=315, ge=0, le=315, multiple_of=45)
    """Bearing of the light source, clockwise from screen north (0 = straight above,
    90 = right, 180 = below, 270 = left), in 45-degree steps. 315 = top-left, the
    conventional top-down RPG light. The pass maps this to an exact integer compass
    vector — no trigonometry, so results cannot differ across platforms."""

    light_radius: int = Field(default=4, ge=1, le=16)
    """How far in from a silhouette edge the lit/shadow band extends, in pixels,
    for sprites large enough to fit it. 4 reads as interior form on 32px+
    sprites: after the 1px ink outline covers the outermost ring, a ~3px lit
    band and a ~3px shadow band remain visible on each side, leaving the centre
    as the mid tone (the classic 2-3 tone ramp discipline, not a gradient wash).
    On smaller sprites the effective radius is clamped down by the render-polish
    pass (`rendering.effects._size_adaptive_radii`) to roughly one sixth of the
    sprite's interior, so a 20x16 shell gets ~2px bands with a preserved
    mid-tone centre instead of the bands swallowing the whole sprite."""

    shadow_strength: int = Field(default=56, ge=0, le=255)
    """Gate for the shadow band: when > 0, shadow-side pixels are set to their
    material ramp's dark tone(s) (never interpolated, so the result lands on the
    ramp by construction); 0 keeps the base colour on the shadow side."""

    highlight_strength: int = Field(default=26, ge=0, le=255)
    """Gate for the highlight band: when > 0, highlight-side pixels are set to
    their material ramp's light tone(s); 0 keeps the base colour on the lit side."""

    ambient_occlusion_strength: int = Field(default=26, ge=0, le=255)
    """Extra darkening for pixels near the silhouette edge / inner concavities
    (0 disables ambient occlusion)."""

    ambient_occlusion_radius: int = Field(default=3, ge=1, le=4)
    """How many pixel rings inside the silhouette the ambient occlusion reaches.
    3 lets inner concavities and the shadow-side band read as form: the
    per-ring falloff (strongest at the edge, fading inward) darkens three
    rings instead of hugging the outline."""

    ground_shadow_enabled: bool = True
    """Draw the soft contact shadow beneath the sprite's lowest opaque row."""

    ground_shadow_strength: int = Field(default=64, ge=0, le=255)
    """Max darkening of the contact shadow's darkest pixel."""

    ground_shadow_rows: int = Field(default=2, ge=0, le=8)
    """How many rows the contact shadow extends below the feet (0 disables it)."""

    @field_validator("outline_color")
    @classmethod
    def _outline_color_is_hex(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("outline_color must be a #rrggbb hex string")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise ValueError("outline_color must be a #rrggbb hex string") from exc
        return value

    @classmethod
    def default(cls) -> ArtDirection:
        """The default art direction: top-down 3/4 perspective polish. A fresh
        instance per call, so no caller ever shares a mutable default."""
        return cls()

    @classmethod
    def terrain_default(cls) -> ArtDirection:
        """The terrain-tile art direction: **no per-tile bevel, no ink
        outline, no contact shadow**.

        A terrain tile's silhouette *is* the tile, so the sprite-oriented
        polish stages would bevel it: directional edge-band shading lifts the
        tile's top/left edge and shadows its bottom/right, ambient occlusion
        darkens the whole shadow side of the interior, and the ink outline
        crushes the shared 1px grout ring to near-black — together they make
        a field read as a grid of raised 16px blocks instead of continuous
        ground. Terrain turns every one of those knobs off: shading, AO,
        outline and ground shadow are all zeroed, so every tile keeps a flat
        interior and the authored grout ring stays visible as the tile's
        sel-out edge. The light *direction* is still declared (top-left, the
        conventional top-down light) so a whole-field light treatment can
        stay globally consistent, but nothing is applied per tile.

        The render backend (`rendering.local.render_tile`) additionally
        hue-tints the ring toward the tile's dominant material (dark green
        on grass, dark brown on dirt) instead of letting the ink outline
        darken it to near-black — see `tint_tile_ring`.
        """
        return cls(
            shadow_strength=0,
            highlight_strength=0,
            ambient_occlusion_strength=0,
            outline_width=0,
            ground_shadow_enabled=False,
        )
