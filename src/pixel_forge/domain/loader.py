"""YAML <-> pydantic bridge with loud, precise errors. Never silently defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from pixel_forge.errors import SchemaError
from pixel_forge.schemas.asset import AssetDocUnion, parse_asset_doc


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SchemaError(f"malformed YAML in {path}{_yaml_error_location(exc)}: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(
            f"{path}: expected a YAML mapping at the document root, got {type(data).__name__}"
        )
    return data


def dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
    )
    path.write_text(text, encoding="utf-8")


def load_asset_doc(path: Path) -> AssetDocUnion:
    data = load_yaml(path)
    try:
        return parse_asset_doc(data)
    except ValidationError as exc:
        raise SchemaError(_format_validation_error(path, exc)) from exc


def dump_asset_doc(doc: AssetDocUnion, path: Path) -> None:
    data = doc.model_dump(mode="json", exclude_defaults=True)
    data.pop("kind", None)
    dump_yaml(data, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _yaml_error_location(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return ""
    return f" at line {mark.line + 1}, column {mark.column + 1}"


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: {exc.error_count()} validation error(s)"]
    for err in exc.errors():
        field_path = ".".join(str(segment) for segment in err["loc"])
        lines.append(f"  {field_path}: {err['msg']}")
    return "\n".join(lines)
