"""Project-level configuration (`pixel-forge.yaml` at a project root)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str
    godot_baseline: str = "4.4"
    assets_dir: str = "assets"
    build_dir: str = "build"
    references_dir: str = "references"
    default_palette: str | None = None
