"""Palette resolution: colour-id lookup, nearest-colour matching, limit checks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pixel_forge.errors import PaletteError
from pixel_forge.schemas.common import RGBA
from pixel_forge.schemas.palette import Palette, PaletteColor

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def hex_to_rgba(hex_str: str) -> RGBA:
    if not _HEX_RE.match(hex_str):
        raise PaletteError(f"invalid hex colour: {hex_str!r}")
    value = hex_str[1:]
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    a = int(value[6:8], 16) if len(value) == 8 else 255
    return (r, g, b, a)


def rgba_to_hex(rgba: RGBA) -> str:
    r, g, b, a = rgba
    hex_str = f"#{r:02x}{g:02x}{b:02x}"
    if a != 255:
        hex_str += f"{a:02x}"
    return hex_str


@dataclass(frozen=True)
class ResolvedPalette:
    """A `Palette` with colour lookups resolved to concrete RGBA values."""

    palette: Palette

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(color.id for color in self.palette.colors)

    @property
    def size(self) -> int:
        return len(self.palette.colors)

    def rgba(self, color_id: str) -> RGBA:
        color = self.palette.by_id.get(color_id)
        if color is None:
            raise PaletteError(
                f"unknown palette color id {color_id!r} in palette {self.palette.id!r}; "
                f"valid ids: {', '.join(self.ids)}"
            )
        return hex_to_rgba(color.hex)

    def contains_rgba(self, rgba: RGBA) -> bool:
        return any(hex_to_rgba(color.hex) == rgba for color in self.palette.colors)

    def nearest(self, rgba: RGBA) -> str:
        """Nearest colour by squared-RGB distance. Ties keep the earlier declared id."""
        if not self.palette.colors:
            raise PaletteError(f"palette {self.palette.id!r} has no colors")
        r, g, b = rgba[0], rgba[1], rgba[2]
        best_id = self.palette.colors[0].id
        best_dist: int | None = None
        for color in self.palette.colors:
            cr, cg, cb, _ = hex_to_rgba(color.hex)
            dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_id = color.id
        return best_id


def resolve_palette(palette: Palette) -> ResolvedPalette:
    return ResolvedPalette(palette=palette)


def check_palette_limit(palette: Palette, limit: int) -> list[str]:
    """Colour ids beyond `limit` (declaration order), empty when within limit."""
    return [color.id for color in palette.colors[limit:]]


# --- HSL colour math (deterministic, integer-consistent) ---------------------------
#
# All ramp/outline functions are pure functions of hex strings: same input, same
# output, no randomness, no timestamps, no float-vs-int platform variance that
# could leak into a rendered pixel. HSL keeps hue stable across a ramp's value
# steps, which is the professional-palette discipline this module encodes.


def rgb_to_hsl(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert an sRGB triple to (hue_deg, saturation, lightness), each in [0, 1]
    for s/l and [0, 360) for hue."""
    r, g, b = (v / 255.0 for v in rgb)
    max_c, min_c = max(r, g, b), min(r, g, b)
    lightness = (max_c + min_c) / 2.0
    delta = max_c - min_c
    if delta == 0.0:
        return (0.0, 0.0, lightness)
    saturation = (
        delta / (2.0 - max_c - min_c) if lightness > 0.5 else delta / (max_c + min_c)
    )
    if max_c == r:
        hue = (g - b) / delta + (6.0 if g < b else 0.0)
    elif max_c == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return (hue * 60.0, saturation, lightness)


def hsl_to_rgb(hue: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    """Inverse of `rgb_to_hsl`; the sRGB triple is rounded to integer channels."""
    h = hue % 360.0
    c = (1.0 - abs(2.0 * lightness - 1.0)) * saturation
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = lightness - c / 2.0
    if h < 60.0:
        r, g, b = c, x, 0.0
    elif h < 120.0:
        r, g, b = x, c, 0.0
    elif h < 180.0:
        r, g, b = 0.0, c, x
    elif h < 240.0:
        r, g, b = 0.0, x, c
    elif h < 300.0:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (round((r + m) * 255.0), round((g + m) * 255.0), round((b + m) * 255.0))


def _nudge_hue(hue: float, toward_warm: bool, amount: float) -> float:
    """Nudge `hue` `amount` degrees toward the warm or cool pole of the wheel.

    Warm is the red/yellow side around 0/360, cool the blue side around 240.
    The nudge always moves along the *short* side of the wheel for the chosen
    pole: a cool hue's highlight moves up toward magenta/red (209 -> 215)
    instead of wrapping down through cyan into the cool side — the direction
    bug that made blues'/purples' light steps cooler than their mids. A ±6°
    shift is a whisper, never a path across the wheel, so a colour never flips
    past the far side into a different hue family. Deterministic.
    """
    if toward_warm:
        return ((hue - amount) if hue <= 180.0 else (hue + amount)) % 360.0
    return ((hue + amount) if hue <= 180.0 else (hue - amount)) % 360.0


def _nudge_hue_material(hue: float, toward_warm: bool, amount: float) -> float:
    """Material-aware hue nudge for auto-ramp *terrain* tones.

    Same pole semantics as `_nudge_hue` for every hue >= 60° — greens keep
    their light steps moving toward yellow-green and their shadows toward
    blue-green, cool blue/magenta hues keep the P2 direction — but the
    red/orange/brown family (hue < 60°) is corrected to follow the wheel's
    warm side: a brown's *light* step nudges **up toward orange** and its
    *shadow* step nudges **down toward deep red-brown**, instead of the
    legacy pole rule sending both the long way around through yellow. This is
    the reference treatment (CraftPix dirt ramps sit at hue 17-32 with
    orange-light / red-brown-shadow edges, grass at hue 70-110 with
    yellow-green lights and blue-green shadows). Deterministic.
    """
    if hue < 60.0:
        # Red/orange/brown family: warm = toward yellow-orange, cool = toward
        # deep red — the short side of the wheel for these hues.
        return ((hue + amount) if toward_warm else (hue - amount)) % 360.0
    if toward_warm:
        return ((hue - amount) if hue <= 180.0 else (hue + amount)) % 360.0
    return ((hue + amount) if hue <= 180.0 else (hue - amount)) % 360.0


def lighten(hex_str: str, amount: float) -> str:
    """Raise a colour's HSL lightness by `amount` (0..1), hue/saturation preserved."""
    if not 0.0 <= amount <= 1.0:
        raise PaletteError(f"lighten amount must be in [0, 1], got {amount!r}")
    rgba = hex_to_rgba(hex_str)
    h, s, lightness = rgb_to_hsl(rgba[:3])
    r, g, b = hsl_to_rgb(h, s, min(1.0, lightness + amount))
    return rgba_to_hex((r, g, b, rgba[3]))


def darken(hex_str: str, amount: float) -> str:
    """Lower a colour's HSL lightness by `amount` (0..1), hue/saturation preserved."""
    if not 0.0 <= amount <= 1.0:
        raise PaletteError(f"darken amount must be in [0, 1], got {amount!r}")
    rgba = hex_to_rgba(hex_str)
    h, s, lightness = rgb_to_hsl(rgba[:3])
    r, g, b = hsl_to_rgb(h, s, max(0.0, lightness - amount))
    return rgba_to_hex((r, g, b, rgba[3]))


# Ramp steps are placed in CIE L* (perceptual lightness) space rather than
# linear HSL lightness: adjacent steps then differ by roughly the same amount
# on both sides of the base, instead of crushing shadows toward the base while
# blowing highlights out. The shadow floor sits _RAMP_STEP_DELTA_L below the
# base's L* (clamped at L* 0 = black) and the light ceiling _RAMP_STEP_DELTA_L
# above it (clamped at _RAMP_LIGHT_CEILING_L so highlights never blow out past
# ~L* 85). For a mid-lightness base (L* 35-55) that lands the light tone at
# L* 58-78 — the L* 60-75 region professional reference ramps (e.g. CraftPix
# cloth/leather) put their highlights, with a per-step ΔL* of ~16-23.
_RAMP_STEP_DELTA_L = 23.0
_RAMP_LIGHT_CEILING_L = 85.0
# Shadows nudge slightly cool (toward blue), highlights slightly warm (toward
# red/yellow) — the hue-preserving shift professional ramps use so value steps
# stay on-material rather than desaturating toward grey. These legacy amounts
# are the *whisper* the P2 work shipped; `material_hue` ramps (explicitly
# auto-ramped palettes, e.g. terrain demos) use the stronger
# `_MATERIAL_*_HUE_SHIFT_DEG` versions via `_nudge_hue_material` so the
# warm-light/cool-shadow treatment is actually visible in rendered art.
_RAMP_SHADOW_HUE_SHIFT_DEG = 6.0
_RAMP_HIGHLIGHT_HUE_SHIFT_DEG = 6.0
# Material (terrain) ramp hue discipline: a clearly-visible warm/cool split
# (light greens toward yellow-green, shadows toward blue-green, browns' lights
# toward orange) that stays within the hue-family tolerance the ramp tests
# enforce (±20°), so a step never flips into a different colour family.
_MATERIAL_SHADOW_HUE_SHIFT_DEG = 10.0
_MATERIAL_HIGHLIGHT_HUE_SHIFT_DEG = 10.0
# build_ramp supports up to 7 steps: shadow/dark/deep below the base, the base,
# light/bright/glow above.
_MAX_RAMP_STEPS = 7


def _hsl_lightness_for_l_star(hue: float, saturation: float, target_l: float) -> float:
    """HSL lightness (0..1) whose 8-bit sRGB colour's CIE L* is closest to `target_l`.

    CIE L* is monotonic in HSL lightness at fixed hue/saturation, so a binary
    search locates the crossing; 8-bit rounding makes several lightness values
    share one colour, so the samples around the crossing are compared and the
    closest representable colour wins. Deterministic: same inputs, same output.
    """
    lo, hi = 0.0, 1.0
    for _ in range(32):
        mid = (lo + hi) / 2.0
        r, g, b = hsl_to_rgb(hue, saturation, mid)
        if cielab_lightness((r, g, b, 255)) < target_l:
            lo = mid
        else:
            hi = mid
    best_lightness, best_delta = hi, float("inf")
    for k in range(9):
        cand = max(0.0, hi - k / 255.0)
        r, g, b = hsl_to_rgb(hue, saturation, cand)
        delta = abs(cielab_lightness((r, g, b, 255)) - target_l)
        if delta < best_delta:
            best_lightness, best_delta = cand, delta
    return best_lightness


def build_ramp(base_hex: str, steps: int, *, material_hue: bool = False) -> list[str]:
    """Build a `steps`-tone ramp from `base_hex`, darkest first.

    The base colour itself sits at the middle step (index ``(steps - 1) // 2``)
    with its hex preserved verbatim; shadow steps are darker same-hue with a
    slight cool shift, highlight steps lighter same-hue with a slight warm
    shift. Steps are placed at even intervals in CIE L* (perceptual lightness):
    the shadow floor sits ~_RAMP_STEP_DELTA_L below the base's L* and the light
    ceiling ~_RAMP_STEP_DELTA_L above it (capped at _RAMP_LIGHT_CEILING_L), so
    a mid-lightness base lands its highlight at L* 60-75 with roughly even ΔL*
    per step — the spread of professional reference ramps, not the crushed-
    shadow/blown-highlight asymmetry of linear lightness fractions.

    `material_hue=True` applies the material-aware hue discipline
    (`_nudge_hue_material` with `_MATERIAL_*_HUE_SHIFT_DEG`): light greens warm
    toward yellow-green, shadows cool toward blue-green, browns' light steps
    warm toward orange and their shadows deepen toward red-brown — the
    reference top-down terrain treatment, clearly visible in rendered art.
    Default (`material_hue=False`) is the legacy whisper shift exactly as
    shipped, so every existing sprite ramp is byte-identical.

    Deterministic: identical input hex always yields identical output hexes.
    `steps` must be in [1, 7].
    """
    if not 1 <= steps <= _MAX_RAMP_STEPS:
        raise PaletteError(f"ramp steps must be in [1, {_MAX_RAMP_STEPS}], got {steps}")
    base = hex_to_rgba(base_hex)
    if steps == 1:
        return [rgba_to_hex(base)]
    hue, saturation, _ = rgb_to_hsl(base[:3])
    base_index = (steps - 1) // 2
    n_dark = base_index
    n_light = steps - 1 - base_index

    nudge = _nudge_hue_material if material_hue else _nudge_hue
    shadow_shift = _MATERIAL_SHADOW_HUE_SHIFT_DEG if material_hue else _RAMP_SHADOW_HUE_SHIFT_DEG
    highlight_shift = (
        _MATERIAL_HIGHLIGHT_HUE_SHIFT_DEG if material_hue else _RAMP_HIGHLIGHT_HUE_SHIFT_DEG
    )

    base_l = cielab_lightness(base)
    shadow_floor_l = max(0.0, base_l - _RAMP_STEP_DELTA_L)
    if base_l >= _RAMP_LIGHT_CEILING_L:
        # A base already brighter than the ceiling has no headroom under it;
        # the light side uses the full step delta up to white instead.
        light_ceiling_l = min(base_l + _RAMP_STEP_DELTA_L, 100.0)
    else:
        light_ceiling_l = min(base_l + _RAMP_STEP_DELTA_L, _RAMP_LIGHT_CEILING_L)

    result: list[str] = []
    for i in range(steps):
        if i == base_index:
            result.append(rgba_to_hex(base))
            continue
        if i < base_index:
            target_l = shadow_floor_l + (base_l - shadow_floor_l) * (i / n_dark)
            step_hue = nudge(hue, toward_warm=False, amount=shadow_shift)
        else:
            target_l = base_l + (light_ceiling_l - base_l) * ((i - base_index) / n_light)
            step_hue = nudge(hue, toward_warm=True, amount=highlight_shift)
        step_lightness = _hsl_lightness_for_l_star(step_hue, saturation, target_l)
        r, g, b = hsl_to_rgb(step_hue, saturation, step_lightness)
        result.append(rgba_to_hex((r, g, b, base[3])))
    return result


def derive_outline(base_hex: str) -> str:
    """Derive a dark charcoal outline colour with a hint of `base_hex`'s hue.

    The result sits at HSL lightness 0.06..0.14 with the base's hue and a
    fraction of its saturation — never pure black, so the silhouette reads as
    part of the lit scene. Deterministic.
    """
    base = hex_to_rgba(base_hex)
    hue, saturation, _ = rgb_to_hsl(base[:3])
    outline_lightness = max(0.06, min(0.14, _ * 0.30))
    outline_saturation = saturation * 0.35
    r, g, b = hsl_to_rgb(hue, outline_saturation, outline_lightness)
    return rgba_to_hex((r, g, b, base[3]))


def relative_luminance(rgba: RGBA) -> float:
    """Rec.709 relative luminance of an opaque colour (alpha ignored), 0..1."""

    def _channel(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b, _ = rgba
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


_CIE_EPSILON = 216.0 / 24389.0  # (6/29)^3


def cielab_lightness(rgba: RGBA) -> float:
    """CIE L* (perceptual lightness, 0..100) of an opaque colour; alpha ignored."""
    y = relative_luminance(rgba)
    if y > _CIE_EPSILON:
        return 116.0 * math.cbrt(y) - 16.0
    return 116.0 * (y * (841.0 / 108.0) + 4.0 / 29.0) - 16.0


# Suffixes for auto-generated ramp steps below/above the base colour. Index i on
# the dark side names the (i+1)-darkest tone, so a 3-step ramp is
# `{id}_shadow / {id} / {id}_light` and a 5-step ramp adds `{id}_dark` /
# `{id}_bright`.
_DARK_RAMP_SUFFIXES = ("shadow", "dark", "deep")
_LIGHT_RAMP_SUFFIXES = ("light", "bright", "glow")

# A declared colour whose brightest channel is below this is visually
# indistinguishable from pure black on screen, so it carries no usable hue for
# the outline derivation — e.g. an authored `shadow: #000000` ground shadow,
# which every example palette declares. Such colours are skipped when choosing
# the outline base so the derived outline stays hue-tinted (the module's
# documented contract) instead of collapsing to neutral #0f0f0f.
_OUTLINE_BASE_MIN_MAX_CHANNEL = 24


def _outline_base_hex(declared: list[PaletteColor]) -> str | None:
    """The hex of the darkest declared colour usable as an outline base.

    A usable base has non-zero chroma (max - min > 0, so it is not neutral
    grey) and at least one channel >= `_OUTLINE_BASE_MIN_MAX_CHANNEL` (so it
    is not near-black). Ties keep the earlier declared colour. Returns `None`
    when every declared colour is near-black/grey — a degenerate palette with
    no hue to tint the outline from.
    """
    candidates = [
        c.hex for c in declared if _has_usable_hue(hex_to_rgba(c.hex)[:3])
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda h: relative_luminance(hex_to_rgba(h)))


def _has_usable_hue(rgb: tuple[int, int, int]) -> bool:
    """True when `rgb` is a hue-bearing colour dark enough to read as an outline
    base: at least one channel >= `_OUTLINE_BASE_MIN_MAX_CHANNEL` (not
    near-black) and non-zero chroma (not neutral grey)."""
    return max(rgb) >= _OUTLINE_BASE_MIN_MAX_CHANNEL and (max(rgb) - min(rgb)) > 0


def expand_palette(palette: Palette, *, material_hue: bool | None = None) -> Palette:
    """Materialise `palette.auto_ramp` / `palette.derive_outline` into concrete colours.

    - With `auto_ramp`, every colour declaring `ramp_steps >= 2` expands to that
      many tones via `build_ramp`: the declared hex stays as the mid step under
      its own id, new steps get `_shadow/_dark/_deep` (below) or
      `_light/_bright/_glow` (above) suffixes, share the base's `ramp` group (or
      the base id when the base declares none), and the deepest/lightest steps
      carry a shadow/light `role`. Generated steps set `ramp_steps = 1` so
      re-expansion is a no-op (idempotent).
    - With `derive_outline`, a colour id `outline` is appended unless an
      `outline` colour already exists (a declared outline is always kept
      verbatim). The derived outline's base is the palette's darkest
      *hue-bearing* declared colour — non-zero chroma and brightest channel
      >= `_OUTLINE_BASE_MIN_MAX_CHANNEL` — rather than its overall darkest: an
      authored pure-black `shadow` colour would otherwise be chosen and yield a
      neutral #0f0f0f outline with zero hue, contradicting the hue-tinted
      charcoal contract. When every declared colour is near-black (degenerate),
      the overall darkest colour is used.

    `material_hue` selects the ramp hue discipline: `True` gives the
    material-aware warm-light/cool-shadow tones (`build_ramp(material_hue=True)`),
    `False` the legacy whisper shift. `None` (default) follows the palette's own
    `auto_ramp` flag — an author who explicitly turns auto_ramp on opts into the
    material ramps; a palette that only gets auto_ramp *forced* on by the polish
    pipeline (`palette_for_polish`) keeps the legacy tones so existing sprite
    renders never change.

    A palette with neither flag set is returned unchanged. Deterministic.
    """
    if not palette.auto_ramp and not palette.derive_outline:
        return palette
    if material_hue is None:
        material_hue = palette.auto_ramp

    existing_ids = {color.id for color in palette.colors}
    colors: list[PaletteColor] = []
    for color in palette.colors:
        if not palette.auto_ramp or color.ramp_steps < 2:
            colors.append(color)
            continue
        steps = build_ramp(color.hex, color.ramp_steps, material_hue=material_hue)
        base_index = (len(steps) - 1) // 2
        ramp_group = color.ramp or color.id
        for i, hex_str in enumerate(steps):
            if i == base_index:
                colors.append(color)
                continue
            if i < base_index:
                suffix = _DARK_RAMP_SUFFIXES[i]
                role = "shadow" if i == 0 else None
            else:
                suffix = _LIGHT_RAMP_SUFFIXES[i - base_index - 1]
                role = "light" if i == len(steps) - 1 else None
            new_id = f"{color.id}_{suffix}"
            if new_id in existing_ids:
                other = palette.by_id[new_id]
                if other.ramp_steps >= 2:
                    raise PaletteError(
                        f"auto-ramp for colour {color.id!r} would generate {new_id!r}, which "
                        "the palette already declares; remove the hand-written step or lower "
                        "ramp_steps"
                    )
                # Already present as a previously generated step (ramp_steps=1) or an
                # explicitly flat colour: re-expansion keeps it, staying idempotent.
                continue
            colors.append(
                PaletteColor(id=new_id, hex=hex_str, role=role, ramp=ramp_group, ramp_steps=1)
            )

    if palette.derive_outline and "outline" not in existing_ids:
        # Derive from the darkest declared colour with a usable hue, so the
        # outline stays hue-tinted charcoal (see `_outline_base_hex`); fall
        # back to the overall darkest colour when every declared colour is
        # near-black (degenerate palette with nothing to tint from).
        base_hex = _outline_base_hex(palette.colors)
        if base_hex is None:
            darkest = min(colors, key=lambda c: relative_luminance(hex_to_rgba(c.hex)))
            base_hex = darkest.hex
        colors.append(
            PaletteColor(
                id="outline",
                hex=derive_outline(base_hex),
                role="outline",
                ramp_steps=1,  # derived, never auto-ramped — keeps expansion idempotent
            )
        )

    return Palette(
        id=palette.id,
        colors=colors,
        auto_ramp=palette.auto_ramp,
        derive_outline=palette.derive_outline,
    )


def palette_for_polish(palette: Palette) -> Palette:
    """A palette expanded for the render-polish pass (`rendering.effects`).

    The polish pass quantizes every pixel it writes back onto an approved
    palette colour, so the quantization targets must exist: this forces
    `auto_ramp` and `derive_outline` on (each declared colour's `ramp_steps`
    honoured; `ramp_steps=1` colours stay flat) and materialises the expanded
    palette via `expand_palette`. The declared colours' ids and hexes are
    preserved verbatim — only derived ramp tones and the derived `outline`
    colour are appended — so compositing against the flat declared palette
    resolves identically. Deterministic and idempotent (re-expanding the
    result is a no-op). Raises `PaletteError` if expansion would collide with
    a hand-declared ramp colour (see `expand_palette`).

    Ramp hue discipline follows the *author's own* `auto_ramp` flag, checked
    before it is forced on: a palette that explicitly declares `auto_ramp`
    (terrain demos) gets the material-aware warm-light/cool-shadow tones; a
    sprite palette that only receives auto_ramp through this forcing keeps the
    legacy whisper shift, so existing sprite renders stay byte-identical.
    """
    material_hue = palette.auto_ramp
    if palette.auto_ramp and palette.derive_outline:
        return expand_palette(palette, material_hue=material_hue)
    return expand_palette(
        palette.model_copy(update={"auto_ramp": True, "derive_outline": True}),
        material_hue=material_hue,
    )
