"""Static verification of the Godot plugin (godot/addons/pixel_asset_forge/).

Godot may or may not be installed wherever this test runs, so it does the strongest
verification possible without launching Godot: plugin files exist and declare the 4.4
baseline; every manifest key each `.gd` file reads via `.get("...")` is a real field
somewhere in `pixel_forge.schemas.manifest` (catching drift between the exporter's
schema and the plugin without running either); the importer only ever writes under
`res://generated/`; the two golden fixture manifests are structurally well-formed per a
Python re-implementation of `manifest_validator.gd`'s checks; and the headless wrapper
script is executable and degrades cleanly when `godot` isn't on `PATH`.

See docs/godot.md for what this does *not* prove, and for the manual, hand-verified
account of actually running the plugin under a real Godot install.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pixel_forge.schemas import manifest as manifest_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "godot" / "addons" / "pixel_asset_forge"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "fixtures" / "godot"
WRAPPER_SCRIPT = REPO_ROOT / "tools" / "godot_headless_import.sh"

PLUGIN_FILES = [
    REPO_ROOT / "godot" / "project.godot",
    PLUGIN_DIR / "plugin.cfg",
    PLUGIN_DIR / "plugin.gd",
    PLUGIN_DIR / "importer.gd",
    PLUGIN_DIR / "manifest_validator.gd",
    PLUGIN_DIR / "dock.gd",
    PLUGIN_DIR / "dock.tscn",
    PLUGIN_DIR / "headless_import.gd",
]

GD_SCRIPTS = [p for p in PLUGIN_FILES if p.suffix == ".gd"]

# `textures` (and `procedural`, `pivots`, `events`) are `dict[str, ...]` with free-form
# keys in the schema -- "atlas" is the exporter's naming convention for the sole texture,
# not a pydantic field name, so it can never appear in `_schema_field_names()` below.
FREE_FORM_DICT_KEYS = {"atlas"}


def _schema_field_names() -> set[str]:
    """Every field name declared by any pydantic model in `schemas/manifest.py`."""
    names: set[str] = set()
    for value in vars(manifest_schema).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            names.update(value.model_fields.keys())
    return names


def _manifest_keys_read(source: str) -> set[str]:
    """Every string literal used as a `.get("key")` lookup in a `.gd` source file."""
    return set(re.findall(r'\.get\(\s*"([a-zA-Z_][a-zA-Z0-9_]*)"', source))


def test_plugin_files_exist_and_are_nonempty() -> None:
    for path in PLUGIN_FILES:
        assert path.is_file(), f"missing plugin file: {path}"
        assert path.stat().st_size > 0, f"empty plugin file: {path}"


def test_project_godot_declares_44_baseline_and_enables_plugin() -> None:
    text = (REPO_ROOT / "godot" / "project.godot").read_text()
    assert 'PackedStringArray("4.4")' in text
    assert "pixel_asset_forge/plugin.cfg" in text


def test_plugin_cfg_declares_44_baseline() -> None:
    text = (PLUGIN_DIR / "plugin.cfg").read_text()
    assert "4.4" in text
    assert 'script="plugin.gd"' in text


def test_gd_scripts_only_read_manifest_keys_the_schema_actually_produces() -> None:
    schema_fields = _schema_field_names() | FREE_FORM_DICT_KEYS
    drift: dict[str, set[str]] = {}
    for script in GD_SCRIPTS:
        unknown = _manifest_keys_read(script.read_text()) - schema_fields
        if unknown:
            drift[script.name] = unknown
    assert not drift, f"plugin reads manifest keys the schema does not define: {drift}"


def test_manifest_keys_read_is_a_nonempty_sanity_check() -> None:
    # Guards the extraction regex itself: if it ever stops matching anything, the drift
    # test above would trivially pass for the wrong reason.
    keys = _manifest_keys_read((PLUGIN_DIR / "importer.gd").read_text())
    assert {"asset_id", "asset_type", "manifest_version"} <= _manifest_keys_read(
        (PLUGIN_DIR / "manifest_validator.gd").read_text()
    )
    assert "sprite_frames" in keys
    assert "tileset" in keys


def test_importer_writes_only_under_generated_root() -> None:
    source = (PLUGIN_DIR / "importer.gd").read_text()
    match = re.search(r'const GENERATED_ROOT\s*:?=\s*"([^"]+)"', source)
    assert match is not None, "importer.gd must define a GENERATED_ROOT constant"
    assert match.group(1) == "res://generated"

    assignments = dict(
        re.findall(r"var\s+(\w+)\s*:?=\s*(.+)", source)
    )  # name -> RHS expression text, last write wins (fine: all are single-assignment)

    save_calls = re.findall(r"ResourceSaver\.save\(\s*[\w.]+,\s*(\w+)", source)
    assert save_calls, "expected at least one ResourceSaver.save(...) call in importer.gd"
    for path_var in save_calls:
        rhs = assignments.get(path_var, "")
        assert "out_dir" in rhs or "GENERATED_ROOT" in rhs, (
            f"'{path_var}' passed to ResourceSaver.save() was not built from "
            f"out_dir/GENERATED_ROOT: {rhs!r}"
        )


def test_wrapper_script_is_executable() -> None:
    assert WRAPPER_SCRIPT.is_file()
    mode = WRAPPER_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{WRAPPER_SCRIPT} is not executable"
    assert os.access(WRAPPER_SCRIPT, os.X_OK)


def test_wrapper_script_skips_cleanly_without_godot() -> None:
    bash = shutil.which("bash")
    assert bash is not None, "bash not found on this machine's PATH"
    env = {"PATH": "/nonexistent-bin-dir-for-test"}
    result = subprocess.run(
        [bash, str(WRAPPER_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "SKIPPED" in result.stdout


# --- Python re-implementation of manifest_validator.gd's structural checks ---------


def _validate_manifest_shape(manifest: dict[str, Any], manifest_dir: Path) -> list[str]:
    """Mirrors manifest_validator.gd's checks closely enough to prove the golden
    fixtures are structurally well-formed manifests, independent of Godot."""
    errors: list[str] = []
    required_keys = ["manifest_version", "asset_id", "asset_type", "textures"]
    for key in required_keys:
        if key not in manifest:
            errors.append(f"missing required key '{key}'")

    if manifest.get("manifest_version") != 1:
        errors.append(f"unsupported manifest_version {manifest.get('manifest_version')!r}")

    valid_asset_types = {"character", "enemy", "prop", "terrain"}
    asset_type = manifest.get("asset_type")
    if asset_type not in valid_asset_types:
        errors.append(f"asset_type {asset_type!r} is not one of {valid_asset_types}")

    asset_id = str(manifest.get("asset_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", asset_id):
        errors.append(f"asset_id {asset_id!r} must match [A-Za-z0-9_-]+")

    for tex_name, rel_path in manifest.get("textures", {}).items():
        abs_path = manifest_dir / rel_path
        if not abs_path.is_file():
            errors.append(f"textures.{tex_name} references '{rel_path}' which does not exist")

    return errors


@pytest.mark.parametrize("fixture_name", ["beacon.forge.json", "forest.forge.json"])
def test_golden_fixture_is_structurally_valid_per_python_reimplementation(
    fixture_name: str,
) -> None:
    fixture_path = GOLDEN_DIR / fixture_name
    manifest = json.loads(fixture_path.read_text())
    errors = _validate_manifest_shape(manifest, GOLDEN_DIR)

    # The fixtures directory ships only the .forge.json manifests, not the atlas.png
    # textures they reference (those belong to the exporter's own golden-image tests,
    # outside this plugin's file ownership) -- so the *only* expected error is the
    # texture-missing one. Anything else means the fixture itself is malformed.
    non_texture_errors = [e for e in errors if "textures." not in e]
    assert non_texture_errors == [], (
        f"{fixture_name}: unexpected structural errors: {non_texture_errors}"
    )
    assert len(errors) == 1, f"{fixture_name}: expected exactly one (texture-missing) error"
    assert "does not exist" in errors[0]


def test_golden_fixtures_parse_as_godot_manifest() -> None:
    """The fixtures must also validate against the real pydantic schema the exporter
    produces -- the Python-side half of the drift check that GDScript can't do."""
    for fixture_name in ["beacon.forge.json", "forest.forge.json"]:
        data = json.loads((GOLDEN_DIR / fixture_name).read_text())
        manifest_schema.GodotManifest.model_validate(data)
