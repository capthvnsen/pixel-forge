"""End-to-end proof that `examples/` is a real, buildable pixel-forge project.

Copies `examples/` into `tmp_path` (never writes to the repo's own tree) and
drives it through `pixel_forge.api.build_all` and the real CLI, the same way
`pixel-forge build-all` would be run against it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pixel_forge import api
from pixel_forge.cli.main import app
from pixel_forge.schemas import GodotManifest

EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"

_SPRITE_ASSETS = {
    "engineer": "character",
    "crawler": "enemy",
    "beacon": "prop",
    "sporeling": "enemy",
    "rune_chest": "prop",
    "vanguard": "character",
}
_TERRAIN_ASSET = "forest_tileset"
_ALL_ASSETS = {*_SPRITE_ASSETS, _TERRAIN_ASSET}

runner = CliRunner()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "examples"
    shutil.copytree(EXAMPLES_ROOT, root, ignore=shutil.ignore_patterns("build"))
    return root


def _png_hashes(root: Path) -> dict[str, str]:
    build_dir = root / "build"
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(build_dir.rglob("*.png"))
    }


def _assert_not_blocking(report: api.BuildReport, root: Path) -> None:
    if report.blocking:
        details = []
        for asset_id in report.failed:
            for finding in api.validate_asset(root, asset_id).findings:
                if finding.severity == "error":
                    details.append(f"  {asset_id} {finding.rule_id}: {finding.message}")
        raise AssertionError("blocking findings in build-all report:\n" + "\n".join(details))
    assert report.blocking is False
    assert report.failed == []


def _assert_artifacts_on_disk(root: Path, asset_id: str) -> None:
    build_dir = root / "build" / asset_id
    assert (build_dir / "manifest.json").is_file()
    assert (root / "build" / "godot" / f"{asset_id}.forge.json").is_file()
    if asset_id == _TERRAIN_ASSET:
        assert (build_dir / f"{asset_id}_atlas.png").is_file()
    else:
        assert (build_dir / f"{asset_id}_sheet.png").is_file()
        assert (build_dir / f"{asset_id}_contact.png").is_file()
        previews = list(build_dir.glob("preview_*"))
        assert previews, f"no preview file written for {asset_id}"


def _assert_godot_manifest(root: Path, asset_id: str, asset_type: str) -> None:
    manifest_path = root / "build" / "godot" / f"{asset_id}.forge.json"
    manifest = GodotManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert manifest.asset_type == asset_type
    if asset_id == _TERRAIN_ASSET:
        assert manifest.tileset is not None
        assert manifest.tileset.tiles
    else:
        assert manifest.sprite_frames


def test_build_all_produces_every_artifact_for_every_asset(project_root: Path) -> None:
    report = api.build_all(project_root)
    _assert_not_blocking(report, project_root)

    asset_ids = {m.asset_id for m in report.assets}
    assert asset_ids == _ALL_ASSETS

    for asset_id, asset_type in {**_SPRITE_ASSETS, _TERRAIN_ASSET: "terrain"}.items():
        _assert_artifacts_on_disk(project_root, asset_id)
        _assert_godot_manifest(project_root, asset_id, asset_type)


def test_build_all_is_idempotent(project_root: Path) -> None:
    api.build_all(project_root)
    before = _png_hashes(project_root)
    assert before

    second = api.build_all(project_root)
    _assert_not_blocking(second, project_root)
    assert _png_hashes(project_root) == before


def test_build_all_with_force_reproduces_byte_identical_pngs(project_root: Path) -> None:
    api.build_all(project_root)
    before = _png_hashes(project_root)

    forced = api.build_all(project_root, force=True)
    _assert_not_blocking(forced, project_root)
    assert _png_hashes(project_root) == before


def test_cli_build_all_exits_zero(project_root: Path) -> None:
    result = runner.invoke(app, ["build-all", "--root", str(project_root)])
    assert result.exit_code == 0, result.output


def test_cli_build_all_json_reports_non_blocking(project_root: Path) -> None:
    result = runner.invoke(app, ["--json", "build-all", "--root", str(project_root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["blocking"] is False
