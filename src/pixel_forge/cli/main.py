"""Typer app wiring: global options, sub-apps, and command registration.

Every command function is defined in `commands.py`; this module only builds the
Typer tree and wraps each command in `commands.guarded` so a `ForgeError` (or
any unexpected exception) becomes a clean stderr message and exit code 3
instead of a traceback.
"""

from __future__ import annotations

import typer

from pixel_forge import __version__
from pixel_forge.cli import commands

app = typer.Typer(
    name="pixel-forge",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    help="AI-native pixel-art asset production toolkit for Godot 4.",
)
export_app = typer.Typer(no_args_is_help=True, help="Export build artifacts for other engines.")
references_app = typer.Typer(no_args_is_help=True, help="Manage the reference-art library.")
style_app = typer.Typer(no_args_is_help=True, help="Inspect and edit the project style profile.")
schemas_app = typer.Typer(no_args_is_help=True, help="Generate machine-readable schema files.")
source_app = typer.Typer(
    no_args_is_help=True, help="Manage externally-produced frame files for an asset."
)

app.add_typer(export_app, name="export")
app.add_typer(references_app, name="references")
app.add_typer(style_app, name="style")
app.add_typer(schemas_app, name="schemas")
app.add_typer(source_app, name="source")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Emit the result as one JSON document on stdout."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress human-readable output."),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    ctx.obj = commands.State(json=json_output, quiet=quiet)


app.command("init")(commands.guarded(commands.init_project_cmd))
app.command("new")(commands.guarded(commands.new_asset_cmd))
app.command("list")(commands.guarded(commands.list_assets_cmd))
app.command("inspect")(commands.guarded(commands.inspect_asset_cmd))
app.command("validate")(commands.guarded(commands.validate_asset_cmd))
app.command("render")(commands.guarded(commands.render_asset_cmd))
app.command("preview")(commands.guarded(commands.preview_cmd))
app.command("revise")(commands.guarded(commands.revise_cmd))
app.command("update-spec")(commands.guarded(commands.update_spec_cmd))
app.command("operations")(commands.guarded(commands.operations_cmd))
app.command("revisions")(commands.guarded(commands.revisions_cmd))
app.command("diff")(commands.guarded(commands.diff_cmd))
app.command("test-seams")(commands.guarded(commands.test_seams_cmd))
app.command("build")(commands.guarded(commands.build_cmd))
app.command("build-all")(commands.guarded(commands.build_all_cmd))
app.command("import-region")(commands.guarded(commands.import_region_cmd))
app.command("extract-palette")(commands.guarded(commands.extract_palette_cmd))
app.command("import-sheet")(commands.guarded(commands.import_sheet_cmd))
app.command("view")(commands.guarded(commands.view_cmd))
app.command("contact")(commands.guarded(commands.contact_cmd))

export_app.command("godot")(commands.guarded(commands.export_godot_cmd))
references_app.command("init")(commands.guarded(commands.references_init_cmd))
style_app.command("show")(commands.guarded(commands.style_show_cmd))
style_app.command("set")(commands.guarded(commands.style_set_cmd))
source_app.command("pin")(commands.guarded(commands.source_pin_cmd))
schemas_app.command("export")(commands.guarded(commands.schemas_export_cmd))
