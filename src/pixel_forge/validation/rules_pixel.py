"""Pixel-integrity rules: per-frame raster sanity checks (PIX001-PIX010).

Deterministic unless marked heuristic. All rules receive already-rendered
`Canvas` objects via `RuleContext.frames`; none of them render anything.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.palette import ResolvedPalette, rgba_to_hex
from pixel_forge.domain.palette import check_palette_limit as _check_palette_limit
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import Finding
from pixel_forge.validation.engine import RuleContext, make_finding, register

_SPRITE_TYPES = ("character", "enemy", "prop")


def _non_palette_colors(canvas: Canvas, palette: ResolvedPalette) -> list[RGBA]:
    return sorted(c for c in canvas.colors() if not palette.contains_rgba(c))


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
        "A non-transparent colour not present in the palette is exactly how an "
        "AA/blend artifact shows up. Skipped when doc.validation.allow_antialiasing."
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
                        f"colour {rgba_to_hex(rgba)} is not in the palette; this is the "
                        "signature of an antialiasing/blend artifact"
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
    description="A colour used in a frame must be an approved palette colour.",
)
def _pix004(ctx: RuleContext) -> list[Finding]:
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        canvas = ctx.frames[key]
        for rgba in _non_palette_colors(canvas, ctx.palette):
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
    description="Palette must not declare more colours than doc.validation.palette_limit.",
)
def _pix005(ctx: RuleContext) -> list[Finding]:
    limit = ctx.doc.validation.palette_limit
    excess = _check_palette_limit(ctx.palette.palette, limit)
    if not excess:
        return []
    return [
        make_finding(
            ctx,
            "PIX005",
            "error",
            "deterministic",
            message=(
                f"palette declares {ctx.palette.size} colours, exceeding the limit of {limit} "
                f"by {len(excess)}: {', '.join(excess)}"
            ),
            remediation=f"reduce the palette to {limit} colours or raise validation.palette_limit",
            measurements={
                "palette_size": ctx.palette.size,
                "palette_limit": limit,
                "excess_count": len(excess),
                "excess_ids": ",".join(excess),
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
