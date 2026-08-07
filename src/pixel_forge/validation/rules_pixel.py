"""Pixel-integrity rules: per-frame raster and palette sanity checks (PIX001-PIX015).

Deterministic unless marked heuristic. All rules receive already-rendered
`Canvas` objects via `RuleContext.frames`; none of them render anything.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.palette import (
    ResolvedPalette,
    cielab_lightness,
    hex_to_rgba,
    rgb_to_hsl,
    rgba_to_hex,
)
from pixel_forge.domain.palette import check_palette_limit as _check_palette_limit
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import BitmapShape, Finding, PaletteColor, SpriteAssetBase
from pixel_forge.validation.engine import RuleContext, make_finding, register

_SPRITE_TYPES = ("character", "enemy", "prop")

# Upper bound on measurements["coords"] pixel lists in the quality rules below
# (PIX016-PIX020), so a pathological frame can never blow up report size.
_MAX_COORDS_PIX = 100


def _non_palette_colors(canvas: Canvas, palette: ResolvedPalette) -> list[RGBA]:
    return sorted(c for c in canvas.colors() if not palette.contains_rgba(c))


def _on_segment(
    point: tuple[int, int, int], a: tuple[int, int, int], b: tuple[int, int, int]
) -> bool:
    """True if `point` is (on rounding) the nearest point on segment `a`-`b` in RGB space."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    d2 = dx * dx + dy * dy + dz * dz
    if d2 == 0:
        return False
    px, py, pz = point[0] - a[0], point[1] - a[1], point[2] - a[2]
    t = (px * dx + py * dy + pz * dz) / d2
    if t < 0.0 or t > 1.0:
        return False
    nearest = (round(a[0] + t * dx), round(a[1] + t * dy), round(a[2] + t * dz))
    return nearest == point


def _is_palette_blend(rgba: RGBA, palette: ResolvedPalette) -> bool:
    """True if `rgba`'s RGB lies on (or within rounding of) the segment between two
    distinct palette colours — the exact signature an antialiasing/dithering blend
    leaves; a colour that is off-palette but not on any such segment is simply wrong,
    not a blend artifact."""
    point = (rgba[0], rgba[1], rgba[2])
    colors = [hex_to_rgba(c.hex) for c in palette.palette.colors]
    triples = [(r, g, b) for r, g, b, _a in colors]
    return any(_on_segment(point, a, b) for i, a in enumerate(triples) for b in triples[i + 1 :])


@register(
    "PIX001",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description="Rendered frame dimensions must match doc.asset.canvas.",
)
def _pix001(ctx: RuleContext) -> list[Finding]:
    expected_w, expected_h = ctx.doc.asset.canvas
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        if (canvas.width, canvas.height) != (expected_w, expected_h):
            findings.append(
                make_finding(
                    ctx,
                    "PIX001",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"frame is {canvas.width}x{canvas.height}, "
                        f"expected {expected_w}x{expected_h} (doc.asset.canvas)"
                    ),
                    remediation=(
                        "check region offsets/scale_size aren't pushing content outside the "
                        "declared canvas, or update asset.canvas to match"
                    ),
                    measurements={
                        "width": canvas.width,
                        "height": canvas.height,
                        "expected_width": expected_w,
                        "expected_height": expected_h,
                    },
                )
            )
    return findings


@register(
    "PIX002",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description="Alpha must be strictly 0 or 255 (binary transparency).",
)
def _pix002(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        alpha = ctx.frames[key].array[..., 3]
        bad_mask = (alpha != 0) & (alpha != 255)
        bad_count = int(np.count_nonzero(bad_mask))
        if bad_count:
            bad_values = sorted(int(v) for v in np.unique(alpha[bad_mask]))
            findings.append(
                make_finding(
                    ctx,
                    "PIX002",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{bad_count} pixel(s) have non-binary alpha values "
                        f"{bad_values}; wrong transparency mode"
                    ),
                    remediation="re-render with binary alpha; never write partial-alpha pixels",
                    measurements={
                        "bad_pixel_count": bad_count,
                        "bad_alpha_values": ",".join(str(v) for v in bad_values),
                    },
                )
            )
    return findings


@register(
    "PIX003",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A non-palette colour that lies on (or within rounding of) the segment between "
        "two palette colours in RGB space — the exact signature an antialiasing/blend "
        "artifact leaves. Skipped when doc.validation.allow_antialiasing. A non-palette "
        "colour that is not a blend of two palette colours is PIX004's concern instead."
    ),
)
def _pix003(ctx: RuleContext) -> list[Finding]:
    if ctx.doc.validation.allow_antialiasing:
        return []
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        for rgba in _non_palette_colors(canvas, ctx.palette):
            if not _is_palette_blend(rgba, ctx.palette):
                continue
            findings.append(
                make_finding(
                    ctx,
                    "PIX003",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"colour {rgba_to_hex(rgba)} is a blend of two palette colours; this is "
                        "the signature of an antialiasing artifact"
                    ),
                    remediation=(
                        "re-render with nearest-neighbour only, or set "
                        "validation.allow_antialiasing if intentional"
                    ),
                    measurements={"color_hex": rgba_to_hex(rgba)},
                )
            )
    return findings


@register(
    "PIX004",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A colour used in a frame must be an approved palette colour or a blend of two "
        "(PIX003's concern). Never waivable — not gated by allow_antialiasing."
    ),
)
def _pix004(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        for rgba in _non_palette_colors(canvas, ctx.palette):
            if _is_palette_blend(rgba, ctx.palette):
                continue
            findings.append(
                make_finding(
                    ctx,
                    "PIX004",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=f"colour {rgba_to_hex(rgba)} used in frame is not an approved colour",
                    remediation="use only approved palette colours, or add this colour to the "
                    "palette",
                    measurements={"color_hex": rgba_to_hex(rgba)},
                )
            )
    return findings


@register(
    "PIX005",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "The declared palette must not declare more colours than "
        "doc.validation.palette_limit. Auto-ramp/outline tones derived at render "
        "time (domain.palette.palette_for_polish) are not counted: they are "
        "generated quantization targets, not authored palette entries."
    ),
)
def _pix005(ctx: RuleContext) -> list[Finding]:
    limit = ctx.doc.validation.palette_limit
    # Count only the authored palette. RuleContext.palette may be the render-time
    # expanded one (api.py validation with art_direction), whose derived ramp
    # tones would wrongly push a small authored palette over the limit.
    declared = ctx.doc.palette
    excess = _check_palette_limit(declared, limit)
    if not excess:
        return []
    return [
        make_finding(
            ctx,
            "PIX005",
            "error",
            "deterministic",
            message=(
                f"palette declares {len(declared.colors)} colours, exceeding the limit of "
                f"{limit} by {len(excess)}: {', '.join(excess)}"
            ),
            remediation=f"reduce the palette to {limit} colours or raise validation.palette_limit",
            measurements={
                "palette_size": len(declared.colors),
                "palette_limit": limit,
                "excess_count": len(excess),
                "excess_ids": ", ".join(excess),
            },
        )
    ]


def _neighbor_counts(mask: NDArray[np.bool_]) -> NDArray[np.int_]:
    h, w = mask.shape
    padded = np.pad(mask, 1, constant_values=False).astype(np.int_)
    counts = np.zeros((h, w), dtype=np.int_)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            counts += padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
    return counts


@register(
    "PIX006",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description="Orphan pixels: a non-transparent pixel with zero non-transparent 8-neighbours.",
)
def _pix006(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        mask = canvas.array[..., 3] != 0
        if not mask.any():
            continue
        orphans = mask & (_neighbor_counts(mask) == 0)
        count = int(np.count_nonzero(orphans))
        if count:
            ys, xs = np.nonzero(orphans)
            findings.append(
                make_finding(
                    ctx,
                    "PIX006",
                    "warning",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{count} orphan pixel(s) with no non-transparent 8-neighbour "
                        f"(first at {int(xs[0])},{int(ys[0])})"
                    ),
                    remediation="remove stray pixels or connect them to the surrounding shape",
                    measurements={
                        "orphan_count": count,
                        "first_x": int(xs[0]),
                        "first_y": int(ys[0]),
                    },
                )
            )
    return findings


def _edge_mask(opaque: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """4-neighbour silhouette edge: an opaque pixel touching a transparent pixel or
    the canvas boundary (out-of-canvas treated as transparent)."""
    h, w = opaque.shape
    padded = np.pad(opaque, 1, constant_values=False)
    north = padded[0:h, 1 : w + 1]
    south = padded[2 : h + 2, 1 : w + 1]
    west = padded[1 : h + 1, 0:w]
    east = padded[1 : h + 1, 2 : w + 2]
    return opaque & (~north | ~south | ~west | ~east)


@register(
    "PIX007",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Suspicious outline heuristic: silhouette edge pixels (opaque, 4-adjacent to "
        "transparent or the canvas boundary) are grouped by colour; the most common "
        "colour is treated as the outline. Any other colour present on the edge that "
        "accounts for under 10% of all edge pixels is flagged, since a small patch of "
        "'wrong' outline colour is the classic signature of a stray pixel or an "
        "accidental palette swap, whereas a colour covering a large share of the edge "
        "is more likely an intentional multi-colour outline."
    ),
)
def _pix007(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        opaque = canvas.array[..., 3] != 0
        # Exclude the render-polish contact-shadow band (the `polish_shadow_rows`
        # rows appended below the sprite's feet) from the edge-colour coverage
        # analysis: those pixels are shadow-darkened, never inked, and sit on
        # the silhouette edge (the band's bottom row and its up-to-1px-wider
        # flanks), so they would otherwise read as a persistent off-colour
        # "outline" patch and fire this heuristic on every polished frame.
        shadow_rows = ctx.polish_shadow_rows
        if shadow_rows > 0:
            ys, _xs = np.nonzero(opaque)
            if ys.size:
                band_top = int(ys.max()) - shadow_rows + 1
                if band_top > 0:
                    opaque = opaque.copy()
                    opaque[band_top:, :] = False
        edge = _edge_mask(opaque)
        total_edge = int(np.count_nonzero(edge))
        if total_edge == 0:
            continue
        ys, xs = np.nonzero(edge)
        counts: dict[RGBA, int] = {}
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
            r, g, b, a = canvas.array[y, x]
            rgba = (int(r), int(g), int(b), int(a))
            counts[rgba] = counts.get(rgba, 0) + 1
        dominant = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        for rgba, count in sorted(counts.items()):
            if rgba == dominant:
                continue
            ratio = count / total_edge
            if ratio < 0.10:
                findings.append(
                    make_finding(
                        ctx,
                        "PIX007",
                        "warning",
                        "heuristic",
                        animation=animation,
                        direction=direction,
                        frame=index,
                        message=(
                            f"colour {rgba_to_hex(rgba)} covers only {ratio:.1%} of the "
                            f"silhouette edge, versus outline colour {rgba_to_hex(dominant)}"
                        ),
                        remediation="check for a stray outline pixel or accidental colour swap",
                        measurements={
                            "outline_color_hex": rgba_to_hex(dominant),
                            "offending_color_hex": rgba_to_hex(rgba),
                            "edge_pixel_count": count,
                            "edge_total": total_edge,
                            "ratio": ratio,
                        },
                    )
                )
    return findings


@register(
    "PIX008",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description="A frame required by an animation must not be entirely empty (zero opaque pixels).",
)
def _pix008(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        if canvas.opaque_count() == 0:
            findings.append(
                make_finding(
                    ctx,
                    "PIX008",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message="frame has zero opaque pixels but is required by this animation",
                    remediation="check region visibility/offset for this frame; nothing renders",
                    measurements={"opaque_pixel_count": 0},
                )
            )
    return findings


def _downscale_upscale_mask(mask: NDArray[np.bool_], scale: int) -> NDArray[np.bool_]:
    """Block-downscale `mask` by `scale` (a block is "on" if any pixel in it is
    opaque) then upscale back by repeating. Invariant under this round-trip iff
    every scale x scale block is uniformly opaque or uniformly transparent -
    i.e. every feature is aligned to and at least as large as the logical pixel
    grid."""
    h, w = mask.shape
    pad_h, pad_w = (-h) % scale, (-w) % scale
    padded = np.pad(mask, ((0, pad_h), (0, pad_w)), constant_values=False)
    ph, pw = padded.shape
    blocks = padded.reshape(ph // scale, scale, pw // scale, scale)
    down = blocks.any(axis=(1, 3))
    up = np.repeat(np.repeat(down, scale, axis=0), scale, axis=1)
    return up[:h, :w]


@register(
    "PIX009",
    severity="warning",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "logical_pixel_scale != 1 and the frame contains a feature smaller than that "
        "scale, detected by checking the opaque mask is invariant under a "
        "downscale-by-scale then upscale-by-scale round trip."
    ),
)
def _pix009(ctx: RuleContext) -> list[Finding]:
    scale = ctx.doc.asset.logical_pixel_scale
    if scale == 1:
        return []
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        mask = canvas.array[..., 3] != 0
        if not mask.any():
            continue
        roundtrip = _downscale_upscale_mask(mask, scale)
        mismatched = int(np.count_nonzero(mask != roundtrip))
        if mismatched:
            findings.append(
                make_finding(
                    ctx,
                    "PIX009",
                    "warning",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{mismatched} pixel(s) form a feature smaller than the "
                        f"logical_pixel_scale of {scale}"
                    ),
                    remediation=f"align every shape to the {scale}x{scale} logical pixel grid",
                    measurements={
                        "mismatched_pixel_count": mismatched,
                        "logical_pixel_scale": scale,
                    },
                )
            )
    return findings


def _role_matches(role: str | None, token: str) -> bool:
    return role is not None and token in role.lower()


@register(
    "PIX010",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Inconsistent lighting metadata heuristic: palette colours whose role contains "
        "'shadow' mark shaded pixels. If no palette colour declares a light/shadow role "
        "or a ramp, the doc carries no lighting metadata and only an info finding is "
        "emitted. Otherwise, each frame's shadow-pixel centroid (relative to its bbox "
        "centre) is bucketed into a compass direction; if frames disagree on that "
        "direction, a consistent light source is not implied and a warning fires."
    ),
)
def _pix010(ctx: RuleContext) -> list[Finding]:
    colors = ctx.palette.palette.colors
    shadow_ids = {c.id for c in colors if _role_matches(c.role, "shadow")}
    has_lighting_metadata = bool(shadow_ids) or any(
        _role_matches(c.role, "light") or c.ramp is not None for c in colors
    )
    if not has_lighting_metadata:
        return [
            make_finding(
                ctx,
                "PIX010",
                "info",
                "heuristic",
                message="doc carries no lighting metadata (no palette colour role/ramp declared)",
                remediation="tag shadow/light palette colours with role/ramp to enable this check",
                measurements={"palette_colors_with_role": 0},
            )
        ]

    shadow_rgba = {ctx.palette.rgba(cid) for cid in shadow_ids}
    buckets: set[str] = set()
    considered = 0
    for key in sorted(ctx.frames):
        canvas = ctx.frames[key]
        bbox = canvas.bbox()
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        cx, cy = (x0 + x1 - 1) / 2, (y0 + y1 - 1) / 2
        arr = canvas.array
        shadow_mask = np.zeros(arr.shape[:2], dtype=bool)
        for rgba in shadow_rgba:
            shadow_mask |= np.all(arr == np.array(rgba, dtype=np.uint8), axis=-1)
        if not shadow_mask.any():
            continue
        ys, xs = np.nonzero(shadow_mask)
        dx = float(xs.mean()) - cx
        dy = float(ys.mean()) - cy
        ns = "N" if dy < -0.5 else "S" if dy > 0.5 else ""
        ew = "W" if dx < -0.5 else "E" if dx > 0.5 else ""
        buckets.add(ns + ew or "center")
        considered += 1

    if considered == 0 or len(buckets) <= 1:
        return []
    return [
        make_finding(
            ctx,
            "PIX010",
            "warning",
            "heuristic",
            message=(
                f"shadow-colour placement implies inconsistent light directions across "
                f"frames: {sorted(buckets)}"
            ),
            remediation="keep the shadow side consistent across frames for a single light source",
            measurements={"directions": ",".join(sorted(buckets)), "frames_considered": considered},
        )
    ]


@register(
    "PIX011",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A bitmap shape's `key` must map every character to a palette colour id that "
        "actually exists in the doc's palette. Render time only discovers this as a crashing "
        "PaletteError; this rule reports it as a normal finding instead."
    ),
)
def _pix011(ctx: RuleContext) -> list[Finding]:
    if not isinstance(ctx.doc, SpriteAssetBase):
        return []
    findings = []
    # Bitmap keys resolve against the *declared* palette: the compositor renders
    # with the flat declared palette (only the polish quantization targets are
    # expanded), so a key pointing at a derived ramp tone would crash at render
    # time even though RuleContext.palette may know the id.
    declared_ids = ctx.doc.palette.by_id
    for region_name in sorted(ctx.doc.regions):
        for shape in ctx.doc.regions[region_name].shapes:
            if not isinstance(shape, BitmapShape):
                continue
            for char in sorted(shape.key):
                color_id = shape.key[char]
                if color_id in declared_ids:
                    continue
                findings.append(
                    make_finding(
                        ctx,
                        "PIX011",
                        "error",
                        "deterministic",
                        region=region_name,
                        message=(
                            f"bitmap in region {region_name!r} maps key char {char!r} to "
                            f"{color_id!r}, which is not a colour in palette "
                            f"{ctx.doc.palette.id!r}"
                        ),
                        remediation=(
                            f"point key {char!r} at an existing palette colour id, or add "
                            f"{color_id!r} to the palette"
                        ),
                        measurements={"region": region_name, "char": char, "color_id": color_id},
                    )
                )
    return findings


# A material's shaded area must clear this many pixels before PIX012 treats a single shade as
# a quality problem rather than a legitimately flat small detail (a shadow sliver, a highlight
# speck, ...).
_PIX012_MIN_MATERIAL_AREA_PX = 32


@register(
    "PIX012",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Flat-shading heuristic: palette colours that share a `ramp` id are the shades "
        "of one material. For every ramp with 2+ declared colours, sum the pixel area "
        "that uses any colour from that ramp — both across a region's bitmap shapes and "
        "across the already-rendered frame canvases (so a large flat rect/ellipse region "
        "with no bitmap keys is seen too). If that area exceeds "
        "PIX012_MIN_MATERIAL_AREA_PX but fewer than 2 distinct colours from the ramp are "
        "actually used, the surface is flagged: a large flat region despite having a ramp "
        "to shade it with. Colours with no declared ramp are not considered, so flat "
        "outlines/accents never trip this rule."
    ),
)
def _pix012(ctx: RuleContext) -> list[Finding]:
    if not isinstance(ctx.doc, SpriteAssetBase):
        return []
    # Ramp membership comes from the *declared* palette: this rule judges the
    # authored bitmap material discipline, so render-time derived ramp tones
    # must not inflate a ramp's step count.
    ramp_by_color: dict[str, str] = {
        color.id: color.ramp for color in ctx.doc.palette.colors if color.ramp is not None
    }
    ramp_sizes: dict[str, int] = {}
    for ramp in ramp_by_color.values():
        ramp_sizes[ramp] = ramp_sizes.get(ramp, 0) + 1

    findings = []
    # Ramps the bitmap-shape path already flagged are not re-flagged by the
    # rendered-frame path below: for a bitmap-authored asset the composited
    # pixels are exactly the bitmap content, so a second finding would be noise.
    flagged_ramps: set[str] = set()
    for region_name in sorted(ctx.doc.regions):
        area_by_ramp: dict[str, int] = {}
        colors_used_by_ramp: dict[str, set[str]] = {}
        for shape in ctx.doc.regions[region_name].shapes:
            if not isinstance(shape, BitmapShape):
                continue
            for row in shape.rows:
                for char in row:
                    if char in (".", " "):
                        continue
                    color_id = shape.key[char]
                    char_ramp = ramp_by_color.get(color_id)
                    if char_ramp is None or ramp_sizes[char_ramp] < 2:
                        continue
                    area_by_ramp[char_ramp] = area_by_ramp.get(char_ramp, 0) + 1
                    colors_used_by_ramp.setdefault(char_ramp, set()).add(color_id)
        for ramp in sorted(area_by_ramp):
            area = area_by_ramp[ramp]
            distinct = len(colors_used_by_ramp[ramp])
            if area > _PIX012_MIN_MATERIAL_AREA_PX and distinct < 2:
                flagged_ramps.add(ramp)
                findings.append(
                    make_finding(
                        ctx,
                        "PIX012",
                        "warning",
                        "heuristic",
                        region=region_name,
                        message=(
                            f"region {region_name!r} covers {area}px of material {ramp!r} with "
                            f"only {distinct} distinct colour(s) from that ramp; flat, unshaded art"
                        ),
                        remediation=(
                            f"use more than one colour from the {ramp!r} ramp to shade this "
                            "surface (light/mid/shadow), or re-author with a tonal ramp"
                        ),
                        measurements={
                            "region": region_name,
                            "ramp": ramp,
                            "area_px": area,
                            "distinct_colors_used": distinct,
                            "min_area_px": _PIX012_MIN_MATERIAL_AREA_PX,
                        },
                    )
                )
    # Rendered-frame path: count covered pixels per ramp from the composited
    # canvases, so a large flat region drawn with rect/ellipse shapes (which
    # have no bitmap keys) also trips the rule. Frames render with the flat
    # declared palette, so rendered pixels map straight back to declared ids.
    if ctx.frames:
        color_ramp: dict[RGBA, str] = {}
        for color in ctx.doc.palette.colors:
            declared_ramp = ramp_by_color.get(color.id)
            if declared_ramp is not None and ramp_sizes[declared_ramp] >= 2:
                color_ramp[hex_to_rgba(color.hex)] = declared_ramp
        for key in sorted(ctx.frames):
            animation, direction, index = key
            arr = ctx.frames[key].array.reshape(-1, 4)
            visible = arr[arr[:, 3] != 0]
            if len(visible) == 0:
                continue
            frame_area_by_ramp: dict[str, int] = {}
            frame_colors_used_by_ramp: dict[str, set[RGBA]] = {}
            unique, counts = np.unique(visible, axis=0, return_counts=True)
            for color, count in zip(unique, counts, strict=True):
                rgba = (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
                frame_ramp = color_ramp.get(rgba)
                if frame_ramp is None or frame_ramp in flagged_ramps:
                    continue
                frame_area_by_ramp[frame_ramp] = frame_area_by_ramp.get(frame_ramp, 0) + int(count)
                frame_colors_used_by_ramp.setdefault(frame_ramp, set()).add(rgba)
            for frame_ramp in sorted(frame_area_by_ramp):
                area = frame_area_by_ramp[frame_ramp]
                distinct = len(frame_colors_used_by_ramp[frame_ramp])
                if area > _PIX012_MIN_MATERIAL_AREA_PX and distinct < 2:
                    findings.append(
                        make_finding(
                            ctx,
                            "PIX012",
                            "warning",
                            "heuristic",
                            animation=animation,
                            direction=direction,
                            frame=index,
                            message=(
                                f"frame {animation}/{direction}/{index} covers {area}px of "
                                f"material {frame_ramp!r} with only {distinct} distinct colour(s) "
                                "from that ramp; flat, unshaded surface"
                            ),
                            remediation=(
                                "use more than one colour from the "
                                f"{frame_ramp!r} ramp to shade this "
                                "surface (light/mid/shadow), or re-author with a tonal ramp"
                            ),
                            measurements={
                                "ramp": frame_ramp,
                                "area_px": area,
                                "distinct_colors_used": distinct,
                                "min_area_px": _PIX012_MIN_MATERIAL_AREA_PX,
                            },
                        )
                    )
    return findings


# A palette whose HSL lightness spread stays below this is flat: no material can
# carry a shadow/mid/highlight step, so the art cannot express form. Outline
# colours are excluded — they are silhouettes, not shading steps.
_PIX013_MIN_LIGHTNESS_SPREAD = 0.25


@register(
    "PIX013",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Flat-palette heuristic: after excluding outline-role colours, the palette's "
        "declared colours span less than PIX013_MIN_LIGHTNESS_SPREAD in HSL lightness. "
        "Every colour is roughly the same value, so no material can be shaded; the "
        "render is guaranteed flat."
    ),
)
def _pix013(ctx: RuleContext) -> list[Finding]:
    colors = ctx.palette.palette.colors
    shading_colors = [c for c in colors if not _role_matches(c.role, "outline")]
    if len(shading_colors) < 2:
        return []
    lightness = [rgb_to_hsl(hex_to_rgba(c.hex)[:3])[2] for c in shading_colors]
    lo, hi = min(lightness), max(lightness)
    span = hi - lo
    if span >= _PIX013_MIN_LIGHTNESS_SPREAD:
        return []
    return [
        make_finding(
            ctx,
            "PIX013",
            "warning",
            "heuristic",
            message=(
                f"palette lightness spans only {span:.2f} (min {lo:.2f}, max {hi:.2f}) "
                f"across {len(shading_colors)} shading colours; every colour is nearly the "
                "same value, so no material can be shaded"
            ),
            remediation=(
                "add darker shadow and lighter highlight steps per material — "
                "pixel_forge.domain.palette.build_ramp generates a hue-preserving ramp "
                "from each base colour, or set palette.auto_ramp to do it in bulk"
            ),
            measurements={
                "lightness_min": round(lo, 3),
                "lightness_max": round(hi, 3),
                "lightness_span": round(span, 3),
                "min_span": _PIX013_MIN_LIGHTNESS_SPREAD,
                "palette_size": len(shading_colors),
            },
        )
    ]


# Adjacent ramp steps must differ by at least this many CIE L* units; a smaller
# gap is perceptually the same tone and renders as a flat band.
_PIX014_MIN_STEP_DELTA_L = 4.0
# A ramp whose adjacent gaps are wildly unequal is perceptually lopsided —
# shadows crushed toward the base while highlights blow out — even when every
# gap clears the minimum. Flag when the largest gap is more than this many
# times the smallest (a hand-authored ramp with per-step ΔL* 16-23 sits near 1).
_PIX014_MAX_STEP_ASYMMETRY_RATIO = 2.5
# A ramp whose darkest step sits at or below this CIE L* is exempt from the
# asymmetry branch only: the darkest tone is geometrically floor-clamped at
# L* 0 (no representable lightness exists below it), so its gap to the next
# step is forced tiny and the largest/smallest ratio reads lopsided no matter
# how evenly the ramp was authored (e.g. build_ramp('#0f0f0f', 3) lands its
# shadow at L* 0 with a ΔL* 4.3 first gap vs 23.2 above — ratio 5.4). The
# banding check still applies to near-black ramps.
_PIX014_FLOOR_CLAMP_EXEMPT_L = 3.0


@register(
    "PIX014",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Banding heuristic: colours that share a `ramp` id are one material's tonal "
        "steps. Sorted by CIE L* lightness, adjacent steps must differ by at least "
        "PIX014_MIN_STEP_DELTA_L; a smaller gap reads as the same tone, so the material "
        "renders as flat bands instead of a shade transition. Ramps with 3+ steps must "
        "also be balanced: the largest adjacent gap may be at most "
        "PIX014_MAX_STEP_ASYMMETRY_RATIO times the smallest, so a ramp whose shadows are "
        "crushed while its highlights blow out is caught even when every gap is legal. "
        "Ramps are judged on the *declared* palette (matching PIX012), not the "
        "palette_for_polish-expanded render palette — expansion materialises a full "
        "build_ramp tone set per declared colour, so judging the mixed set would compare "
        "hand-authored steps against derived tones and flag legitimate ramps. A ramp "
        "whose darkest step is floor-clamped near-black (L* <= "
        "PIX014_FLOOR_CLAMP_EXEMPT_L) is exempt from the balance check: the darkest tone "
        "has no representable lightness below it, so its first gap is geometrically "
        "tiny and the ratio reads lopsided regardless of authoring."
    ),
)
def _pix014(ctx: RuleContext) -> list[Finding]:
    by_ramp: dict[str, list[PaletteColor]] = {}
    # Judge the *declared* palette, matching PIX012: the render path hands rules
    # the palette_for_polish-expanded palette (each declared colour with
    # ramp_steps >= 2 materialises its own build_ramp tone set into the same ramp
    # group), so judging ramp *quality* against that set would mix hand-authored
    # steps with derived tones and flag legitimate ramps as banded/lopsided.
    for color in ctx.doc.palette.colors:
        if color.ramp is not None:
            by_ramp.setdefault(color.ramp, []).append(color)
    findings = []
    for ramp in sorted(by_ramp):
        steps = by_ramp[ramp]
        if len(steps) < 2:
            continue
        ordered = sorted(
            steps,
            key=lambda c: (cielab_lightness(hex_to_rgba(c.hex)), c.id),
        )
        deltas = [
            abs(cielab_lightness(hex_to_rgba(a.hex)) - cielab_lightness(hex_to_rgba(b.hex)))
            for a, b in itertools.pairwise(ordered)
        ]
        for (a, b), delta_l in zip(itertools.pairwise(ordered), deltas, strict=True):
            if delta_l < _PIX014_MIN_STEP_DELTA_L:
                findings.append(
                    make_finding(
                        ctx,
                        "PIX014",
                        "warning",
                        "heuristic",
                        message=(
                            f"ramp {ramp!r} steps {a.id!r} ({a.hex}) and {b.id!r} ({b.hex}) "
                            f"differ by only {delta_l:.1f} CIE L*; perceptually the same "
                            "tone, so the material renders as a flat band"
                        ),
                        remediation=(
                            "space the ramp steps further apart in lightness "
                            f"(adjacent steps need \u0394L* \u2265 {_PIX014_MIN_STEP_DELTA_L}), "
                            "e.g. via pixel_forge.domain.palette.build_ramp"
                        ),
                        measurements={
                            "ramp": ramp,
                            "color_a": a.id,
                            "color_b": b.id,
                            "delta_l": round(delta_l, 3),
                            "min_delta_l": _PIX014_MIN_STEP_DELTA_L,
                        },
                    )
                )
        # Balance check: with at least two adjacent gaps, a ramp whose largest
        # gap dwarfs its smallest is lopsided even when every gap is legal.
        # A zero gap is already reported as banding and would divide by zero here.
        # A ramp whose darkest step is floor-clamped at near-black (L* below
        # _PIX014_FLOOR_CLAMP_EXEMPT_L) is exempt: no representable lightness
        # exists below the darkest tone, so its first gap is geometrically tiny
        # and the ratio measures the clamp, not the authoring. The banding check
        # above still guards the dark end of such ramps.
        darkest_l = cielab_lightness(hex_to_rgba(ordered[0].hex))
        if (
            len(deltas) >= 2
            and min(deltas) > 0.0
            and darkest_l > _PIX014_FLOOR_CLAMP_EXEMPT_L
        ):
            ratio = max(deltas) / min(deltas)
            if ratio > _PIX014_MAX_STEP_ASYMMETRY_RATIO:
                findings.append(
                    make_finding(
                        ctx,
                        "PIX014",
                        "warning",
                        "heuristic",
                        message=(
                            f"ramp {ramp!r} is lopsided: adjacent steps differ by "
                            f"{min(deltas):.1f} to {max(deltas):.1f} CIE L* "
                            f"({ratio:.1f}x, limit "
                            f"{_PIX014_MAX_STEP_ASYMMETRY_RATIO:.1f}x); one side of the "
                            "ramp is crushed while the other blows out"
                        ),
                        remediation=(
                            "space the ramp steps at even CIE L* intervals so shadows and "
                            "highlights move by similar amounts per step, e.g. via "
                            "pixel_forge.domain.palette.build_ramp"
                        ),
                        measurements={
                            "ramp": ramp,
                            "min_delta_l": round(min(deltas), 3),
                            "max_delta_l": round(max(deltas), 3),
                            "asymmetry_ratio": round(ratio, 3),
                            "max_asymmetry_ratio": _PIX014_MAX_STEP_ASYMMETRY_RATIO,
                        },
                    )
                )
    return findings


@register(
    "PIX015",
    severity="warning",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "An outline colour (role containing 'outline', or id 'outline') must not be "
        "pure black (#000000): professional art outlines with a dark charcoal tinted "
        "by the material's hue, so the silhouette reads as part of the lit scene "
        "rather than a hard void. Deterministic — an exact match, not a judgement call."
    ),
)
def _pix015(ctx: RuleContext) -> list[Finding]:
    findings = []
    for color in ctx.palette.palette.colors:
        r, g, b, _a = hex_to_rgba(color.hex)
        is_pure_black = r == 0 and g == 0 and b == 0
        is_outline = _role_matches(color.role, "outline") or color.id == "outline"
        if not (is_pure_black and is_outline):
            continue
        findings.append(
            make_finding(
                ctx,
                "PIX015",
                "warning",
                "deterministic",
                message=(
                    f"outline colour {color.id!r} is pure black (#000000); a pure-black "
                    "outline flattens the silhouette against the background"
                ),
                remediation=(
                    "use a dark charcoal tinted with the material's hue instead — "
                    "pixel_forge.domain.palette.derive_outline(<base hex>) never returns "
                    "pure black"
                ),
                measurements={"color_id": color.id, "color_hex": color.hex},
            )
        )
    return findings


# ---------------------------------------------------------------------------
# PIX016-PIX020: machine-readable quality heuristics (W3-A). Every finding
# carries measurements["coords"] = [[x, y], ...] (row-major, capped) so repair
# agents get pixel-level guidance.
# ---------------------------------------------------------------------------


def _outline_rgba(ctx: RuleContext) -> set[RGBA]:
    """RGBA tuples of the palette's outline colour(s).

    Same convention as PIX015: a colour whose role contains 'outline' or whose
    id is exactly 'outline'. Empty when the palette declares no outline colour
    — outline-dependent rules then skip rather than guess.
    """
    result: set[RGBA] = set()
    for color in ctx.palette.palette.colors:
        if _role_matches(color.role, "outline") or color.id == "outline":
            result.add(hex_to_rgba(color.hex))
    return result


def _silhouette_mask(ctx: RuleContext, canvas: Canvas) -> NDArray[np.bool_]:
    """Opaque mask with the render-polish contact-shadow band removed (when
    present), so edge/outline heuristics judge the sprite itself rather than
    the shadow band's flank (same exclusion PIX007 applies inline)."""
    opaque = canvas.array[..., 3] != 0
    shadow_rows = ctx.polish_shadow_rows
    if shadow_rows > 0:
        ys, _xs = np.nonzero(opaque)
        if ys.size:
            band_top = int(ys.max()) - shadow_rows + 1
            if band_top > 0:
                opaque = opaque.copy()
                opaque[band_top:, :] = False
    return np.asarray(opaque, dtype=bool)


def _row_major_coords(ys: NDArray[np.int_], xs: NDArray[np.int_]) -> list[list[int]]:
    """[[x, y], ...] pairs in row-major (y, then x) order, capped."""
    return [[int(x), int(y)] for y, x in zip(ys.tolist(), xs.tolist(), strict=True)]


@register(
    "PIX016",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Orphan pixel heuristic (same-colour variant): an opaque pixel whose four "
        "orthogonal neighbours contain no pixel of the exact same colour. A single "
        "pixel floating in its own colour is the classic signature of a stray "
        "speck or an unintended checkerboard/dither pattern — which this toolkit's "
        "binary-alpha, nearest-neighbour rendering never produces on purpose. "
        "Complements PIX006, which flags pixels with no opaque 8-neighbour at all."
    ),
)
def _pix016(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        arr = ctx.frames[key].array
        mask = arr[..., 3] != 0
        if not mask.any():
            continue
        h, w = mask.shape
        padded = np.pad(arr, ((1, 1), (1, 1), (0, 0)), constant_values=0)
        has_same = np.zeros((h, w), dtype=bool)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbour = padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
            has_same |= np.all(neighbour == arr, axis=-1)
        orphans = mask & ~has_same
        defect_coords: list[list[int]] = []
        count = 0
        for y, x in zip(*np.nonzero(orphans), strict=True):
            # Calibrated: a *defect* orphan is a stray speck — its 3x3
            # neighbourhood contains no OTHER colour (the render-polish
            # shade-band dither sits between tones, so its orphans touch other
            # colours and are excluded) and its same-colour 8-component is
            # tiny (it doesn't connect diagonally into the silhouette).
            y0, y1 = max(0, y - 1), min(h, y + 2)
            x0, x1 = max(0, x - 1), min(w, x + 2)
            sub = arr[y0:y1, x0:x1]
            own = arr[y, x]
            other = (sub[..., 3] != 0) & ~np.all(sub == own, axis=-1)
            if np.any(other):
                continue
            if _same_color_8_component_size(arr, y, x) > _PIX016_MAX_8_COMPONENT:
                continue
            count += 1
            defect_coords.append([int(x), int(y)])
            if len(defect_coords) >= _MAX_COORDS_PIX:
                break
        if count:
            findings.append(
                make_finding(
                    ctx,
                    "PIX016",
                    "warning",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{count} stray speck pixel(s) of one colour floating in "
                        f"a flat field (first at {defect_coords[0][0]},{defect_coords[0][1]})"
                    ),
                    remediation=(
                        "remove the stray pixel or bridge it to the surrounding "
                        "same-colour shape"
                    ),
                    measurements={
                        "orphan_count": count,
                        "coords": defect_coords,
                    },
                )
            )
    return findings


def _same_color_components(
    mask: NDArray[np.bool_], colors: NDArray[np.uint8]
) -> list[list[tuple[int, int]]]:
    """4-connected components of `mask` where every pixel shares the exact RGBA
    in `colors`. Returns per-component pixel lists in row-major start order."""
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for start_y in range(h):
        for start_x in range(w):
            if visited[start_y, start_x] or not mask[start_y, start_x]:
                continue
            color = tuple(colors[start_y, start_x])
            stack = [(start_y, start_x)]
            visited[start_y, start_x] = True
            component: list[tuple[int, int]] = []
            while stack:
                y, x = stack.pop()
                component.append((y, x))
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = y + dy, x + dx
                    if (
                        0 <= ny < h
                        and 0 <= nx < w
                        and not visited[ny, nx]
                        and mask[ny, nx]
                        and tuple(colors[ny, nx]) == color
                    ):
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            components.append(component)
    return components


def _same_color_8_component_size(
    colors: NDArray[np.uint8], y: int, x: int
) -> int:
    """Size of the 8-connected same-colour opaque component containing (y, x).

    A pixel whose same-colour 8-component stays tiny (<= 4 px) is a stray
    speck; one that connects diagonally into a larger shape is part of the
    silhouette and must not be judged as noise.
    """
    h, w = colors.shape[:2]
    own = tuple(colors[y, x])
    visited = np.zeros((h, w), dtype=bool)
    stack = [(y, x)]
    visited[y, x] = True
    size = 0
    while stack:
        cy, cx = stack.pop()
        size += 1
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ny, nx = cy + dy, cx + dx
                if (
                    0 <= ny < h
                    and 0 <= nx < w
                    and not visited[ny, nx]
                    and colors[ny, nx, 3] != 0
                    and tuple(colors[ny, nx]) == own
                ):
                    visited[ny, nx] = True
                    stack.append((ny, nx))
    return size


_PIX016_MAX_8_COMPONENT = 4


@register(
    "PIX017",
    severity="info",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Noisy-cluster heuristic: a same-colour 4-connected cluster of 2-3 pixels "
        "whose bounding box contains no pixel of any other colour — a tiny isolated "
        "speck of one colour, the signature of render noise or a dropped anti-alias "
        "remnant, rather than a deliberate detail (a deliberate detail sits next to "
        "other colours)."
    ),
)
def _pix017(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        arr = ctx.frames[key].array
        mask = arr[..., 3] != 0
        if not mask.any():
            continue
        noisy_coords: list[list[int]] = []
        cluster_count = 0
        for component in _same_color_components(mask, arr):
            if not (2 <= len(component) <= 3):
                continue
            ys = [p[0] for p in component]
            xs = [p[1] for p in component]
            # Calibrated: the check ring is the 1px-expanded bbox, not the tight
            # bbox — the render-polish dither clusters touch other tones within
            # one pixel, so they are excluded; a genuine speck sits in a flat
            # field with nothing else around it.
            y0, y1 = max(0, min(ys) - 1), min(mask.shape[0], max(ys) + 2)
            x0, x1 = max(0, min(xs) - 1), min(mask.shape[1], max(xs) + 2)
            sub = arr[y0:y1, x0:x1]
            comp_rgba = np.array(arr[component[0][0], component[0][1]], dtype=np.uint8)
            opaque = sub[..., 3] != 0
            same_color = np.all(sub == comp_rgba, axis=-1)
            if np.any(opaque & ~same_color):
                continue
            if (
                _same_color_8_component_size(arr, component[0][0], component[0][1])
                > _PIX016_MAX_8_COMPONENT
            ):
                continue
            cluster_count += 1
            noisy_coords.extend([[x, y] for y, x in component])
            if len(noisy_coords) >= _MAX_COORDS_PIX:
                break
        if cluster_count:
            findings.append(
                make_finding(
                    ctx,
                    "PIX017",
                    "info",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{cluster_count} isolated same-colour cluster(s) of 2-3 "
                        "pixels with no other colour in their bounding box; likely "
                        "speckle noise"
                    ),
                    remediation=(
                        "remove the speck or connect it to the surrounding shape; "
                        "if intentional, give it a neighbouring shade so it reads "
                        "as detail"
                    ),
                    measurements={
                        "cluster_count": cluster_count,
                        "coords": noisy_coords[:_MAX_COORDS_PIX],
                    },
                )
            )
    return findings


@register(
    "PIX018",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Broken-outline heuristic: with an outline colour declared (role contains "
        "'outline' or id 'outline'), the silhouette edge should be continuously "
        "inked — each edge pixel covered by the outline colour itself or by its "
        "inward neighbour. A run of >= 2 consecutive uncovered edge pixels is a "
        "gap where the ink outline breaks (a cut, a dropped pixel, or an outline "
        "that never closed). Skipped when the palette declares no outline colour."
    ),
)
def _pix018(ctx: RuleContext) -> list[Finding]:
    outline = _outline_rgba(ctx)
    if not outline:
        return []
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        arr = ctx.frames[key].array
        opaque = _silhouette_mask(ctx, ctx.frames[key])
        if not opaque.any():
            continue
        edge = _edge_mask(opaque)
        if not edge.any():
            continue
        h, w = opaque.shape
        ink = np.zeros((h, w), dtype=bool)
        for rgba in outline:
            ink |= np.all(arr == np.array(rgba, dtype=np.uint8), axis=-1)
        # A boundary pixel is covered when the ink outline is on the pixel itself
        # (boundary outline) or directly inward from it (inset outline). Sideways
        # ink along the boundary must NOT cover a notch — that would let a 2px
        # outline gap hide behind the corner pixels it sits between.
        inward = opaque & ~edge
        inward_ink = np.zeros((h, w), dtype=bool)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            inward_ink |= np.pad(inward & ink, 1)[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
        covered = ink | inward_ink
        uncovered = edge & ~covered
        if not uncovered.any():
            continue
        gap_coords: list[list[int]] = []
        gap_count = 0
        visited = np.zeros((h, w), dtype=bool)
        for y in range(h):
            for x in range(w):
                if visited[y, x] or not uncovered[y, x]:
                    continue
                stack = [(y, x)]
                visited[y, x] = True
                gap: list[tuple[int, int]] = []
                while stack:
                    cy, cx = stack.pop()
                    gap.append((cy, cx))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = cy + dy, cx + dx
                            if (
                                0 <= ny < h
                                and 0 <= nx < w
                                and not visited[ny, nx]
                                and uncovered[ny, nx]
                            ):
                                visited[ny, nx] = True
                                stack.append((ny, nx))
                if len(gap) >= 2:
                    gap_count += 1
                    gap_coords.extend([[x, y] for y, x in gap])
                    if len(gap_coords) >= _MAX_COORDS_PIX:
                        break
        if gap_count:
            findings.append(
                make_finding(
                    ctx,
                    "PIX018",
                    "warning",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{gap_count} gap(s) of >= 2 consecutive pixels where the "
                        "ink outline is missing along the silhouette edge"
                    ),
                    remediation=(
                        "re-ink the outline across each gap so the silhouette edge "
                        "is continuously outlined"
                    ),
                    measurements={
                        "gap_count": gap_count,
                        "coords": gap_coords[:_MAX_COORDS_PIX],
                    },
                )
            )
    return findings


@register(
    "PIX019",
    severity="info",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Spatial-banding heuristic: a run of >= 6 consecutive same-colour opaque "
        "pixels along a 45-degree diagonal with no dithering (the run never "
        "alternates colour). The render-polish shade bands top out at 4px runs, "
        "so a 6px+ run is a genuine hand-drawn staircase rather than the engine's "
        "deliberate gradient. Palette-level ramp banding is PIX014's concern; "
        "this rule reports the *pixels* of each offending run so a repair agent "
        "can dither them."
    ),
)
def _pix019(ctx: RuleContext) -> list[Finding]:
    # Banding is an authored-art check: it targets hand-drawn 45-degree
    # staircases in the spec geometry. The render-polish pass deliberately
    # shades with integer run-distance bands (1-2px tone steps along the light
    # axis), so judging polished frames by this rule reports the engine's own
    # deterministic output as a defect. The polish expands the palette
    # (palette_for_polish forces auto_ramp + derive_outline), so a palette
    # larger than the declared one means the frames were polished — skip.
    if len(ctx.palette.palette.colors) > len(ctx.doc.palette.colors):
        return []
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        arr = ctx.frames[key].array
        mask = arr[..., 3] != 0
        if not mask.any():
            continue
        h, w = mask.shape
        # Banding = a long same-colour diagonal run whose colour DIFFERS from
        # the colour of the field it cuts through. A same-colour run in a
        # same-colour field is flat fill (a solid rect must NOT fire); a tone
        # step — black cutting through red — is what reads as a hard stair.
        # Edge runs (the shape's 45-degree outline) are excluded; the
        # render-polish shade bands are excluded by the palette-expansion skip.
        opaque = _silhouette_mask(ctx, ctx.frames[key])
        edge = _edge_mask(opaque)
        run_coords: list[list[int]] = []
        run_count = 0
        for step in ((1, 1), (1, -1)):
            # Each diagonal line is walked once from its two starting edges.
            starts: list[tuple[int, int]] = []
            if step[1] == 1:  # down-right: starts on the top row and left column
                starts.extend((0, x) for x in range(w))
                starts.extend((y, 0) for y in range(1, h))
            else:  # down-left: starts on the top row and right column
                starts.extend((0, x) for x in range(w))
                starts.extend((y, w - 1) for y in range(1, h))
            for sy, sx in starts:
                run: list[tuple[int, int]] = []
                interior: list[tuple[int, int]] = []
                y, x = sy, sx
                while 0 <= y < h and 0 <= x < w:
                    if not mask[y, x]:
                        # Transparent resets the run but the walk continues, so a
                        # diagonal run in the middle of the canvas (opaque pixels
                        # with transparent leading cells on the same line) is
                        # still found — not just runs touching the canvas edge.
                        run = []
                        interior = []
                        y += step[0]
                        x += step[1]
                        continue
                    if run and tuple(arr[run[-1][0], run[-1][1]]) != tuple(arr[y, x]):
                        run = []
                        interior = []
                    run.append((y, x))
                    if not edge[y, x]:
                        interior.append((y, x))
                    if len(interior) >= 6:
                        # Field colour = dominant opaque colour in the run's
                        # 1px-expanded neighbourhood, excluding the run's own
                        # pixels. Same colour -> flat fill, not a band.
                        run_colour = tuple(arr[y, x])
                        field: dict[tuple[int, int, int, int], int] = {}
                        for ry, rx in run:
                            for ny in range(max(0, ry - 1), min(h, ry + 2)):
                                for nx in range(max(0, rx - 1), min(w, rx + 2)):
                                    if (ny, nx) in run or not mask[ny, nx]:
                                        continue
                                    colour = tuple(arr[ny, nx])
                                    field[colour] = field.get(colour, 0) + 1
                        if field:
                            dominant: tuple[int, int, int, int] = max(
                                field, key=lambda c: field[c]
                            )
                            if dominant != run_colour:
                                run_count += 1
                                if len(run_coords) < _MAX_COORDS_PIX:
                                    # Stable order: x ascending, then y (the test
                                    # contract), regardless of walk direction.
                                    run_coords.extend(
                                        [px, py]
                                        for py, px in sorted(run, key=lambda p: (p[1], p[0]))
                                    )
                        run = []
                        interior = []
                    y += step[0]
                    x += step[1]
        if run_count:
            findings.append(
                make_finding(
                    ctx,
                    "PIX019",
                    "info",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"{run_count} undithered same-colour run(s) of >= 6 pixels "
                        "on a 45-degree diagonal through a flat field; shading may "
                        "read as banding"
                    ),
                    remediation=(
                        "dither or break up the longest diagonal runs with an "
                        "adjacent shade so the slope reads as a gradient"
                    ),
                    measurements={"run_count": run_count, "coords": run_coords},
                )
            )
    return findings


@register(
    "PIX020",
    severity="info",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Weak-silhouette heuristic: the frame's opaque pixels fill less than 25% "
        "of their bounding box (a sparse, spindly outline with no body) or the "
        "silhouette's centroid sits more than 30% of the bbox half-diagonal away "
        "from the bbox centre (mass piled into one corner, so the sprite reads "
        "unbalanced). Both thresholds are heuristic — a deliberate thin weapon "
        "may trip the fill branch."
    ),
)
def _pix020(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        bbox = canvas.bbox()
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        bw, bh = x1 - x0, y1 - y0
        area = bw * bh
        count = canvas.opaque_count()
        fill = count / area if area else 0.0
        mask = canvas.array[..., 3] != 0
        ys, xs = np.nonzero(mask)
        centroid_x = float(xs.mean())
        centroid_y = float(ys.mean())
        centre_x = (x0 + x1 - 1) / 2
        centre_y = (y0 + y1 - 1) / 2
        half_diag = math.hypot(bw, bh) / 2
        off_centre = math.hypot(centroid_x - centre_x, centroid_y - centre_y) / half_diag
        if fill >= 0.25 and off_centre <= 0.30:
            continue
        reasons = []
        if fill < 0.25:
            reasons.append(f"fill ratio {fill:.2f} < 0.25")
        if off_centre > 0.30:
            reasons.append(f"centroid {off_centre:.2f} off centre (> 0.30)")
        findings.append(
            make_finding(
                ctx,
                "PIX020",
                "info",
                "heuristic",
                animation=animation,
                direction=direction,
                frame=index,
                message=(
                    f"weak silhouette: {'; '.join(reasons)} for a {bw}x{bh}px "
                    f"bounding box with {count} opaque pixels"
                ),
                remediation=(
                    "add body mass so the silhouette fills its bounding box, or "
                    "re-balance the pose so the mass sits near the bbox centre"
                ),
                measurements={
                    "fill_ratio": round(fill, 3),
                    "off_centre_ratio": round(off_centre, 3),
                    "bbox_width": bw,
                    "bbox_height": bh,
                    "opaque_pixel_count": count,
                    "centroid_x": round(centroid_x, 2),
                    "centroid_y": round(centroid_y, 2),
                    "coords": [
                        [x0, y0],
                        [x1 - 1, y0],
                        [x0, y1 - 1],
                        [x1 - 1, y1 - 1],
                    ],
                },
            )
        )
    return findings
