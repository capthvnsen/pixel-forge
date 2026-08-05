"""Validation findings and reports produced by the validation engine (Task 8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Severity = Literal["error", "warning", "info"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity
    kind: Literal["deterministic", "heuristic"]
    asset_id: str
    direction: str | None = None
    animation: str | None = None
    frame: int | None = None
    region: str | None = None
    message: str
    remediation: str
    measurements: dict[str, float | int | str] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    findings: list[Finding] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocking(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_text(self) -> str:
        if not self.findings:
            return f"{self.asset_id}: no findings"
        lines = [f"{self.asset_id}: {self.error_count} error(s), {self.warning_count} warning(s)"]
        for f in self.findings:
            location = ".".join(
                str(part)
                for part in (f.direction, f.animation, f.frame, f.region)
                if part is not None
            )
            location_str = f" [{location}]" if location else ""
            lines.append(f"  {f.severity.upper()} {f.rule_id}{location_str}: {f.message}")
        return "\n".join(lines)
