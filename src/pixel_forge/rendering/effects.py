"""Deterministic render-polish post-processing pass.

Today every frame renders *flat*: solid palette fills with no shading, no outline,
no shadow. This module is the quality lever that turns a flat composite into
professionally shaded pixel art, in four deterministic stages applied in order:

1. **Shading** — a cheap normal heuristic derived from each opaque pixel's distance
   to transparency in its 4-neighbourhood: pixels near a silhouette edge *facing*
   the light are lifted (highlight tone), pixels near an edge facing away are
   lowered (shadow tone), and the deep interior keeps the author's palette colour
   (mid tone). The light factor quantises to `ArtDirection.ramp_steps` discrete
   tones, and each tone is then mapped **directly onto its material's value ramp**
   (from the expanded palette): shadow tones take the ramp's dark steps,
   highlight tones its light steps, mid tones the base colour. No RGB interpolation
   is involved, so the shaded result lands on an exact ramp tone by construction —
   a highlight can never be quantized back onto its own base colour.
2. **Ambient occlusion** — pixels within a couple of rings of any silhouette edge
   (inner concavities included) are additionally darkened, subtly.
3. **Ink outline** — the outer silhouette boundary ring(s) are recoloured to a dark
   charcoal ink (1px by default, never pure black), preferring the palette's
   derived `outline` colour when the expanded palette has one.
4. **Ground shadow** — an ellipse-ish contact shadow a few rows below the sprite's
   lowest opaque row: dark near the feet, fading with distance and toward the
   horizontal edges, hugging the sprite's horizontal span (at most ~1px wider per
   side). Drawn with fully opaque pixels (alpha stays strictly 0/255) whose
   *colour* is a darkened copy of the sprite pixel above, so it reads as a soft
   shadow under binary-alpha transparency.

**Palette discipline**: every pixel the pass writes is quantized back onto an
approved palette colour (`palette_for_polish`-expanded palette) via a final
nearest-colour pass, so PIX003/PIX004 (blend / unapproved-colour rules) can never
fire on polished output. Quantization maps each unique colour on the canvas to its
nearest palette colour (`ResolvedPalette.nearest`, squared-RGB distance, ties to
the earlier declared id); pixels already exactly on a palette colour are untouched
(distance 0 always wins). A quantization result of pure black is never emitted:
when the nearest palette colour is `#000000` (a palette may declare a black
`shadow` colour, and the ground shadow darkens onto it), the pixel is nudged to
the palette's hue-tinted `outline` charcoal (or the nearest non-black colour when
no `outline` exists) — polished output never contains pure-black pixels.

Determinism contract: `polish_canvas` is a pure function of (canvas pixels,
`ArtDirection`, `ResolvedPalette`). Every effect uses exact integer arithmetic —
no randomness, no clock, no network, and no floating point (not even for the light
direction, which is looked up from a fixed compass table) — so the same input
always produces byte-identical output, on any platform. Alpha is never touched
except by the ground shadow, which only writes fully opaque pixels; nothing ever
writes a semi-transparent pixel.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.palette import ResolvedPalette, relative_luminance
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas.palette import PaletteColor
from pixel_forge.schemas.style import ArtDirection

# `light_angle_deg` (clockwise from screen north, y grows down) -> exact integer
# unit vector pointing from the sprite toward the light source. No trigonometry:
# the angle is validated to be a multiple of 45, so this lookup is total and
# byte-identical on every platform.
_LIGHT_VECTORS: dict[int, tuple[int, int]] = {
    0: (0, -1),
    45: (1, -1),
    90: (1, 0),
    135: (1, 1),
    180: (0, 1),
    225: (-1, 1),
    270: (-1, 0),
    315: (-1, -1),
}

# Neutral dark base for ground-shadow pixels whose column has no sprite pixel
# directly above (shadow wider than the sprite's feet).
_SHADOW_FALLBACK = (44, 44, 54)

# Suffixes for derived ramp tones, matching domain.palette.expand_palette's naming:
# dark side (darkest first) shadow/dark/deep, light side (lightest last)
# light/bright/glow. `expand_palette` materialises `{base}_{suffix}` tones next to
# the base colour whenever `palette_for_polish` runs, so a base colour's ramp can
# be reconstructed purely from the expanded palette by suffix lookup.
_DARK_RAMP_SUFFIXES = ("shadow", "dark", "deep")
_LIGHT_RAMP_SUFFIXES = ("light", "bright", "glow")


def polish_canvas(
    canvas: Canvas,
    direction: ArtDirection,
    palette: ResolvedPalette,
    *,
    region_tags: NDArray[np.int64] | None = None,
) -> Canvas:
    """Apply the render-polish pass to `canvas`, returning a new `Canvas`.

    Pure function of the input pixels, `direction`, and `palette`: identical
    input always yields byte-identical output. The input canvas is never
    mutated. `palette` is the approved palette the pass quantizes onto —
    normally the `palette_for_polish`-expanded palette, so ramp tones and a
    derived `outline` colour exist as quantization targets (see
    `domain.palette.palette_for_polish`); every pixel the pass writes lands on
    one of its colours.

    `region_tags` (optional) is a per-pixel region-ownership map as produced by
    `rendering.compositor.composite_tagged`: `tags[y, x]` is the index of the
    topmost region that drew that pixel, -1 for transparent. When given, the
    shading stage keys each pixel's light factor on its REGION's own local
    geometry (run distances against the region's own silhouette, plus a
    size-adaptive radius capped to the region's own bbox) instead of only the
    global sprite silhouette — so a form whose edges lie interior to the sprite
    (a helmet's underside, a backpack's rim, a limb against the torso) still
    reads as its own volume. Regions too flat to hold a form (own interior
    < 3px: authored ground shadows, thin accents) and untagged pixels keep the
    global edge-distance banding, so the verified flat-shadow treatment is
    preserved. AO, ink outline and ground shadow always stay keyed on the global
    silhouette.
    """
    out = Canvas(canvas.width, canvas.height)
    out.array[:] = canvas.array
    arr = out.array
    opaque = arr[..., 3] != 0
    if not opaque.any():
        return out
    up, down, left, right = _run_distances(opaque)
    light_radius, ao_radius = _size_adaptive_radii(opaque, direction)
    ramps = _build_material_ramps(palette)
    # The shading stage keys each pixel's light factor on per-region local
    # geometry when the compositor's region tags are available; AO, ink outline
    # and ground shadow always stay keyed on the GLOBAL silhouette (concavities,
    # outer ring, feet shadow describe the sprite as a whole, not per-form
    # volumes).
    shade_up, shade_down, shade_left, shade_right = up, down, left, right
    shade_radius: int | NDArray[np.int64] = light_radius
    if region_tags is not None:
        shade_up, shade_down, shade_left, shade_right, shade_radius = _per_region_geometry(
            opaque, region_tags, up, down, left, right, direction, light_radius
        )
    tone = _apply_shading(
        arr, opaque, shade_up, shade_down, shade_left, shade_right, direction, ramps, shade_radius
    )
    _apply_ambient_occlusion(arr, opaque, tone, up, down, left, right, direction, ao_radius)
    _apply_outline(arr, opaque, up, down, left, right, direction, palette)
    _apply_ground_shadow(arr, direction)
    _quantize_to_palette(arr, palette)
    return out


# --- run distances (the shared geometric primitive) ------------------------------------------


def _run_distances(
    mask: NDArray[np.bool_],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]]:
    """Directional run distances for every opaque pixel: how many consecutive opaque
    pixels separate it from the nearest transparent pixel (or the canvas edge, treated
    as transparent) in each of the four directions, inclusive of itself. 1 means the
    pixel is directly adjacent to transparency in that direction; 0 for transparent
    pixels. Pure integer arithmetic over the binary mask."""
    up = _run_distance(mask, axis=0, forward=True)
    down = _run_distance(mask, axis=0, forward=False)
    left = _run_distance(mask, axis=1, forward=True)
    right = _run_distance(mask, axis=1, forward=False)
    return up, down, left, right


def _run_distance(mask: NDArray[np.bool_], axis: int, forward: bool) -> NDArray[np.int64]:
    """One-directional run distance along `axis` (`0` = vertical, `1` = horizontal)."""
    m = mask.astype(np.int64)
    h, w = m.shape
    dist = np.zeros_like(m)
    if axis == 0:
        acc = np.zeros(w, dtype=np.int64)
        rows = range(h) if forward else range(h - 1, -1, -1)
        for y in rows:
            acc = np.where(m[y] != 0, acc + 1, 0)
            dist[y] = acc
    else:
        acc = np.zeros(h, dtype=np.int64)
        cols = range(w) if forward else range(w - 1, -1, -1)
        for x in cols:
            acc = np.where(m[:, x] != 0, acc + 1, 0)
            dist[:, x] = acc
    return dist


# --- material ramps (tone -> ramp-step lookup) ------------------------------------------------


@dataclass(frozen=True)
class _Ramp:
    """A material's ordered value ramp, darkest tone first, plus the base colour's
    index within it. Built once per polish call from the expanded palette."""

    tones: tuple[tuple[int, int, int], ...]
    index: int


def _suffix_base(color_id: str) -> str | None:
    """If `color_id` looks like a derived ramp step `{base}_{suffix}`, return `base`;
    else `None`. `expand_palette` names generated tones this way, so the pattern
    identifies them — but only in combination with `ramp_steps == 1` (a hand-declared
    colour like warden's `gold_dark` keeps its default `ramp_steps` and stays
    declared)."""
    for suffix in (*_DARK_RAMP_SUFFIXES, *_LIGHT_RAMP_SUFFIXES):
        stem = f"_{suffix}"
        if color_id.endswith(stem):
            return color_id[: -len(stem)]
    return None


def _is_generated_step(color: PaletteColor, by_id: dict[str, PaletteColor]) -> bool:
    """True for a colour `expand_palette` materialised from another colour's ramp:
    id `{base}_{suffix}` with `base` itself in the palette, and `ramp_steps` forced
    to 1. Declared colours (whatever their names) keep their authored `ramp_steps`,
    so they never match."""
    if color.ramp_steps != 1:
        return False
    base = _suffix_base(color.id)
    return base is not None and base in by_id


def _build_material_ramps(
    palette: ResolvedPalette,
) -> dict[tuple[int, int, int], _Ramp]:
    """Map every palette colour's RGB to its material's value ramp.

    The shading stage works on the flat composite, whose pixels are exactly the
    declared palette colours, so the lookup key is the pixel's RGB and the value
    is the material ramp that colour belongs to:

    - **Hand-declared ramp groups**: declared colours sharing a `ramp` id (e.g.
      warden's `steel_hi/steel_lite/steel_mid/steel_dark/steel_deep` all with
      `ramp: steel`) form one material. The ramp is the group's members sorted
      darkest-first by relative luminance; each member's index is its own position,
      so a pixel keeps its authored colour at mid tone and pulls toward the
      group's dark/light steps as the tone goes negative/positive.
    - **Auto-ramp colours**: every other colour (including flat ones) uses the
      derived tones `expand_palette` materialised next to it (`{id}_shadow`/
      `{id}_dark`/`{id}`/`{id}_light`/`{id}_bright`/`{id}_glow`). The base sits at
      index = number of dark tones, which is exactly `build_ramp`'s mid-step
      position. A flat colour (no derived tones exist) yields a one-tone ramp and
      stays flat.

    Generated ramp steps themselves map to their base's ramp (position found by
    exact RGB, else luminance), so an already-polished canvas re-polishes
    consistently. Deterministic: pure functions of the palette, iteration in
    declaration order, stable luminance sorts.
    """
    colors = palette.palette.colors
    by_id = palette.palette.by_id
    generated = {c.id for c in colors if _is_generated_step(c, by_id)}
    declared = [c for c in colors if c.id not in generated]

    groups: dict[str, list[PaletteColor]] = {}
    for c in declared:
        if c.ramp is not None:
            groups.setdefault(c.ramp, []).append(c)

    def _ramp_for(color: PaletteColor) -> _Ramp:
        if (
            color.id not in generated
            and color.ramp is not None
            and len(groups.get(color.ramp, [])) >= 2
        ):
            members = sorted(
                groups[color.ramp], key=lambda m: relative_luminance(palette.rgba(m.id))
            )
            tones = tuple(_rgb3(palette.rgba(m.id)) for m in members)
            index = next(i for i, m in enumerate(members) if m.id == color.id)
            return _Ramp(tones=tones, index=index)
        dark: list[tuple[int, int, int]] = [
            _rgb3(palette.rgba(f"{color.id}_{s}"))
            for s in _DARK_RAMP_SUFFIXES
            if f"{color.id}_{s}" in by_id
        ]
        light: list[tuple[int, int, int]] = [
            _rgb3(palette.rgba(f"{color.id}_{s}"))
            for s in _LIGHT_RAMP_SUFFIXES
            if f"{color.id}_{s}" in by_id
        ]
        base_rgb: tuple[int, int, int] = _rgb3(palette.rgba(color.id))
        return _Ramp(tones=(*dark, base_rgb, *light), index=len(dark))

    by_id_ramps = {c.id: _ramp_for(c) for c in colors}
    result: dict[tuple[int, int, int], _Ramp] = {}
    for c in colors:
        rgba = palette.rgba(c.id)
        ramp = by_id_ramps[c.id]
        if c.id not in generated:
            result[(rgba[0], rgba[1], rgba[2])] = ramp
        else:
            # A generated step: map to its base colour's ramp with the step's own
            # position (exact RGB match; the generated tone is one of the ramp's
            # tones by construction) so re-polishing is stable.
            base_id = c.ramp or ""
            base_ramp = by_id_ramps.get(base_id, ramp) if base_id in by_id else ramp
            rgb = (rgba[0], rgba[1], rgba[2])
            tones = base_ramp.tones
            try:
                index = tones.index(rgb)
            except ValueError:
                index = min(range(len(tones)), key=lambda i: _rgb_dist(tones[i], rgb))
            result[rgb] = _Ramp(tones=tones, index=index)
    return result


def _rgb_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _rgb3(rgba: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Strip alpha from a palette RGBA, narrowing the tuple type for mypy."""
    return (rgba[0], rgba[1], rgba[2])


# --- stage 1: directional shading ------------------------------------------------------------


def _per_region_geometry(
    opaque: NDArray[np.bool_],
    tags: NDArray[np.int64],
    up: NDArray[np.int64],
    down: NDArray[np.int64],
    left: NDArray[np.int64],
    right: NDArray[np.int64],
    direction: ArtDirection,
    radius: int,
) -> tuple[
    NDArray[np.int64],
    NDArray[np.int64],
    NDArray[np.int64],
    NDArray[np.int64],
    NDArray[np.int64],
]:
    """Per-region run distances + per-pixel size-adaptive radius for the shading stage.

    The global edge-distance banding shades every pixel by its distance to the
    sprite's OUTER silhouette, so a form whose edges lie interior to the sprite —
    a helmet's underside, a backpack's rim, a limb against the torso — stays flat
    mid-tone. Per-region form shading fixes that: for each region tag the
    compositor stamped, run distances are recomputed against that region's OWN
    silhouette (its own pixels), so interior region boundaries become local edges
    and get the same lit/shadow treatment as the outer silhouette.

    Regions too flat to hold a form (own bbox interior < 3px — authored ground
    shadows, 1-2px accents) keep the global distances: shading them as volumes
    would band a flat shadow ellipse into lit/shadow rings, which is wrong. Their
    pixels fall through to the global arrays, byte-identical to the pre-coherence
    behaviour.

    The radius is also made per-pixel: each region's effective light radius is
    size-adaptive to ITS OWN bbox (same `interior // 6` cap as
    `_size_adaptive_radii`), so a 6x6 shield inside a 64px sprite gets ~1px bands
    instead of the sprite-wide radius swallowing it, while a big region keeps the
    full configured radius. Untagged pixels (tag -1) keep the global radius.

    Pure integer arithmetic over the masks; deterministic.
    """
    shade_up = up.copy()
    shade_down = down.copy()
    shade_left = left.copy()
    shade_right = right.copy()
    shade_radius: NDArray[np.int64] = np.full(opaque.shape, radius, dtype=np.int64)
    for tag in np.unique(tags):
        if tag < 0:
            continue
        mask = tags == tag
        if not mask.any():
            continue
        ys, xs = np.nonzero(mask)
        bbox_w = int(xs.max()) - int(xs.min()) + 1
        bbox_h = int(ys.max()) - int(ys.min()) + 1
        interior = min(bbox_w, bbox_h) - 2 * direction.outline_width
        if interior < 3:
            continue  # too flat to hold a form: keep the global treatment
        ru, rd, rl, rr = _run_distances(mask)
        shade_up[mask] = ru[mask]
        shade_down[mask] = rd[mask]
        shade_left[mask] = rl[mask]
        shade_right[mask] = rr[mask]
        cap = max(1, interior // 6)
        shade_radius[mask] = min(radius, cap)
    return shade_up, shade_down, shade_left, shade_right, shade_radius


def _size_adaptive_radii(
    opaque: NDArray[np.bool_],
    direction: ArtDirection,
) -> tuple[int, int]:
    """Size-adaptive band radii: the effective (light, ambient-occlusion) radius.

    A fixed `light_radius=4` + `ambient_occlusion_radius=3` reads as interior
    form on a 32px+ sprite, but on sub-32px sprites the lit band, shadow band
    and 1px ink outline consume the whole interior, leaving no mid-tone centre
    (the sprite reads as flat stacked bands). The radii are therefore clamped
    to the sprite's own size: the opaque mask's bbox fixes `interior_min_dimension`
    (the smaller bbox side minus both ink-outline rings), and the effective
    radius is capped at one sixth of that interior — so a 20x16 shell (~14px
    interior) gets ~2px bands with a preserved mid-tone centre, while a 32px+
    sprite (interior >= 30) keeps the full configured radius (4). Pure integer
    arithmetic over the binary mask; deterministic, and the same clamp applies
    to both the shading and the ambient-occlusion radius so the two effects
    stay proportionally balanced at every size.
    """
    if not opaque.any():
        return direction.light_radius, direction.ambient_occlusion_radius
    ys, xs = np.nonzero(opaque)
    bbox_w = int(xs.max()) - int(xs.min()) + 1
    bbox_h = int(ys.max()) - int(ys.min()) + 1
    interior_min_dimension = min(bbox_w, bbox_h) - 2 * direction.outline_width
    cap = max(1, interior_min_dimension // 6)
    return (
        min(direction.light_radius, cap),
        min(direction.ambient_occlusion_radius, cap),
    )


def _apply_shading(
    arr: NDArray[np.uint8],
    opaque: NDArray[np.bool_],
    up: NDArray[np.int64],
    down: NDArray[np.int64],
    left: NDArray[np.int64],
    right: NDArray[np.int64],
    direction: ArtDirection,
    ramps: dict[tuple[int, int, int], _Ramp],
    radius: int | NDArray[np.int64],
) -> NDArray[np.int64]:
    """Apply the directional ramp shading; returns the per-pixel quantised tone
    (in `[-(ramp_steps-1)//2, +(ramp_steps-1)//2]`) so later stages can use it.

    Each opaque pixel's continuous light factor is quantised to a tone index
    exactly as before (`net` clipped to `±half`); the pixel is then **set to its
    material ramp's tone at that index** — shadow side → the ramp's dark steps,
    highlight side → its light steps, mid → the base colour. The ramp comes from
    `_build_material_ramps`, so the result is a palette colour by construction and
    the final quantization cannot erase the banding. `shadow_strength` /
    `highlight_strength` gate the dark/light bands (0 keeps the base colour on
    that side); ramp step counts beyond the material's own ramp length clamp at
    the extremes. `radius` is the size-adaptive effective band width (see
    `_size_adaptive_radii`) — the lit/shadow band extends `radius` pixels in
    from the silhouette edge instead of the configured `light_radius` on sprites
    too small to keep a mid-tone centre.
    """
    half = (direction.ramp_steps - 1) // 2
    if half <= 0 or (direction.shadow_strength == 0 and direction.highlight_strength == 0):
        return np.zeros_like(up)
    light_dx, light_dy = _LIGHT_VECTORS[direction.light_angle_deg]

    # How close each pixel is to each silhouette edge, capped at `radius`.
    lit_top = np.maximum(0, radius - up + 1)
    lit_bottom = np.maximum(0, radius - down + 1)
    lit_left = np.maximum(0, radius - left + 1)
    lit_right = np.maximum(0, radius - right + 1)

    if light_dy < 0:
        light_y, dark_y = lit_top, lit_bottom
    elif light_dy > 0:
        light_y, dark_y = lit_bottom, lit_top
    else:
        light_y, dark_y = np.zeros_like(lit_top), np.zeros_like(lit_top)
    if light_dx < 0:
        light_x, dark_x = lit_left, lit_right
    elif light_dx > 0:
        light_x, dark_x = lit_right, lit_left
    else:
        light_x, dark_x = np.zeros_like(lit_top), np.zeros_like(lit_top)

    # A pixel near *either* light-side edge is lit; near either dark-side edge it is
    # shadowed. The net factor is clamped to the ramp's half-width, so a 3-step ramp
    # yields crisp shadow / mid / highlight bands instead of a soft wash.
    net = np.maximum(light_y, light_x) - np.maximum(dark_y, dark_x)
    tone = np.clip(net, -half, half)

    shadow_side = direction.shadow_strength > 0
    light_side = direction.highlight_strength > 0

    r = arr[..., 0].astype(np.int64)
    g = arr[..., 1].astype(np.int64)
    b = arr[..., 2].astype(np.int64)

    for base_rgb, ramp in ramps.items():
        mask = opaque & (r == base_rgb[0]) & (g == base_rgb[1]) & (b == base_rgb[2])
        if not mask.any():
            continue
        t = tone[mask]
        tones_arr = np.array(ramp.tones, dtype=np.uint8)
        n = len(ramp.tones)
        idx = np.full(t.shape, ramp.index, dtype=np.int64)
        if shadow_side:
            dm = t < 0
            if dm.any():
                # Stretch the tone over the dark side of the ramp: `-half` reaches
                # the darkest step, `-1` at least one step down, clamped to the ramp.
                off = np.maximum(1, (-t[dm]) * ramp.index // half)
                idx[dm] = np.maximum(0, ramp.index - off)
        if light_side:
            lm = t > 0
            if lm.any():
                off = np.maximum(1, t[lm] * (n - 1 - ramp.index) // half)
                idx[lm] = np.minimum(n - 1, ramp.index + off)
        arr[mask, 0] = tones_arr[idx, 0]
        arr[mask, 1] = tones_arr[idx, 1]
        arr[mask, 2] = tones_arr[idx, 2]
    return np.asarray(tone, dtype=np.int64)


# --- stage 2: ambient occlusion ---------------------------------------------------------------


def _apply_ambient_occlusion(
    arr: NDArray[np.uint8],
    opaque: NDArray[np.bool_],
    tone: NDArray[np.int64],
    up: NDArray[np.int64],
    down: NDArray[np.int64],
    left: NDArray[np.int64],
    right: NDArray[np.int64],
    direction: ArtDirection,
    radius: int,
) -> None:
    strength = direction.ambient_occlusion_strength
    if strength <= 0:
        return
    min_dist = np.minimum(np.minimum(up, down), np.minimum(left, right))
    # Occlusion only darkens the shadow side (tone <= 0): pixels near a lit edge
    # keep their highlight, so the two effects never fight each other.
    mask = opaque & (min_dist <= radius) & (tone <= 0)
    if not mask.any():
        return
    amt = strength * (radius + 1 - min_dist) // radius
    scale = 255 - np.where(mask, amt, 0)
    for c in range(3):
        arr[..., c][mask] = (arr[..., c].astype(np.int64) * scale // 255)[mask]


# --- stage 3: ink outline ----------------------------------------------------------------------


def _apply_outline(
    arr: NDArray[np.uint8],
    opaque: NDArray[np.bool_],
    up: NDArray[np.int64],
    down: NDArray[np.int64],
    left: NDArray[np.int64],
    right: NDArray[np.int64],
    direction: ArtDirection,
    palette: ResolvedPalette,
) -> None:
    width = direction.outline_width
    if width <= 0:
        return
    min_dist = np.minimum(np.minimum(up, down), np.minimum(left, right))
    mask = opaque & (min_dist <= width)
    if not mask.any():
        return
    if "outline" in palette.ids:
        # Prefer the palette's derived outline colour when expansion produced
        # one: it is hue-tinted from the palette's darkest colour and exists as
        # an exact quantization target, so the inked ring survives the final
        # nearest-colour pass verbatim (distance 0).
        r, g, b, _ = palette.rgba("outline")
    else:
        # No derived outline: use the direction's charcoal as the *target* and
        # let the final quantization snap it to the nearest approved colour.
        r, g, b = _parse_hex(direction.outline_color)
    arr[mask, 0] = r
    arr[mask, 1] = g
    arr[mask, 2] = b
    # alpha of inked pixels is already 255 (they were opaque)


# --- stage 4: ground shadow --------------------------------------------------------------------


def _apply_ground_shadow(arr: NDArray[np.uint8], direction: ArtDirection) -> None:
    if (
        not direction.ground_shadow_enabled
        or direction.ground_shadow_rows <= 0
        or direction.ground_shadow_strength <= 0
    ):
        return
    opaque = arr[..., 3] != 0
    if not opaque.any():
        return
    h, w = arr.shape[:2]
    ys, xs = np.nonzero(opaque)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    ground_y = int(ys.max()) + 1  # first row below the sprite (bbox is half-open)
    rows = min(direction.ground_shadow_rows, h - ground_y)
    if rows <= 0:
        return
    half_w = (x1 - x0) // 2
    cx2 = x0 + x1 - 1  # twice the horizontal centre, kept integer (may be odd)
    strength = direction.ground_shadow_strength
    for dy in range(rows):
        y = ground_y + dy
        # Darkest right under the feet, fading with distance (integer falloff).
        row_scale = (2 * (rows - dy) + 1) * 255 // (2 * rows + 1)
        # The shadow widens slightly toward its last row (ellipse-ish
        # footprint), capped at half_w + 1: a professional contact shadow hugs
        # the feet width rather than fanning out like a drop shadow.
        hw = min(max(1, half_w * (rows + dy + 1) // (rows + 1)), half_w + 1)
        for dx in range(-hw, hw + 1):
            x = (cx2 + 2 * dx + 1) // 2  # round(cx + dx) via doubled integer math
            # Never spill more than 1px beyond the sprite's own horizontal
            # span [x0, x1) — the ellipse rounding is centred on the geometric
            # middle, which for an odd-width sprite sits off the pixel grid.
            if x < x0 - 1 or x > x1:
                continue
            if x < 0 or x >= w:
                continue
            # Centre columns darker than the flanks.
            col_scale = 255 - (255 * dx * dx * 3) // (5 * hw * hw)
            amt = strength * row_scale * col_scale // (255 * 255)
            if amt <= 0:
                continue
            above = arr[y - 1, x]
            if above[3] == 255:
                base = (int(above[0]), int(above[1]), int(above[2]))
            else:
                base = _SHADOW_FALLBACK
            arr[y, x] = (
                base[0] * (255 - amt) // 255,
                base[1] * (255 - amt) // 255,
                base[2] * (255 - amt) // 255,
                255,
            )


# --- helpers ------------------------------------------------------------------------------------


def _quantize_to_palette(arr: NDArray[np.uint8], palette: ResolvedPalette) -> None:
    """Snap every opaque pixel's RGB to its nearest approved palette colour.

    Runs once, after all four stages, so the pass can never leave a
    non-palette colour behind: shading/AO blends, the ink outline, and the
    darkened ground shadow all land on the expanded palette
    (`domain.palette.palette_for_polish`). Pixels already exactly on a
    palette colour map to themselves (squared-RGB distance 0 always wins);
    alpha is untouched — transparent pixels stay transparent, opaque stay
    255. Deterministic: `nearest` breaks ties by declaration order and the
    numpy unique/inverse remap is stable.

    A result of pure black is never emitted: when the nearest colour is
    `#000000` (declared `shadow` colours and the ground shadow both produce
    it), the pixel is nudged to the palette's hue-tinted `outline` charcoal
    (or, failing that, the palette's nearest non-black colour) so polished
    output stays in the lit scene rather than reading as a vector-stroke
    void.
    """
    opaque = arr[..., 3] != 0
    if not opaque.any():
        return
    palette_rgbs: list[tuple[int, int, int]] = [
        _rgb3(palette.rgba(c.id)) for c in palette.palette.colors
    ]
    fallback: tuple[int, int, int] | None = None
    if (0, 0, 0) in palette_rgbs:
        if "outline" in palette.ids:
            o: tuple[int, int, int] = _rgb3(palette.rgba("outline"))
            if o != (0, 0, 0):
                fallback = o
        if fallback is None:
            candidates: list[tuple[int, int, int]] = [
                rgb for rgb in palette_rgbs if rgb != (0, 0, 0)
            ]
            if candidates:
                fallback = min(candidates, key=lambda rgb: _rgb_dist(rgb, (0, 0, 0)))
    pixels = arr[opaque]
    uniq, inverse = np.unique(pixels, axis=0, return_inverse=True)
    lookup = np.empty_like(uniq)
    for i, rgba in enumerate(uniq):
        color_id = palette.nearest((int(rgba[0]), int(rgba[1]), int(rgba[2]), 255))
        nr, ng, nb, _ = palette.rgba(color_id)
        if (nr, ng, nb) == (0, 0, 0) and fallback is not None:
            nr, ng, nb = fallback
        lookup[i] = (nr, ng, nb, rgba[3])
    arr[opaque] = lookup[inverse]


def _parse_hex(value: str) -> tuple[int, int, int]:
    """`#rrggbb` -> `(r, g, b)`. `ArtDirection` validates the shape; this only converts."""
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
