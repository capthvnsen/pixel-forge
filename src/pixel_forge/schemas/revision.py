"""Revision operations and the append-only revision log (Task 10)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pixel_forge.schemas.validation import ValidationReport

type JSONScalar = float | int | str | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]


class OperationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, JSONValue] = Field(default_factory=dict)
    targets: dict[str, list[str]] = Field(default_factory=dict)  # regions/directions/... -> names
    protect: list[str] = Field(default_factory=list)  # anchor/region names that must not change


class RevisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    parent_revision: str | None = None
    timestamp: str  # ISO-8601, supplied by the caller — never generated here
    operation: OperationSpec
    inverse: OperationSpec | None = None
    asset_id: str
    affected_regions: list[str] = Field(default_factory=list)
    affected_frames: list[int] = Field(default_factory=list)
    affected_directions: list[str] = Field(default_factory=list)
    hash_before: str
    hash_after: str
    validation: ValidationReport | None = None


class RevisionDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    revision_a: str
    revision_b: str
    operations: list[OperationSpec] = Field(default_factory=list)  # a -> b, in order
    affected_regions: list[str] = Field(default_factory=list)
    affected_frames: list[int] = Field(default_factory=list)
    affected_directions: list[str] = Field(default_factory=list)
    hash_a: str
    hash_b: str
