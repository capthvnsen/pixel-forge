"""Project root lifecycle: load, create, and enumerate assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from pixel_forge.domain.loader import dump_asset_doc, dump_yaml, load_asset_doc, load_yaml
from pixel_forge.domain.paths import CONFIG_FILENAME, ProjectPaths, safe_join
from pixel_forge.errors import AssetNotFoundError, ForgeError, SchemaError
from pixel_forge.schemas.asset import AssetDocUnion
from pixel_forge.schemas.project import ProjectConfig


@dataclass(frozen=True)
class Project:
    root: Path
    config: ProjectConfig
    paths: ProjectPaths

    @classmethod
    def load(cls, root: Path) -> Project:
        config_path = safe_join(root, CONFIG_FILENAME)
        if not config_path.is_file():
            raise SchemaError(
                f"no pixel-forge project found at {root} "
                f"(missing {CONFIG_FILENAME}); run `pixel-forge init` first"
            )
        config = _load_config(config_path)
        paths = ProjectPaths(root=config_path.parent, config=config)
        return cls(root=paths.root, config=config, paths=paths)

    @classmethod
    def create(cls, root: Path, name: str) -> Project:
        root.mkdir(parents=True, exist_ok=True)
        config_path = safe_join(root, CONFIG_FILENAME)
        new_config = ProjectConfig(name=name)

        if config_path.is_file():
            existing_config = _load_config(config_path)
            if existing_config != new_config:
                raise ForgeError(f"a different pixel-forge project already exists at {config_path}")
            paths = ProjectPaths(root=config_path.parent, config=existing_config)
            return cls(root=paths.root, config=existing_config, paths=paths)

        dump_yaml(new_config.model_dump(mode="json"), config_path)
        paths = ProjectPaths(root=config_path.parent, config=new_config)
        for rel_dir in (new_config.assets_dir, new_config.build_dir, new_config.references_dir):
            safe_join(root, rel_dir).mkdir(parents=True, exist_ok=True)
        return cls(root=paths.root, config=new_config, paths=paths)

    def discover_assets(self) -> list[str]:
        """Sorted ids of every asset that has a spec file."""
        assets_dir = self.paths.assets_dir
        if not assets_dir.is_dir():
            return []
        ids: list[str] = []
        for entry in assets_dir.iterdir():
            if entry.is_dir() and (entry / f"{entry.name}.yaml").is_file():
                ids.append(entry.name)
        return sorted(ids)

    def load_asset(self, asset_id: str) -> AssetDocUnion:
        spec_path = self.paths.asset_spec(asset_id)
        if not spec_path.is_file():
            raise AssetNotFoundError(f"no asset {asset_id!r} in project at {self.root}")
        return load_asset_doc(spec_path)

    def save_asset(self, doc: AssetDocUnion) -> Path:
        spec_path = self.paths.asset_spec(doc.asset.id)
        dump_asset_doc(doc, spec_path)
        return spec_path


def _load_config(config_path: Path) -> ProjectConfig:
    data = load_yaml(config_path)
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise SchemaError(f"{config_path}: invalid project config\n{exc}") from exc
