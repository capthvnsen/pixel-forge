"""Filesystem path safety: every path CLI/MCP touches routes through here.

`safe_join` is the sole security boundary: it resolves the joined path against
the resolved project root and rejects anything that lands outside it, whether
via `..`, an absolute component, a literal `~` (never expanded), or a symlink
whose target escapes root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pixel_forge.errors import PathSecurityError
from pixel_forge.schemas.project import ProjectConfig

_ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_ASSET_ID_MAX_LEN = 64

CONFIG_FILENAME = "pixel-forge.yaml"


def safe_join(root: Path, *parts: str) -> Path:
    """Join `parts` onto `root` and guarantee the result stays inside `root`.

    Resolves `root` and the candidate path, then requires the candidate to be
    `root` itself or a descendant of it. Resolution walks symlinks for every
    existing path component (the standard `Path.resolve()`/`realpath`
    behaviour), so a symlink whose target escapes root is caught even when the
    requested leaf does not exist yet, because only the nonexistent tail is
    left unresolved and everything before it is still dereferenced.
    """
    for part in parts:
        if "\x00" in part:
            raise PathSecurityError(f"NUL byte in path component: {part!r}")

    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*parts)
    try:
        resolved = candidate.resolve()
    except ValueError as exc:
        raise PathSecurityError(f"could not resolve path {candidate!r}: {exc}") from exc

    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise PathSecurityError(f"path escapes project root {root_resolved}: {resolved}")
    return resolved


def validate_asset_id(asset_id: str) -> str:
    """Validate an asset id used as a path component. Returns it unchanged."""
    if "\x00" in asset_id:
        raise PathSecurityError(f"asset id contains a NUL byte: {asset_id!r}")
    if len(asset_id) > _ASSET_ID_MAX_LEN or not _ASSET_ID_RE.match(asset_id):
        raise PathSecurityError(
            f"invalid asset id: {asset_id!r} (must match {_ASSET_ID_RE.pattern!r}, "
            f"max {_ASSET_ID_MAX_LEN} chars)"
        )
    return asset_id


@dataclass(frozen=True)
class ProjectPaths:
    """Every filesystem location inside a pixel-forge project, root + config."""

    root: Path
    config: ProjectConfig

    @property
    def config_file(self) -> Path:
        return safe_join(self.root, CONFIG_FILENAME)

    @property
    def assets_dir(self) -> Path:
        return safe_join(self.root, self.config.assets_dir)

    @property
    def build_dir(self) -> Path:
        return safe_join(self.root, self.config.build_dir)

    @property
    def references_dir(self) -> Path:
        return safe_join(self.root, self.config.references_dir)

    def asset_dir(self, asset_id: str) -> Path:
        asset_id = validate_asset_id(asset_id)
        return safe_join(self.root, self.config.assets_dir, asset_id)

    def asset_spec(self, asset_id: str) -> Path:
        asset_id = validate_asset_id(asset_id)
        return safe_join(self.root, self.config.assets_dir, asset_id, f"{asset_id}.yaml")

    def asset_revisions(self, asset_id: str) -> Path:
        asset_id = validate_asset_id(asset_id)
        return safe_join(self.root, self.config.assets_dir, asset_id, "revisions.jsonl")

    def build_asset_dir(self, asset_id: str) -> Path:
        asset_id = validate_asset_id(asset_id)
        return safe_join(self.root, self.config.build_dir, asset_id)

    def build_godot_dir(self) -> Path:
        return safe_join(self.root, self.config.build_dir, "godot")
