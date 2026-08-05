"""Animation specs: frames, per-frame region transforms, procedural shader hooks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixel_forge.schemas.common import RegionTransform


class FrameSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int
    events: list[str] = Field(default_factory=list)
    transforms: dict[str, RegionTransform] = Field(default_factory=dict)  # region -> transform

    @model_validator(mode="after")
    def _check_duration(self) -> FrameSpec:
        if self.duration_ms <= 0:
            raise ValueError(f"duration_ms must be > 0, got {self.duration_ms}")
        return self


class ProceduralAnimationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shader: str
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    target_region: str | None = None


class AnimationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop: bool = True
    frames: list[FrameSpec]
    procedural: ProceduralAnimationSpec | None = None

    @model_validator(mode="after")
    def _check_frame_count(self) -> AnimationSpec:
        if len(self.frames) < 1:
            raise ValueError("an animation must have at least 1 frame")
        return self
