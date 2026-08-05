"""Integration tests for the Typer CLI. Exercises the CLI exclusively through
`typer.testing.CliRunner` against the real `pixel_forge.cli.main:app` — no direct
calls into `pixel_forge.api` except to build broken fixture assets on disk, the
same way `tests/integration/test_api.py` does.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner, Result

from pixel_forge import __version__, templates
from pixel_forge.cli.main import app
from pixel_forge.domain import Project
from pixel_forge.schemas import parse_asset_doc

runner = CliRunner()


def _invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    result = _invoke("init", str(root), "--name", name)
    assert result.exit_code == 0, result.output
    return root


def _make_broken_asset(root: Path, asset_id: str) -> None:
    """A structurally valid spec whose shape references a nonexistent palette colour
    — parses fine, but rendering/validation raises/flags it. Mirrors test_api.py."""
    data = templates.asset_template("character", asset_id)
    data["regions"]["block"]["shapes"][0]["color"] = "not_a_real_color"
    Project.load(root).save_asset(parse_asset_doc(data))


def _make_body_region_asset(root: Path, asset_id: str) -> None:
    """Starter character template with its region renamed 'block' -> 'body'."""
    data = templates.asset_template("character", asset_id)
    data["regions"] = {"body": data["regions"].pop("block")}
    Project.load(root).save_asset(parse_asset_doc(data))


def _make_off_canvas_asset(root: Path, asset_id: str) -> None:
    """A structurally valid, renderable asset whose region has been pushed off
    canvas via a real revision, so `validate`/`build` see a blocking PIX008
    finding rather than a render-time `ForgeError`."""
    assert _invoke("new", "character", asset_id, "--root", str(root)).exit_code == 0
    result = _invoke(
        "revise",
        asset_id,
        "--operation",
        "translate_region",
        "--param",
        "region=block",
        "--param",
        "offset=[-1000,-1000]",
        "--root",
        str(root),
    )
    assert result.exit_code == 0, result.output


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# --- full lifecycle ------------------------------------------------------------------------


def test_full_lifecycle_exits_zero_and_writes_expected_files(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    assert _invoke("init", str(root), "--name", "demo").exit_code == 0
    assert (root / "pixel-forge.yaml").is_file()

    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    assert (root / "assets" / "hero" / "hero.yaml").is_file()

    render = _invoke("render", "hero", "--root", str(root))
    assert render.exit_code == 0, render.output
    assert (root / "build" / "hero" / "manifest.json").is_file()

    assert _invoke("validate", "hero", "--root", str(root)).exit_code == 0

    preview = _invoke("preview", "hero", "--root", str(root))
    assert preview.exit_code == 0, preview.output
    assert any((root / "build" / "hero").glob("preview_*"))

    export = _invoke("export", "godot", "hero", "--root", str(root))
    assert export.exit_code == 0, export.output
    assert (root / "build" / "godot" / "hero.forge.json").is_file()

    build = _invoke("build", "hero", "--root", str(root))
    assert build.exit_code == 0, build.output


# --- --json output ---------------------------------------------------------------------------


def test_json_output_is_a_single_document_for_list_inspect_validate_render(
    tmp_path: Path,
) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    assert _invoke("render", "hero", "--root", str(root)).exit_code == 0

    list_result = _invoke("--json", "list", "--root", str(root))
    assert list_result.exit_code == 0, list_result.output
    list_payload = json.loads(list_result.stdout)
    assert isinstance(list_payload, list)
    assert list_payload[0]["asset_id"] == "hero"

    inspect_result = _invoke("--json", "inspect", "hero", "--root", str(root))
    assert inspect_result.exit_code == 0
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["asset_id"] == "hero"
    assert "regions" in inspect_payload

    validate_result = _invoke("--json", "validate", "hero", "--root", str(root))
    assert validate_result.exit_code == 0
    validate_payload = json.loads(validate_result.stdout)
    assert "blocking" in validate_payload
    assert "findings" in validate_payload

    render_result = _invoke("--json", "render", "hero", "--root", str(root), "--force")
    assert render_result.exit_code == 0
    render_payload = json.loads(render_result.stdout)
    assert render_payload["asset_id"] == "hero"
    assert "frames_written" in render_payload


# --- exit code 1: blocking validation ----------------------------------------------------------


def test_validate_exits_one_on_blocking_errors(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _make_off_canvas_asset(root, "cursed")
    result = _invoke("validate", "cursed", "--root", str(root))
    assert result.exit_code == 1


def test_build_exits_one_on_blocking_errors(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _make_off_canvas_asset(root, "cursed")
    result = _invoke("build", "cursed", "--root", str(root))
    assert result.exit_code == 1


# --- exit code 2: usage errors ------------------------------------------------------------------


def test_unknown_asset_type_exits_two(tmp_path: Path) -> None:
    root = _init(tmp_path)
    result = _invoke("new", "spaceship", "foo", "--root", str(root))
    assert result.exit_code == 2


def test_unknown_operation_exits_two(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    result = _invoke("revise", "hero", "--operation", "not_a_real_op", "--root", str(root))
    assert result.exit_code == 2


# --- exit code 3: internal errors ---------------------------------------------------------------


def test_missing_asset_id_exits_three_with_stderr_message_and_no_traceback(
    tmp_path: Path,
) -> None:
    root = _init(tmp_path)
    result = _invoke("validate", "nope", "--root", str(root))
    assert result.exit_code == 3
    assert "nope" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


# --- --dry-run leaves the filesystem untouched ---------------------------------------------------


def test_new_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    before = _snapshot(root)
    result = _invoke("new", "character", "ghost", "--root", str(root), "--dry-run")
    assert result.exit_code == 0, result.output
    assert _snapshot(root) == before


def test_render_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    before = _snapshot(root)
    result = _invoke("render", "hero", "--root", str(root), "--dry-run")
    assert result.exit_code == 0, result.output
    assert _snapshot(root) == before


def test_preview_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    before = _snapshot(root)
    result = _invoke("preview", "hero", "--root", str(root), "--dry-run")
    assert result.exit_code == 0, result.output
    assert _snapshot(root) == before


def test_revise_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    before = _snapshot(root)
    result = _invoke(
        "revise",
        "hero",
        "--operation",
        "translate_region",
        "--param",
        "region=block",
        "--param",
        "offset=[1,0]",
        "--root",
        str(root),
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert _snapshot(root) == before


# --- revise mutates the spec and appends a revision -----------------------------------------------


def test_revise_mutates_spec_and_appends_revision(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _make_body_region_asset(root, "hero")

    revise = _invoke(
        "revise",
        "hero",
        "--operation",
        "translate_region",
        "--param",
        "region=body",
        "--param",
        "offset=[1,0]",
        "--root",
        str(root),
    )
    assert revise.exit_code == 0, revise.output

    revisions = _invoke("--json", "revisions", "hero", "--root", str(root))
    assert revisions.exit_code == 0
    payload = json.loads(revisions.stdout)
    assert len(payload) == 1
    assert payload[0]["operation"]["name"] == "translate_region"


# --- update-spec replaces the whole document and appends a revision ------------------------------


def _write_updated_spec(root: Path, tmp_path: Path, asset_id: str, **overrides: object) -> Path:
    doc = Project.load(root).load_asset(asset_id)
    spec = doc.model_dump(mode="json", exclude_defaults=True)
    spec.pop("kind", None)
    spec.update(overrides)
    spec_file = tmp_path / f"{asset_id}_updated.yaml"
    spec_file.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return spec_file


def test_update_spec_replaces_document_and_appends_revision(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    spec_file = _write_updated_spec(root, tmp_path, "hero", directions=["south", "north"])

    result = _invoke("update-spec", "hero", "--file", str(spec_file), "--root", str(root))
    assert result.exit_code == 0, result.output

    updated = Project.load(root).load_asset("hero")
    assert updated.directions == ["south", "north"]  # type: ignore[union-attr]

    revisions = _invoke("--json", "revisions", "hero", "--root", str(root))
    payload = json.loads(revisions.stdout)
    assert len(payload) == 1
    assert payload[0]["operation"]["name"] == "replace_spec"


def test_update_spec_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    spec_file = _write_updated_spec(root, tmp_path, "hero", directions=["south", "north"])
    before = _snapshot(root)

    result = _invoke(
        "update-spec", "hero", "--file", str(spec_file), "--root", str(root), "--dry-run"
    )
    assert result.exit_code == 0, result.output
    assert _snapshot(root) == before


# --- asset id normalisation ---------------------------------------------------------------------


def test_assets_prefix_and_bare_id_resolve_to_the_same_asset(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "engineer", "--root", str(root)).exit_code == 0

    bare = _invoke("--json", "inspect", "engineer", "--root", str(root))
    prefixed = _invoke("--json", "inspect", "assets/engineer", "--root", str(root))
    assert bare.exit_code == 0
    assert prefixed.exit_code == 0
    assert json.loads(bare.stdout) == json.loads(prefixed.stdout)


# --- build-all ------------------------------------------------------------------------------------


def test_build_all_exits_zero_then_one_once_an_asset_is_broken(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert _invoke("new", "character", "hero", "--root", str(root)).exit_code == 0
    assert _invoke("new", "terrain", "ground", "--root", str(root)).exit_code == 0

    ok = _invoke("build-all", "--root", str(root))
    assert ok.exit_code == 0, ok.output

    _make_broken_asset(root, "cursed")
    broken = _invoke("build-all", "--root", str(root))
    assert broken.exit_code == 1


# --- --version ------------------------------------------------------------------------------------


def test_version_prints_version_and_exits_zero() -> None:
    result = _invoke("--version")
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
