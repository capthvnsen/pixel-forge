"""Cross-subsystem contracts that no single module's tests can prove on their own.

The semantic-editing promise ("widen the backpack by 2px") is only coherent if a
*spec edit* (`revisions.resize_region`) and a *frame transform* (`RegionTransform.scale_size`)
grow a shape identically. Those live in two modules written independently; this test is
what stops them drifting.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.animation import resolve_frames
from pixel_forge.domain import resolve_palette
from pixel_forge.rendering import LocalRenderBackend
from pixel_forge.revisions import apply_operation
from pixel_forge.schemas import Finding, OperationSpec, ValidationReport, parse_asset_doc


def _doc(scale_size: tuple[int, int] = (0, 0)) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "probe", "type": "character", "canvas": [16, 16], "baseline_y": 15},
            "palette": {
                "id": "p",
                "colors": [{"id": "ink", "hex": "#112233"}, {"id": "skin", "hex": "#aabbcc"}],
            },
            "directions": ["south"],
            "anchors": {"feet": [8, 12]},
            "regions": {
                "pack": {
                    "anchor": "feet",
                    "layer": 10,
                    "shapes": [{"op": "rect", "color": "ink", "at": [-3, -6], "size": [6, 5]}],
                }
            },
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [
                        {"duration_ms": 100, "transforms": {"pack": {"scale_size": scale_size}}}
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _render(doc: Any) -> Any:
    backend = LocalRenderBackend()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    return backend.render_frame(doc, frame, palette)


@pytest.mark.parametrize("delta", [(2, 0), (0, 2), (3, 0), (2, 2), (-2, 0), (3, 3)])
def test_resize_region_matches_scale_size_transform(delta: tuple[int, int]) -> None:
    """A spec-level resize and an equivalent per-frame scale_size must render identically."""
    edited, _inverse = apply_operation(
        _doc(), OperationSpec(name="resize_region", params={"region": "pack", "delta": list(delta)})
    )
    assert _render(edited).equals(_render(_doc(scale_size=delta))), (
        f"resize_region{delta} and scale_size{delta} diverged"
    )


def test_resize_keeps_shape_centre() -> None:
    """Growing by an even delta must not move the shape's midpoint."""
    before = _render(_doc()).bbox()
    after = _render(_doc(scale_size=(2, 2))).bbox()
    assert before is not None and after is not None
    before_centre = (before[0] + before[2], before[1] + before[3])
    after_centre = (after[0] + after[2], after[1] + after[3])
    assert before_centre == after_centre


def test_render_is_deterministic_across_instances() -> None:
    assert _render(_doc()).equals(_render(_doc()))


def test_validation_report_survives_a_dump_load_round_trip() -> None:
    """Revision logs persist reports as JSON; a report that can't be re-read is write-only."""
    finding = Finding(
        rule_id="PIX001",
        severity="error",
        kind="deterministic",
        asset_id="probe",
        message="m",
        remediation="r",
    )
    report = ValidationReport(asset_id="probe", findings=[finding])
    restored = ValidationReport.model_validate(report.model_dump(mode="json"))
    assert restored.findings == report.findings
    assert restored.blocking and restored.error_count == 1

    with pytest.raises(ValidationError):
        ValidationReport.model_validate({"asset_id": "probe", "findings": [], "typo": 1})
