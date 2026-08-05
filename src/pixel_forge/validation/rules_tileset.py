"""Tileset-integrity rules: terrain-only checks (TIL001-TIL007)."""

from __future__ import annotations

from pixel_forge.rendering.sheet import check_seams
from pixel_forge.schemas import Finding, TerrainAsset
from pixel_forge.validation.engine import RuleContext, make_finding, register

_TERRAIN = ("terrain",)


def _terrain_doc(ctx: RuleContext) -> TerrainAsset | None:
    return ctx.doc if isinstance(ctx.doc, TerrainAsset) else None


@register(
    "TIL001",
    severity="error",
    kind="deterministic",
    applies_to=_TERRAIN,
    description=(
        "A transition's tile_id must exist in doc.tiles, and every terrain pair "
        "referenced by tiles/transitions must have at least one transition tile."
    ),
)
def _til001(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None:
        return []
    findings = []
    for index, transition in enumerate(doc.transitions):
        if transition.tile_id not in doc.tiles:
            findings.append(
                make_finding(
                    ctx,
                    "TIL001",
                    "error",
                    "deterministic",
                    message=(
                        f"transition {transition.from_terrain}->{transition.to_terrain} "
                        f"({transition.mask}) references unknown tile id "
                        f"{transition.tile_id!r}"
                    ),
                    remediation="declare the tile in doc.tiles or fix the transition's tile_id",
                    measurements={"transition_index": index, "tile_id": transition.tile_id},
                )
            )

    terrains: set[str] = set()
    for tile in doc.tiles.values():
        if tile.terrain:
            terrains.add(tile.terrain)
    pairs_with_transition: set[frozenset[str]] = set()
    for transition in doc.transitions:
        terrains.add(transition.from_terrain)
        terrains.add(transition.to_terrain)
        pairs_with_transition.add(frozenset((transition.from_terrain, transition.to_terrain)))

    sorted_terrains = sorted(terrains)
    for i, a in enumerate(sorted_terrains):
        for b in sorted_terrains[i + 1 :]:
            if frozenset((a, b)) not in pairs_with_transition:
                findings.append(
                    make_finding(
                        ctx,
                        "TIL001",
                        "error",
                        "deterministic",
                        message=f"terrain pair ({a}, {b}) has no transition tiles at all",
                        remediation=f"add a transition connecting {a!r} and {b!r}",
                        measurements={"terrain_a": a, "terrain_b": b},
                    )
                )
    return findings


@register(
    "TIL002",
    severity="error",
    kind="deterministic",
    applies_to=_TERRAIN,
    description="Every tile id listed in a TerrainSet's adjacency list must exist in doc.tiles.",
)
def _til002(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None:
        return []
    findings = []
    for set_name, terrain_set in sorted(doc.terrain_sets.items()):
        for tile_id in terrain_set.tiles:
            if tile_id not in doc.tiles:
                findings.append(
                    make_finding(
                        ctx,
                        "TIL002",
                        "error",
                        "deterministic",
                        message=f"terrain set {set_name!r} references unknown tile id {tile_id!r}",
                        remediation="declare the tile in doc.tiles or remove it from the set",
                        measurements={"terrain_set": set_name, "tile_id": tile_id},
                    )
                )
    return findings


@register(
    "TIL003",
    severity="error",
    kind="deterministic",
    applies_to=_TERRAIN,
    description=(
        "Visible seams via check_seams: self-pair mismatches above "
        "doc.validation.max_seam_mismatch are errors, cross-pair mismatches above the "
        "same threshold are warnings."
    ),
)
def _til003(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None or not ctx.tiles:
        return []
    threshold = doc.validation.max_seam_mismatch
    findings = []
    for result in check_seams(ctx.tiles):
        if result.mismatched_pixels <= threshold:
            continue
        is_self_pair = result.tile_a == result.tile_b
        findings.append(
            make_finding(
                ctx,
                "TIL003",
                "error" if is_self_pair else "warning",
                "deterministic",
                message=(
                    f"seam mismatch of {result.mismatched_pixels}px between "
                    f"{result.tile_a!r} and {result.tile_b!r} on edge {result.edge}"
                ),
                remediation="adjust the tile's edge pixels so it tiles seamlessly against its "
                "neighbour",
                measurements={
                    "tile_a": result.tile_a,
                    "tile_b": result.tile_b,
                    "edge": result.edge,
                    "mismatched_pixels": result.mismatched_pixels,
                },
            )
        )
    return findings


@register(
    "TIL004",
    severity="error",
    kind="deterministic",
    applies_to=_TERRAIN,
    description=(
        "Animated seam error: an animated tile's frames must share one size, and each "
        "frame must tile against itself within doc.validation.max_seam_mismatch."
    ),
)
def _til004(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None or not ctx.tiles:
        return []
    threshold = doc.validation.max_seam_mismatch
    worst_self_seam: dict[str, int] = {}
    for result in check_seams(ctx.tiles):
        if result.tile_a == result.tile_b:
            worst_self_seam[result.tile_a] = max(
                worst_self_seam.get(result.tile_a, 0), result.mismatched_pixels
            )

    findings = []
    for name, spec in sorted(doc.animated_tiles.items()):
        sizes = {
            (ctx.tiles[tid].width, ctx.tiles[tid].height) for tid in spec.frames if tid in ctx.tiles
        }
        if len(sizes) > 1:
            findings.append(
                make_finding(
                    ctx,
                    "TIL004",
                    "error",
                    "deterministic",
                    message=f"animated tile {name!r} frames have differing sizes: {sorted(sizes)}",
                    remediation="render every frame of an animated tile at the same size",
                    measurements={"animated_tile": name, "distinct_sizes": len(sizes)},
                )
            )
        for tile_id in spec.frames:
            mismatch = worst_self_seam.get(tile_id)
            if mismatch is not None and mismatch > threshold:
                findings.append(
                    make_finding(
                        ctx,
                        "TIL004",
                        "error",
                        "deterministic",
                        message=(
                            f"animated tile {name!r} frame {tile_id!r} does not tile against "
                            f"itself: {mismatch}px seam mismatch"
                        ),
                        remediation="fix the tile's edges so each animation frame tiles seamlessly",
                        measurements={
                            "animated_tile": name,
                            "tile_id": tile_id,
                            "mismatched_pixels": mismatch,
                        },
                    )
                )
    return findings


@register(
    "TIL005",
    severity="error",
    kind="deterministic",
    applies_to=_TERRAIN,
    description=(
        "A sample_map layer must only reference known tile ids, and every row must "
        "match sample_map.size."
    ),
)
def _til005(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None or doc.sample_map is None:
        return []
    width, height = doc.sample_map.size
    findings = []
    for layer_name, rows in sorted(doc.sample_map.layers.items()):
        if len(rows) != height:
            findings.append(
                make_finding(
                    ctx,
                    "TIL005",
                    "error",
                    "deterministic",
                    message=(
                        f"sample_map layer {layer_name!r} has {len(rows)} rows, expected {height}"
                    ),
                    remediation="pad/trim the layer so its row count matches sample_map.size",
                    measurements={
                        "layer": layer_name,
                        "row_count": len(rows),
                        "expected_rows": height,
                    },
                )
            )
        for y, row in enumerate(rows):
            if len(row) != width:
                findings.append(
                    make_finding(
                        ctx,
                        "TIL005",
                        "error",
                        "deterministic",
                        message=(
                            f"sample_map layer {layer_name!r} row {y} has {len(row)} cells, "
                            f"expected {width}"
                        ),
                        remediation="pad/trim the row so its length matches sample_map.size",
                        measurements={
                            "layer": layer_name,
                            "row": y,
                            "cell_count": len(row),
                            "expected_cells": width,
                        },
                    )
                )
            for x, tile_id in enumerate(row):
                if tile_id not in doc.tiles:
                    findings.append(
                        make_finding(
                            ctx,
                            "TIL005",
                            "error",
                            "deterministic",
                            message=(
                                f"sample_map layer {layer_name!r} cell ({x},{y}) references "
                                f"unknown tile id {tile_id!r}"
                            ),
                            remediation="declare the tile in doc.tiles or fix the sample map",
                            measurements={"layer": layer_name, "x": x, "y": y, "tile_id": tile_id},
                        )
                    )
    return findings


@register(
    "TIL006",
    severity="warning",
    kind="deterministic",
    applies_to=_TERRAIN,
    description="Tiles within one terrain set should all declare the same collision value.",
)
def _til006(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None:
        return []
    findings = []
    for set_name, terrain_set in sorted(doc.terrain_sets.items()):
        values: dict[str, list[str]] = {}
        for tile_id in terrain_set.tiles:
            tile = doc.tiles.get(tile_id)
            if tile is not None:
                values.setdefault(str(tile.collision), []).append(tile_id)
        if len(values) > 1:
            findings.append(
                make_finding(
                    ctx,
                    "TIL006",
                    "warning",
                    "deterministic",
                    message=(
                        f"terrain set {set_name!r} tiles declare differing collision values: "
                        f"{sorted(values)}"
                    ),
                    remediation="make all tiles in a terrain set agree on collision metadata",
                    measurements={
                        "terrain_set": set_name,
                        "distinct_collision_values": len(values),
                    },
                )
            )
    return findings


@register(
    "TIL007",
    severity="warning",
    kind="heuristic",
    applies_to=_TERRAIN,
    description=(
        "Excessive repeated patterns: a sample_map layer where the single most "
        "frequent tile exceeds doc.validation.max_repeat_ratio of all cells."
    ),
)
def _til007(ctx: RuleContext) -> list[Finding]:
    doc = _terrain_doc(ctx)
    if doc is None or doc.sample_map is None:
        return []
    ratio_limit = doc.validation.max_repeat_ratio
    findings = []
    for layer_name, rows in sorted(doc.sample_map.layers.items()):
        counts: dict[str, int] = {}
        total = 0
        for row in rows:
            for tile_id in row:
                counts[tile_id] = counts.get(tile_id, 0) + 1
                total += 1
        if total == 0:
            continue
        top_tile, top_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        ratio = top_count / total
        if ratio > ratio_limit:
            findings.append(
                make_finding(
                    ctx,
                    "TIL007",
                    "warning",
                    "heuristic",
                    message=(
                        f"sample_map layer {layer_name!r} is {ratio:.1%} tile {top_tile!r}, "
                        f"exceeding max_repeat_ratio {ratio_limit:.1%}"
                    ),
                    remediation="vary tile placement; one dominant tile reads as repetitive",
                    measurements={
                        "layer": layer_name,
                        "tile_id": top_tile,
                        "ratio": ratio,
                        "max_repeat_ratio": ratio_limit,
                    },
                )
            )
    return findings
