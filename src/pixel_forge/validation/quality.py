"""Machine-readable quality scoring over a validation report (W3-A).

`score_report` is a pure function of a `ValidationReport`: it never re-renders,
never re-runs rules, and never touches the filesystem. It produces a
deterministic 0-100 score with a verdict, plus one `QualityIssue` per finding
carrying a machine `type` label, coordinate-level detail (lifted from
`Finding.measurements["coords"]`), and a `suggested_fix` — a direct repair
instruction an agent can act on.

Scoring: start at 100, subtract per-finding weight by severity
(error=10, warning=3, info=1; overridable via `weights`), floor at 0.
Verdict: >=90 excellent, >=75 good, >=60 needs_work, else poor.
"""

from __future__ import annotations

from collections.abc import Mapping

from pixel_forge.schemas import (
    Finding,
    QualityIssue,
    QualityReport,
    ValidationReport,
    Verdict,
)

# Fine-grained machine `type` labels for rules whose meaning is unambiguous.
# Rules not listed here fall back to their family label (PIX -> raster,
# ANI -> animation, TIL -> terrain).
_FINER_TYPES: Mapping[str, str] = {
    "PIX006": "orphan_pixel",
    "PIX007": "broken_outline",
    "PIX014": "banding",
    "PIX016": "orphan_pixel",
    "PIX017": "noisy_cluster",
    "PIX018": "broken_outline",
    "PIX019": "banding",
    "PIX020": "weak_silhouette",
    "ANI003": "jitter",
    "ANI005": "loop_pop",
    "ANI008": "volume_shift",
    "ANI010": "outline_inconsistent",
    "ANI011": "jitter",
    "ANI012": "volume_shift",
}

_FAMILY_TYPES: Mapping[str, str] = {
    "PIX": "raster",
    "ANI": "animation",
    "TIL": "terrain",
}

_DEFAULT_WEIGHTS: Mapping[str, float] = {"error": 10.0, "warning": 3.0, "info": 1.0}

_VERDICT_THRESHOLDS: tuple[tuple[int, Verdict], ...] = (
    (90, "excellent"),
    (75, "good"),
    (60, "needs_work"),
)

_MAX_COORDS = 100


def issue_type(rule_id: str) -> str:
    """Machine-readable `type` label for a rule id.

    Finer labels (orphan_pixel, banding, jitter, ...) win when the rule clearly
    maps to a specific quality concern; otherwise the rule family is used
    (PIX -> raster, ANI -> animation, TIL -> terrain, anything else -> other).
    """
    finer = _FINER_TYPES.get(rule_id)
    if finer is not None:
        return finer
    family = rule_id[:3]
    return _FAMILY_TYPES.get(family, "other")


def _coords(finding: Finding) -> list[list[int]] | None:
    """Pixel coordinates from `measurements`, validated and capped.

    Prefers the full `coords` list; falls back to a single point from
    `first_x`/`first_y` (PIX006's legacy shape) so every coordinate-localising
    rule produces machine-readable repair guidance.
    """
    raw = finding.measurements.get("coords")
    if not isinstance(raw, list):
        fx = finding.measurements.get("first_x")
        fy = finding.measurements.get("first_y")
        if isinstance(fx, int) and isinstance(fy, int):
            return [[fx, fy]]
        return None
    coords: list[list[int]] = []
    for entry in raw:
        if (
            isinstance(entry, list)
            and len(entry) == 2
            and all(isinstance(value, int) for value in entry)
        ):
            coords.append([entry[0], entry[1]])
        if len(coords) >= _MAX_COORDS:
            break
    return coords or None


def _issue(finding: Finding) -> QualityIssue:
    return QualityIssue(
        type=issue_type(finding.rule_id),
        severity=finding.severity,
        region=finding.region,
        coordinates=_coords(finding),
        frames=[finding.frame] if finding.frame is not None else None,
        suggested_fix=finding.remediation,
        rule_id=finding.rule_id,
    )


def score_report(
    report: ValidationReport,
    weights: Mapping[str, float] | None = None,
) -> QualityReport:
    """Deterministic quality score + repair feedback for a validation report.

    `weights` overrides the per-severity deductions ({error, warning, info}
    keys; unknown severities default to 0 deduction). The result is a pure
    function of `report` and `weights` — identical inputs always produce an
    identical `QualityReport`.
    """
    effective = {**_DEFAULT_WEIGHTS, **(weights or {})}
    score = 100.0
    issues: list[QualityIssue] = []
    for finding in report.findings:
        score -= effective.get(finding.severity, 0.0)
        issues.append(_issue(finding))
    rounded = max(round(score), 0)
    if rounded > 100:
        rounded = 100

    verdict: Verdict = "poor"
    for threshold, label in _VERDICT_THRESHOLDS:
        if rounded >= threshold:
            verdict = label
            break
    return QualityReport(
        asset_id=report.asset_id,
        score=rounded,
        verdict=verdict,
        issues=issues,
    )
