"""Starter component library (W3-B): reusable spec fragments agents assemble.

A component is a YAML spec fragment declaring one or more regions (with the
same shape DSL the rest of the engine uses) plus the anchor every region hangs
off. The `anchor` field is a *placeholder* name (by convention ``"@attach"``)
that the ``add_component`` / ``replace_component`` revision operations rewrite
to a real anchor of the target doc at insertion time — a component must never
hard-code another doc's anchor names, or it could not be attached to a doc
that names its anchors differently.

Components reference palette color ids by name (``leather_mid``,
``metal_light``, ``outline``, ...). They are deliberately authored against the
curated material ramp ids in ``references/curated.py`` so that
``add_component`` can extend the target doc's palette with any missing colour
from a curated palette, keeping every inserted shape on an approved,
palette-quantized colour.

Usage::

    from pixel_forge.components import available_components, load_component
    backpack = load_component("backpack_simple")
"""

from __future__ import annotations

from importlib import resources

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pixel_forge.errors import OperationError
from pixel_forge.schemas.common import Region

#: Files that make up the starter library, in load order.
_COMPONENT_FILES = (
    "backpack_simple.yaml",
    "helmet_round.yaml",
    "shield_round.yaml",
    "sword_basic.yaml",
)


class ComponentSpec(BaseModel):
    """A validated component fragment: regions keyed by name, all anchored at `anchor`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    #: The placeholder anchor name every region in the fragment hangs off.
    anchor: str
    regions: dict[str, Region] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_region_anchors(self) -> ComponentSpec:
        for name, region in self.regions.items():
            if region.anchor != self.anchor:
                raise ValueError(
                    f"component region {name!r} anchors at {region.anchor!r}, but the "
                    f"component declares anchor {self.anchor!r}"
                )
        return self

    @property
    def color_ids(self) -> set[str]:
        """Every palette color id the component's shapes reference."""
        ids: set[str] = set()
        for region in self.regions.values():
            for shape in region.shapes:
                data = shape.model_dump(mode="json")
                if data["op"] == "bitmap":
                    key = data.get("key")
                    if isinstance(key, dict):
                        ids.update(cid for cid in key.values() if isinstance(cid, str))
                else:
                    ids.add(data["color"])
        return ids


def _load_components() -> dict[str, ComponentSpec]:
    result: dict[str, ComponentSpec] = {}
    for fname in _COMPONENT_FILES:
        text = resources.files("pixel_forge.components").joinpath(fname).read_text(
            encoding="utf-8"
        )
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise OperationError(f"component file {fname!r} must contain a mapping")
        try:
            spec = ComponentSpec.model_validate(data)
        except ValidationError as exc:
            raise OperationError(f"component file {fname!r} failed validation: {exc}") from exc
        if spec.id != fname.removesuffix(".yaml"):
            raise OperationError(
                f"component file {fname!r} declares id {spec.id!r}; the id must match "
                "the filename"
            )
        if spec.id in result:
            raise OperationError(f"duplicate component id {spec.id!r}")
        result[spec.id] = spec
    return result


_COMPONENTS: dict[str, ComponentSpec] = _load_components()


def available_components() -> tuple[str, ...]:
    """Ids of every component in the starter library, sorted."""
    return tuple(sorted(_COMPONENTS))


def load_component(name: str) -> ComponentSpec:
    """Return the validated component with id `name`.

    Raises `OperationError` for an unknown name, so callers never touch the
    raw YAML dicts.
    """
    spec = _COMPONENTS.get(name)
    if spec is None:
        raise OperationError(
            f"unknown component {name!r}; available: {', '.join(available_components())}"
        )
    return spec


__all__ = [
    "ComponentSpec",
    "available_components",
    "load_component",
]
