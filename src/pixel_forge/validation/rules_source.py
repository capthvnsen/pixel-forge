"""Source-asset rules (SRC001-SRC002): geometry `ExternalFrameBackend` ignores.

A `source:` asset gets its pixels from files on disk; `ExternalFrameBackend` never
composites `regions`, applies `direction_overrides`, or merges a frame's `transforms`
-- it only reads timing/direction/mirroring metadata to pick which file to load. An
author editing any of those on a `source:` asset gets zero findings and a change that
does nothing. These rules make that loud.
"""

from __future__ import annotations

from pixel_forge.domain.paths import safe_join
from pixel_forge.schemas import Finding, SpriteAssetBase
from pixel_forge.validation.engine import RuleContext, make_finding, register

_SPRITE_TYPES = ("character", "enemy", "prop")


def _source_doc(ctx: RuleContext) -> SpriteAssetBase | None:
    doc = ctx.doc
    if not isinstance(doc, SpriteAssetBase) or doc.source is None:
        return None
    return doc


@register(
    "SRC001",
    severity="warning",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A `source:` asset declares `regions`, `direction_overrides`, or per-frame "
        "`transforms`. `ExternalFrameBackend` reads pixels from disk only and ignores "
        "all three, so the edit has no effect."
    ),
)
def _src001(ctx: RuleContext) -> list[Finding]:
    doc = _source_doc(ctx)
    if doc is None:
        return []
    findings: list[Finding] = []
    for region_name in sorted(doc.regions):
        findings.append(
            make_finding(
                ctx,
                "SRC001",
                "warning",
                "deterministic",
                region=region_name,
                message=(
                    f"region {region_name!r} is declared on source-backed asset "
                    f"{doc.asset.id!r}, but the external backend ignores `regions` "
                    "entirely and reads pixels from disk instead"
                ),
                remediation=(
                    f"delete region {region_name!r} (it does nothing), or drop `source:` "
                    "and draw it with the shape DSL"
                ),
                measurements={"kind": "regions", "region": region_name},
            )
        )
    for direction in sorted(doc.direction_overrides):
        for region_name in sorted(doc.direction_overrides[direction]):
            findings.append(
                make_finding(
                    ctx,
                    "SRC001",
                    "warning",
                    "deterministic",
                    direction=direction,
                    region=region_name,
                    message=(
                        f"direction_overrides[{direction!r}][{region_name!r}] is declared on "
                        f"source-backed asset {doc.asset.id!r}, but the external backend "
                        "ignores `direction_overrides` entirely"
                    ),
                    remediation=(
                        "delete this direction_overrides entry (it does nothing) -- each "
                        "direction of a source: asset is its own file"
                    ),
                    measurements={
                        "kind": "direction_overrides",
                        "direction": direction,
                        "region": region_name,
                    },
                )
            )
    for animation_name in sorted(doc.animations):
        anim = doc.animations[animation_name]
        for index, frame in enumerate(anim.frames):
            for region_name in sorted(frame.transforms):
                findings.append(
                    make_finding(
                        ctx,
                        "SRC001",
                        "warning",
                        "deterministic",
                        animation=animation_name,
                        frame=index,
                        region=region_name,
                        message=(
                            f"animation {animation_name!r} frame {index} transforms region "
                            f"{region_name!r} on source-backed asset {doc.asset.id!r}, but the "
                            "external backend ignores per-frame `transforms` entirely"
                        ),
                        remediation=(
                            "delete this frame transform (it does nothing) -- a source: "
                            "asset's motion comes from the frame files themselves"
                        ),
                        measurements={
                            "kind": "transforms",
                            "animation": animation_name,
                            "frame": index,
                            "region": region_name,
                        },
                    )
                )
    return findings


@register(
    "SRC002",
    severity="warning",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A direction listed as a `mirror` target on a `source:` asset also has its own "
        "frame file on disk. The mirror table always wins -- ExternalFrameBackend never "
        "looks up a mirrored direction's own file -- so the file is dead weight."
    ),
)
def _src002(ctx: RuleContext) -> list[Finding]:
    doc = _source_doc(ctx)
    if doc is None or ctx.asset_dir is None:
        return []
    source = doc.source
    assert source is not None
    findings: list[Finding] = []
    for direction in sorted(doc.mirror):
        for animation_name in sorted(doc.animations):
            anim = doc.animations[animation_name]
            for index in range(len(anim.frames)):
                name = source.filename(animation_name, direction, index)
                path = safe_join(ctx.asset_dir, source.frames_dir, name)
                if not path.is_file():
                    continue
                findings.append(
                    make_finding(
                        ctx,
                        "SRC002",
                        "warning",
                        "deterministic",
                        animation=animation_name,
                        direction=direction,
                        frame=index,
                        message=(
                            f"{source.frames_dir}/{name} exists on disk, but direction "
                            f"{direction!r} mirrors {doc.mirror[direction]!r} -- "
                            "ExternalFrameBackend never reads a mirrored direction's own "
                            "file, so this file is never used"
                        ),
                        remediation=(
                            f"delete {source.frames_dir}/{name} (it's ignored), or remove "
                            f"{direction!r} from `mirror` if it should use its own art"
                        ),
                        measurements={
                            "animation": animation_name,
                            "direction": direction,
                            "frame": index,
                            "path": f"{source.frames_dir}/{name}",
                        },
                    )
                )
    return findings
