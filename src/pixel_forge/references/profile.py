"""Reference library scaffolding and style profile management (Task 11).

Workflow this module supports, for a vision-capable agent operating this
toolkit: look at the image files a human has dropped under a project's
`references/{approved,inspiration,palettes,animation,rejected}/` directories
(created by `scaffold_references`), form an opinion about the art style, then
call `update_profile` with the observed style parameters (`palette_tendencies`,
`outline_style`, `light_direction`, ...) plus `ProvenanceEntry` records naming
which reference file(s) informed which field. This toolkit performs no image
analysis of its own — `references/profile.py` only stores and merges the
structured judgements an agent (or a human) supplies about what it saw.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pixel_forge.domain.loader import dump_yaml, load_yaml
from pixel_forge.domain.paths import safe_join
from pixel_forge.errors import ForgeError
from pixel_forge.schemas.style import ProvenanceEntry, StyleProfile

_REFERENCE_SUBDIRS: tuple[str, ...] = (
    "approved",
    "inspiration",
    "palettes",
    "animation",
    "rejected",
)

_POLICY_PARAGRAPH = (
    "Do not trace, copy, or reproduce protected artwork, exact compositions, or "
    "recognisable characters from any reference in this directory. References inform "
    "style parameters only: palette tendencies, outline treatment, light direction, "
    "timing, and shape language. Producing a derivative that reproduces a specific "
    "protected asset is out of scope for this toolkit and is not a supported workflow."
)


def _readme_text(subdir: str) -> str:
    return f"# references/{subdir}\n\n{_POLICY_PARAGRAPH}\n"


def scaffold_references(root: Path) -> list[Path]:
    """Ensure `references/{approved,inspiration,palettes,animation,rejected}` all exist.

    Each directory gets a `README.md` carrying the no-tracing policy verbatim,
    written only if not already present — a file under `references/approved/`
    (or any other subdirectory) is never overwritten. Idempotent: safe to call
    on every project load.
    """
    created_dirs: list[Path] = []
    for subdir in _REFERENCE_SUBDIRS:
        dir_path = safe_join(root, "references", subdir)
        dir_path.mkdir(parents=True, exist_ok=True)
        readme_path = safe_join(root, "references", subdir, "README.md")
        if not readme_path.is_file():
            readme_path.write_text(_readme_text(subdir), encoding="utf-8")
        created_dirs.append(dir_path)
    return created_dirs


def _profile_path(root: Path) -> Path:
    return safe_join(root, "references", "style_profile.yaml")


def create_profile(root: Path, profile: StyleProfile, *, overwrite: bool = False) -> Path:
    """Write a fresh `references/style_profile.yaml`, refusing to clobber an existing one."""
    profile_path = _profile_path(root)
    if profile_path.is_file() and not overwrite:
        raise ForgeError(
            f"a style profile already exists at {profile_path}; pass overwrite=True to replace it"
        )
    dump_yaml(profile.model_dump(mode="json"), profile_path)
    return profile_path


def load_profile(root: Path) -> StyleProfile:
    profile_path = _profile_path(root)
    if not profile_path.is_file():
        raise ForgeError(f"no style profile found at {profile_path}; call create_profile first")
    data = load_yaml(profile_path)
    try:
        return StyleProfile.model_validate(data)
    except ValidationError as exc:
        raise ForgeError(f"{profile_path}: invalid style profile\n{exc}") from exc


def update_profile(
    root: Path, changes: Mapping[str, Any], *, provenance: Sequence[ProvenanceEntry] = ()
) -> StyleProfile:
    """Shallow-merge `changes` into the existing profile, append de-duped `provenance`, save.

    Provenance entries are deduplicated on `(source_path, role)`, keeping the
    first-seen entry for each key and preserving encounter order.
    """
    profile = load_profile(root)
    data = profile.model_dump(mode="json")
    data.update(changes)

    merged_provenance = list(profile.provenance)
    seen = {(entry.source_path, entry.role) for entry in merged_provenance}
    for entry in provenance:
        key = (entry.source_path, entry.role)
        if key not in seen:
            merged_provenance.append(entry)
            seen.add(key)
    data["provenance"] = [entry.model_dump(mode="json") for entry in merged_provenance]

    try:
        updated = StyleProfile.model_validate(data)
    except ValidationError as exc:
        raise ForgeError(f"invalid style profile update: {exc}") from exc
    dump_yaml(updated.model_dump(mode="json"), _profile_path(root))
    return updated


def list_references(root: Path) -> dict[str, list[str]]:
    """Sorted, README-excluded file listing for each of the five reference directories."""
    result: dict[str, list[str]] = {}
    for subdir in _REFERENCE_SUBDIRS:
        dir_path = safe_join(root, "references", subdir)
        names: list[str] = []
        if dir_path.is_dir():
            for entry in dir_path.iterdir():
                if entry.is_file() and entry.name != "README.md":
                    names.append(entry.name)
        result[subdir] = sorted(names)
    return result
