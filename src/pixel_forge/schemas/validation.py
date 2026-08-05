"""Validation findings and reports produced by the validation engine (Task 8)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

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


_COMPUTED_FIELDS = frozenset({"blocking", "error_count", "warning_count"})


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_computed(cls, data: Any) -> Any:
        """Let a dumped report be re-validated.

        `model_dump()` emits the computed fields below, but `extra="forbid"` would then
        reject them on the way back in — so a report could be serialised and never read
        again. Dropping them here keeps `extra="forbid"` catching genuine typos while
        making dump/load round-trip (revision logs persist reports this way).
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in _COMPUTED_FIELDS}
        return data

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
