"""Rule registry and validation runner.

Rules are plain functions registered with `@register(...)`. `run_validation`
runs every rule whose `applies_to` matches the document's asset type (subject
to `only`/`skip` filtering), in sorted `rule_id` order, and returns all
findings in a stable (rule_id, direction, animation, frame, region) order so
reports diff cleanly across runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.domain.palette import ResolvedPalette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas import AssetDocUnion, Finding, Severity, ValidationReport

RuleKind = Literal["deterministic", "heuristic"]


@dataclass(frozen=True)
class RuleContext:
    doc: AssetDocUnion
    palette: ResolvedPalette
    frames: Mapping[tuple[str, str, int], Canvas]  # (animation, direction, index) -> canvas
    resolved: Sequence[ResolvedFrame]
    tiles: Mapping[str, Canvas]  # terrain only, empty for sprites
    # The asset's own directory, for rules that need to check the filesystem directly
    # (e.g. SRC002 checking for a stray frame file) rather than a rendered Canvas. None
    # in tests that build a RuleContext without a real project on disk.
    asset_dir: Path | None = None
    # How many rows of the render-polish contact-shadow band the renderer appended
    # below the sprite's ground line (0 = no polish, or the shadow disabled). Lets
    # ANI001 measure the sprite's own baseline instead of the shadow's bottom edge.
    polish_shadow_rows: int = 0


Rule = Callable[[RuleContext], list[Finding]]


@dataclass(frozen=True)
class RuleMeta:
    rule_id: str
    severity: Severity
    kind: RuleKind
    applies_to: tuple[str, ...]
    description: str


_REGISTRY: dict[str, tuple[RuleMeta, Rule]] = {}


def register(
    rule_id: str,
    *,
    severity: Severity,
    kind: RuleKind,
    applies_to: tuple[str, ...],
    description: str,
) -> Callable[[Rule], Rule]:
    """Register `rule_id` in the module-level rule registry.

    `severity` is the rule's default; an individual finding may still report a
    different severity (e.g. downgraded to warning when a `validation` option
    relaxes the check).
    """

    def decorator(fn: Rule) -> Rule:
        if rule_id in _REGISTRY:
            raise ValueError(f"rule_id {rule_id!r} is already registered")
        meta = RuleMeta(
            rule_id=rule_id,
            severity=severity,
            kind=kind,
            applies_to=applies_to,
            description=description,
        )
        _REGISTRY[rule_id] = (meta, fn)
        return fn

    return decorator


def registered_rules() -> list[RuleMeta]:
    return [_REGISTRY[rule_id][0] for rule_id in sorted(_REGISTRY)]


def make_finding(
    ctx: RuleContext,
    rule_id: str,
    severity: Severity,
    kind: RuleKind,
    *,
    animation: str | None = None,
    direction: str | None = None,
    frame: int | None = None,
    region: str | None = None,
    message: str,
    remediation: str,
    measurements: dict[str, Any],
) -> Finding:
    """Shared `Finding` constructor so every rule stamps `asset_id` consistently."""
    return Finding(
        rule_id=rule_id,
        severity=severity,
        kind=kind,
        asset_id=ctx.doc.asset.id,
        direction=direction,
        animation=animation,
        frame=frame,
        region=region,
        message=message,
        remediation=remediation,
        measurements=measurements,
    )


def _finding_sort_key(finding: Finding) -> tuple[str, str, str, int, str]:
    return (
        finding.rule_id,
        finding.direction or "",
        finding.animation or "",
        finding.frame if finding.frame is not None else -1,
        finding.region or "",
    )


def run_validation(
    ctx: RuleContext,
    *,
    only: Sequence[str] | None = None,
    skip: Sequence[str] | None = None,
) -> ValidationReport:
    asset_type = ctx.doc.asset.type
    only_set = set(only) if only is not None else None
    skip_set = set(skip) if skip is not None else set()

    findings: list[Finding] = []
    for rule_id in sorted(_REGISTRY):
        meta, fn = _REGISTRY[rule_id]
        if asset_type not in meta.applies_to:
            continue
        if only_set is not None and rule_id not in only_set:
            continue
        if rule_id in skip_set:
            continue
        try:
            findings.extend(fn(ctx))
        except Exception as exc:
            findings.append(
                make_finding(
                    ctx,
                    "ENG001",
                    "error",
                    "deterministic",
                    message=f"validation rule {rule_id!r} raised {type(exc).__name__}: {exc}",
                    remediation=f"fix or temporarily skip rule {rule_id!r} (validation/rules_*.py)",
                    measurements={"failing_rule": rule_id},
                )
            )

    findings.sort(key=_finding_sort_key)
    return ValidationReport(asset_id=ctx.doc.asset.id, findings=findings)
