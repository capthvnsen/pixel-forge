"""End-to-end quality flow: api.quality_asset over the example project."""

from __future__ import annotations

from pathlib import Path

from pixel_forge import api
from pixel_forge.schemas import QualityIssue, QualityReport

EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"

_VALID_VERDICTS = {"excellent", "good", "needs_work", "poor"}


def _assert_well_formed(report: QualityReport) -> None:
    assert isinstance(report, QualityReport)
    assert report.asset_id
    assert isinstance(report.score, int)
    assert 0 <= report.score <= 100
    assert report.verdict in _VALID_VERDICTS
    for issue in report.issues:
        assert isinstance(issue, QualityIssue)
        assert issue.type
        assert issue.severity in {"error", "warning", "info"}
        assert issue.rule_id
        assert issue.suggested_fix
        if issue.coordinates is not None:
            assert all(len(coord) == 2 for coord in issue.coordinates)


def test_quality_asset_engineer_is_well_formed() -> None:
    report = api.quality_asset(EXAMPLES_ROOT, "engineer")
    _assert_well_formed(report)
    # engineer has known non-zero warning/info findings; the report must
    # surface them as issues, and they must never be blocking errors.
    assert report.issues
    assert all(issue.severity != "error" for issue in report.issues)


def test_quality_asset_engineer_issues_carry_coordinates_when_available() -> None:
    report = api.quality_asset(EXAMPLES_ROOT, "engineer")
    # Post-calibration contract: the pixel-level quality rules (PIX016-019)
    # are authored-art checks and stay silent on the render-polish pass's own
    # deterministic output, so a polished example is NOT expected to surface
    # coordinate issues. What they do guarantee is that any finding they emit
    # localises coordinates — covered by the unit tests on unpolished frames
    # (test_pix016/017/018/019 in test_validation_pixel.py). Here we assert the
    # calibrated silence: engineer's remaining issues are pre-existing
    # animation warnings, none of which are error-severity.
    assert all(issue.severity != "error" for issue in report.issues)
    for issue in report.issues:
        # If a coordinate-localising rule ever fires on a polished frame, it
        # MUST carry coordinates (never a bare message).
        if issue.rule_id in {"PIX016", "PIX017", "PIX018", "PIX019"}:
            assert issue.coordinates


def test_quality_asset_forest_tileset_is_well_formed() -> None:
    report = api.quality_asset(EXAMPLES_ROOT, "forest_tileset")
    _assert_well_formed(report)
    # Terrain validation is TIL-only, so every issue maps to the terrain family.
    assert all(issue.type == "terrain" for issue in report.issues)


def test_quality_asset_is_deterministic() -> None:
    first = api.quality_asset(EXAMPLES_ROOT, "engineer")
    second = api.quality_asset(EXAMPLES_ROOT, "engineer")
    assert first == second


def test_quality_asset_json_round_trip() -> None:
    report = api.quality_asset(EXAMPLES_ROOT, "engineer")
    restored = QualityReport.model_validate_json(report.model_dump_json())
    assert restored == report


def test_quality_asset_accepts_art_direction() -> None:
    from pixel_forge.schemas import ArtDirection

    art = ArtDirection(
        outline_width=1,
        ground_shadow_enabled=True,
        ground_shadow_strength=64,
        ground_shadow_rows=2,
    )
    report = api.quality_asset(EXAMPLES_ROOT, "engineer", art_direction=art)
    _assert_well_formed(report)
