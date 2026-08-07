"""Quality scoring + repair feedback: score_report / issue_type / QualityReport."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pixel_forge.schemas import Finding, QualityReport, ValidationReport
from pixel_forge.validation.quality import issue_type, score_report


def _finding(
    *,
    rule_id: str = "PIX001",
    severity: str = "error",
    kind: str = "deterministic",
    remediation: str = "fix it",
    frame: int | None = None,
    region: str | None = None,
    coords: list[list[int]] | None = None,
) -> Finding:
    measurements: dict[str, object] = {}
    if coords is not None:
        measurements["coords"] = coords
    return Finding(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        asset_id="hero",
        frame=frame,
        region=region,
        message="m",
        remediation=remediation,
        measurements=measurements,
    )


def _report(*findings: Finding) -> ValidationReport:
    return ValidationReport(asset_id="hero", findings=list(findings))


# --- scoring -----------------------------------------------------------------


def test_empty_report_scores_100_excellent() -> None:
    report = score_report(_report())
    assert report.asset_id == "hero"
    assert report.score == 100
    assert report.verdict == "excellent"
    assert report.issues == []


@pytest.mark.parametrize(
    ("severity", "expected_score"),
    [("error", 90), ("warning", 97), ("info", 99)],
)
def test_severity_deductions(severity: str, expected_score: int) -> None:
    report = score_report(_report(_finding(severity=severity)))
    assert report.score == expected_score


def test_multiple_findings_accumulate() -> None:
    report = score_report(
        _report(
            _finding(severity="error"),
            _finding(severity="warning"),
            _finding(severity="info"),
        )
    )
    assert report.score == 86  # 100 - 10 - 3 - 1


def test_score_floors_at_zero() -> None:
    report = score_report(_report(*[_finding(severity="error") for _ in range(11)]))
    assert report.score == 0
    assert report.verdict == "poor"


@pytest.mark.parametrize(
    ("score", "verdict"),
    [
        (100, "excellent"),
        (90, "excellent"),
        (89, "good"),
        (75, "good"),
        (74, "needs_work"),
        (60, "needs_work"),
        (59, "poor"),
        (0, "poor"),
    ],
)
def test_verdict_thresholds(score: int, verdict: str) -> None:
    # Custom weights let a single finding land the score exactly on a threshold.
    report = score_report(_report(_finding(severity="warning")), weights={"warning": 100 - score})
    assert report.score == score
    assert report.verdict == verdict


def test_custom_weights_override_deductions() -> None:
    report = score_report(_report(_finding(severity="warning")), weights={"warning": 5.0})
    assert report.score == 95
    # Unknown severity names are ignored rather than crashing.
    report2 = score_report(_report(_finding(severity="error")), weights={"bogus": 99.0})
    assert report2.score == 90


def test_score_report_is_pure_and_deterministic() -> None:
    report = _report(
        _finding(rule_id="PIX016", severity="warning", coords=[[3, 3]]),
        _finding(rule_id="ANI011", severity="warning", frame=1),
    )
    first = score_report(report)
    second = score_report(report)
    assert first == second


# --- issue mapping -----------------------------------------------------------


def test_issue_carries_repair_feedback() -> None:
    report = score_report(
        _report(
            _finding(
                rule_id="PIX016",
                severity="warning",
                frame=2,
                region="helmet",
                remediation="remove the stray pixel",
                coords=[[3, 3], [4, 4]],
            )
        )
    )
    issue = report.issues[0]
    assert issue.type == "orphan_pixel"
    assert issue.severity == "warning"
    assert issue.region == "helmet"
    assert issue.coordinates == [[3, 3], [4, 4]]
    assert issue.frames == [2]
    assert issue.suggested_fix == "remove the stray pixel"
    assert issue.rule_id == "PIX016"


def test_issue_without_coordinates_or_frame() -> None:
    report = score_report(_report(_finding(rule_id="PIX005")))
    issue = report.issues[0]
    assert issue.coordinates is None
    assert issue.frames is None


def test_malformed_coords_are_dropped_not_crashed() -> None:
    finding = _finding(rule_id="PIX016")
    finding.measurements["coords"] = "not-a-list"  # type: ignore[assignment]
    report = score_report(_report(finding))
    assert report.issues[0].coordinates is None
    mixed = _finding(rule_id="PIX016")
    mixed.measurements["coords"] = [[1, 2], ["x", 2]]  # type: ignore[list-item]
    report2 = score_report(_report(mixed))
    assert report2.issues[0].coordinates == [[1, 2]]


@pytest.mark.parametrize(
    ("rule_id", "expected"),
    [
        ("PIX016", "orphan_pixel"),
        ("PIX018", "broken_outline"),
        ("PIX019", "banding"),
        ("PIX014", "banding"),
        ("PIX020", "weak_silhouette"),
        ("PIX017", "noisy_cluster"),
        ("ANI011", "jitter"),
        ("ANI010", "outline_inconsistent"),
        ("ANI012", "volume_shift"),
        ("PIX001", "raster"),
        ("ANI001", "animation"),
        ("TIL001", "terrain"),
        ("ENG001", "other"),
    ],
)
def test_issue_type_mapping(rule_id: str, expected: str) -> None:
    assert issue_type(rule_id) == expected


# --- schema strictness --------------------------------------------------------


def test_quality_report_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        QualityReport.model_validate(
            {"asset_id": "hero", "score": 90, "verdict": "excellent", "typo": 1}
        )


def test_quality_report_rejects_bad_verdict() -> None:
    with pytest.raises(ValidationError):
        QualityReport.model_validate(
            {"asset_id": "hero", "score": 90, "verdict": "stellar"}
        )


def test_quality_report_round_trips_json() -> None:
    report = score_report(
        _report(_finding(rule_id="PIX016", severity="warning", coords=[[1, 1]]))
    )
    restored = QualityReport.model_validate_json(report.model_dump_json())
    assert restored == report
