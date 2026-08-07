"""Animation specs: frames, per-frame region transforms, procedural shader hooks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixel_forge.schemas.common import RegionTransform

#: Easing curves a frame can request for its offset interpolation in timeline
#: sampling. Names resolve to pure functions in `pixel_forge.animation.timeline`
#: (`EASING_CURVES`); `None`/`"linear"` is the default and interpolates with no
#: reshaping (byte-identical to pre-easing rendering).
EasingName = Literal["linear", "ease_in", "ease_out", "ease_in_out", "bounce"]


class FrameSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int
    events: list[str] = Field(default_factory=list)
    transforms: dict[str, RegionTransform] = Field(default_factory=dict)  # region -> transform
    # Animation-quality metadata (optional, defaults keep existing specs
    # byte-identical):
    # - `easing`: the curve used to interpolate INTO this frame from the previous
    #   one when the frames are sampled as a timeline (default: linear).
    # - `hold`: snap to this frame's pose for its whole segment instead of
    #   interpolating towards it.
    easing: EasingName | None = None
    hold: bool = False

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
        # A procedural shader may supply the frames (expanded deterministically at
        # parse/resolution time); a plain animation must hand-author at least one.
        if len(self.frames) < 1 and self.procedural is None:
            raise ValueError("an animation must have at least 1 frame")
        return self
