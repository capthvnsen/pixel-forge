"""Write JSON Schema files for the public pydantic models, deterministically."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from pixel_forge.schemas.asset import CharacterAsset, EnemyAsset, PropAsset, TerrainAsset
from pixel_forge.schemas.manifest import GodotManifest
from pixel_forge.schemas.project import ProjectConfig
from pixel_forge.schemas.revision import RevisionRecord
from pixel_forge.schemas.style import StyleProfile
from pixel_forge.schemas.validation import ValidationReport

_TARGETS: list[tuple[str, type[BaseModel]]] = [
    ("character", CharacterAsset),
    ("enemy", EnemyAsset),
    ("prop", PropAsset),
    ("terrain", TerrainAsset),
    ("validation_report", ValidationReport),
    ("godot_manifest", GodotManifest),
    ("style_profile", StyleProfile),
    ("revision_record", RevisionRecord),
    ("project_config", ProjectConfig),
]


def export_json_schemas(out_dir: Path) -> list[Path]:
    """Write `<name>.schema.json` for each public model into `out_dir`.

    Output is deterministic: `json.dumps(..., indent=2, sort_keys=True)` plus a
    trailing newline, so re-running produces byte-identical files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, model in _TARGETS:
        schema = model.model_json_schema()
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        path = out_dir / f"{name}.schema.json"
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths
