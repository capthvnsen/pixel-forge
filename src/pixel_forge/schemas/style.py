"""Style profiles distilled from reference art (Task 11)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    role: Literal["approved", "inspiration", "palette", "animation", "rejected"]
    notes: str = ""


class StyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    perspective: str = ""
    pixel_density: str = ""
    palette_tendencies: str = ""
    outline_style: str = ""
    light_direction: str = ""
    material_treatment: str = ""
    silhouette_complexity: str = ""
    texture_density: str = ""
    animation_timing: str = ""
    shape_language: str = ""
    environmental_hierarchy: str = ""
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
