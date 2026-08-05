# References and the style profile

`src/pixel_forge/references/profile.py` supports a specific workflow: a human drops
reference images into a project's `references/` directory, a **vision-capable agent**
looks at them and forms a judgement, and that judgement is recorded as structured
fields on a `StyleProfile` plus a provenance trail naming which file(s) informed which
field. The toolkit itself performs no image analysis — nothing in this codebase reads
pixels out of a reference PNG. `references/profile.py` only stores and merges the
structured opinions an agent (or a human) supplies about what it saw.

## Reference directory convention

`scaffold_references(root)` (`pixel-forge references init`, MCP `scaffold_references`)
creates five subdirectories under `references/`, each with a `README.md` carrying the
no-tracing policy verbatim:

| Directory | Purpose |
|---|---|
| `references/approved/` | Art the project has explicitly signed off on as representative of the target style. |
| `references/inspiration/` | Looser mood/style references, not necessarily approved for direct imitation. |
| `references/palettes/` | Colour-scheme references. |
| `references/animation/` | Timing/motion references (video stills, GIFs, animation sheets). |
| `references/rejected/` | Explicitly-not-this-style examples, kept for contrast. |

Idempotent: re-running never overwrites a file already present, in particular
anything already dropped under `references/approved/`. Safe to call on every project
load.

## The no-tracing policy

Every subdirectory's `README.md` states, verbatim:

> Do not trace, copy, or reproduce protected artwork, exact compositions, or
> recognisable characters from any reference in this directory. References inform
> style parameters only: palette tendencies, outline treatment, light direction,
> timing, and shape language. Producing a derivative that reproduces a specific
> protected asset is out of scope for this toolkit and is not a supported workflow.

This is policy, not a technical enforcement mechanism — nothing in the render
pipeline reads reference images at all (the local render backend only reads the asset
spec and palette), so there's no code path that could copy a reference's pixels even
if it wanted to. The constraint is on the *agent's judgement*, not on the toolkit.

## The style profile schema

`references/style_profile.yaml`, one per project, `StyleProfile`
(full field list in `docs/schema.md`): eleven free-text descriptive fields
(`perspective`, `pixel_density`, `palette_tendencies`, `outline_style`,
`light_direction`, `material_treatment`, `silhouette_complexity`, `texture_density`,
`animation_timing`, `shape_language`, `environmental_hierarchy`, each defaulting to
`""`) plus `provenance: list[ProvenanceEntry]`.

`ProvenanceEntry` is `{source_path: str, role: Literal["approved", "inspiration",
"palette", "animation", "rejected"], notes: str = ""}` — `role` matches one of the
five `references/` subdirectory names.

None of these fields are read by the renderer, the validators, or the exporter. The
style profile is a project-level memory an agent is expected to read *before*
authoring or editing a spec, and to keep updated as the project's visual language
settles — it doesn't feed back into any automated check.

## Workflow for a vision-capable agent

1. `scaffold_references(root)` if `references/` doesn't exist yet.
2. A human (or another process) drops image files into the appropriate
   subdirectories.
3. The agent looks at the files under `references/{approved,inspiration,palettes,
   animation}/` (`references.list_references(root)` gives a sorted, README-excluded
   file listing per subdirectory) and forms an opinion: dominant palette tendencies,
   outline treatment, light direction, silhouette complexity, animation timing feel,
   and so on.
4. The agent calls `update_profile` (`api.set_style_profile` / CLI `style set` / MCP
   `update_style_profile`) with the observed fields and a `ProvenanceEntry` per
   reference file that informed a judgement:

   ```bash
   pixel-forge style set \
     --field outline_style='"1px dark outline, no anti-aliasing"' \
     --field light_direction='"top-left"' \
     --provenance 'references/approved/hero_ref.png:approved:outline treatment reference' \
     --root .
   ```

5. `update_profile` shallow-merges the given fields (fields omitted from the call
   keep their current value) and appends `provenance` entries, de-duplicated by
   `(source_path, role)` — calling it again with the same source/role pair is a
   no-op for that entry, not a duplicate.
6. Before authoring or revising an asset spec, the agent reads the current profile
   (`get_style_profile`) to stay consistent with previously recorded judgements,
   rather than re-deriving style decisions from scratch each time.

`create_profile`/`load_profile` refuse to silently overwrite an existing profile
(`create_profile` raises unless `overwrite=True`) or silently invent one on load
(raises `ForgeError` if none exists) — `get_style_profile`/`set_style_profile` in
`api.py` are the layer that makes first-use painless by creating an empty profile on
demand, so a caller never has to special-case "no profile yet" itself.
