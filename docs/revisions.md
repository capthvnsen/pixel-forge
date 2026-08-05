# Revisions

Every edit to an asset's spec after its initial creation should go through the
revision system (`src/pixel_forge/revisions/`), not a hand-edited YAML file: it gives
every change an id, a recorded inverse, a before/after hash, and (optionally) the
validation result computed against the new spec — an audit trail a coding agent (or a
human) can diff, roll back, and reason about later.

The revision system applies to `character`, `enemy`, and `prop` (region/animation)
assets only. Every operation handler calls `_require_sprite_doc`, which raises
`OperationError` for a `terrain` asset — there is currently no revision operation
catalogue for tile edits (see the README's "known limitations").

## Operation catalogue

From `revisions/operations.py::available_operations()` (also `pixel-forge operations`
/ MCP `list_operations`):

| Operation | Params | What it does |
|---|---|---|
| `resize_region` | `region`, `delta`, `shape_indices` (optional, defaults to all shapes) | Grows/shrinks every `rect`/`ellipse` shape in a region by `delta = [dw, dh]`, centred about its middle: `new_size = size + delta`, `new_at = at - delta // 2` (floor division). `pixel`/`line` shapes in the region are left untouched. Raises if any target shape would shrink below 1x1. |
| `translate_region` | `region`, `offset` | Shifts every shape in a region by a fixed `[dx, dy]` pixel offset (adds to `at`, or to both `start`/`end` for a `line`). |
| `recolor_region` | `region`, `mapping` (`{source_color_id: target_color_id}`) | Remaps the palette colour ids a region's shapes reference. `mapping` must be injective (no two sources map to the same target) so it stays reversible, and every target id must already exist in the palette. |
| `set_frame_duration` | `animation`, `frame` (optional; omit for every frame), `duration_ms` | Sets one frame's (or every frame's) duration in an animation. |
| `add_frame` | `animation`, `at`, `frame` (a serialised `FrameSpec`) | Inserts a frame at index `at` (`0..len(frames)` inclusive). |
| `remove_frame` | `animation`, `at` | Removes the frame at index `at`. Refuses to remove an animation's last remaining frame. |
| `set_region_visibility` | `region`, `visible`, plus either (`animation` + `frames: [int, ...]`) or (`directions: [str, ...]`) | Sets a region's `visible` flag for specific frames of one animation, or for specific directions (via `direction_overrides`). Exactly one of the two targeting modes must be used. |

Every handler operates on a `region` — a **protected region raises `OperationError`**
before any change is attempted (`Region.protected`).

## `op.protect`

Any `OperationSpec` can carry a `protect: list[str]` of anchor or region names that
must be provably unchanged after the operation, checked by
`revisions/operations.py::check_protection`:

- A protected **anchor** must resolve to the exact same `Vec2` before and after.
- A protected **region** must have byte-identical shapes (`model_dump(mode="json")`
  equality) before and after.
- An unrecognised name (not an anchor or region on the *before* doc) itself raises
  `OperationError`.

`protect` is advisory per-call, not a substitute for `Region.protected` — it lets a
caller assert "this operation must not have touched X" and fail loudly if it did,
independent of whether X happens to be a protected region.

## The revision record

Every applied operation (`api.apply_asset_operation`, MCP `apply_asset_operation`,
CLI `revise`) appends a `RevisionRecord` (full field reference in `docs/schema.md`):
a deterministic `revision_id` (a 12-char hash of `parent_revision`, the operation, and
the resulting doc hash — never the clock or a random source), the operation applied,
its recorded `inverse`, before/after content hashes, the best-effort
`affected_regions`/`affected_frames`/`affected_directions`, and the `ValidationReport`
computed against the new doc. Records append to
`assets/<asset_id>/revisions.jsonl`, one JSON object per line, oldest first —
never rewritten in place.

## Inverses and rollback

Every handler returns `(inverse_operation_name, inverse_operation_params)` alongside
the mutated doc. Most operations invert with their own forward formula
(`translate_region`'s inverse is `translate_region` with the negated offset;
`add_frame`'s inverse is `remove_frame` at the same index; `recolor_region`'s inverse
is the reversed mapping). Two operations — `resize_region`
(floor-division centring is lossy for odd deltas) and `set_frame_duration`/
`set_region_visibility` (the prior value isn't derivable from the forward params alone)
— instead stash an exact snapshot of the prior values under an internal `restore` key
in the inverse's params. That key is not part of the public parameter contract (an
agent authoring a *new* operation should only ever pass `region`/`delta`/etc.); it
exists purely so `apply(op)` followed by `apply(inverse)` round-trips exactly, which
this module has to guarantee.

`revisions.store.revert_revision(paths, asset_id, revision_id, doc)` applies a
recorded revision's stored `inverse` to `doc` and returns `(new_doc, inverse_of_inverse)`
— there is no dedicated CLI/MCP `revert`/`rollback` command; rolling back means
re-running `revise`/`apply_asset_operation` with the recorded `inverse`'s own name and
params (as demonstrated below), which appends a *new* revision rather than deleting
the one being undone — the log is append-only.

## Worked example: widen the Engineer's backpack by 2px

`examples/assets/engineer/engineer.yaml`'s `backpack` region has two shapes: a metal
`rect` at `[-4, -2]` size `[12, 20]`, and an inset `accent` stripe at `[-2, 4]` size
`[8, 3]`. Widening it by 2px across every direction and animation, without touching
`feet`, the `weapon` region, or the palette, is a single `resize_region` call —
`backpack`'s region shapes are shared across all directions/animations by
construction (per-direction art is expressed as `direction_overrides`, not
independent geometry — see `README.md`'s limitations), so one operation covers every
pose at once. `--protect feet --protect weapon` makes the "don't touch" requirement
an enforced precondition, not just an intention.

```bash
pixel-forge revise engineer \
  --operation resize_region \
  --param region=backpack \
  --param 'delta=[2,0]' \
  --protect feet \
  --protect weapon \
  --timestamp 2026-08-05T12:00:00Z \
  --root examples
```

Real output from this exact command (run against a scratch copy of `examples/`):

```
f72fc1c55151: resize_region on engineer at 2026-08-05T12:00:00Z
  hash: 48115e20f0cdba34c624cbf22cfc79ae2e1fd5ff282775b28100b081e7a6d8d5 -> 9d610b2bf7826ed355ac822f9720c38e1e9914a106871ebd85d8794ba5f1a6d4
  regions: backpack
```

The resulting spec (`assets/engineer/engineer.yaml`, `backpack` region):

```yaml
backpack:
  anchor: upper_back
  layer: 10
  shapes:
  - color: metal
    op: rect
    at: [-5, -2]
    size: [14, 20]
  - color: accent
    op: rect
    at: [-3, 4]
    size: [10, 3]
```

Both shapes grew by 2px in width, centred (`new_at = old_at - delta // 2`): the
backpack rect `12x20 -> 14x20`, and the inset accent stripe `8x3 -> 10x3` right along
with it (it's part of the same region, so it widens too — expected, since
`resize_region` operates on every shape in the region). `feet` and `weapon` are
untouched (protection held; the command would have failed with `OperationError`
otherwise), and no palette colour changed.

The real, full `--json` revision record for this call:

```json
{
  "revision_id": "f72fc1c55151",
  "parent_revision": null,
  "timestamp": "2026-08-05T12:00:00Z",
  "operation": {
    "name": "resize_region",
    "params": { "region": "backpack", "delta": [2, 0] },
    "targets": {},
    "protect": ["feet", "weapon"]
  },
  "inverse": {
    "name": "resize_region",
    "params": {
      "region": "backpack",
      "shape_indices": [0, 1],
      "restore": {
        "0": [-4, -2, 12, 20],
        "1": [-2, 4, 8, 3]
      }
    },
    "targets": {},
    "protect": ["feet", "weapon"]
  },
  "asset_id": "engineer",
  "affected_regions": ["backpack"],
  "affected_frames": [],
  "affected_directions": [],
  "hash_before": "48115e20f0cdba34c624cbf22cfc79ae2e1fd5ff282775b28100b081e7a6d8d5",
  "hash_after": "9d610b2bf7826ed355ac822f9720c38e1e9914a106871ebd85d8794ba5f1a6d4",
  "validation": {
    "asset_id": "engineer",
    "findings": [
      {
        "rule_id": "PIX010",
        "severity": "info",
        "kind": "heuristic",
        "asset_id": "engineer",
        "direction": null,
        "animation": null,
        "frame": null,
        "region": null,
        "message": "doc carries no lighting metadata (no palette colour role/ramp declared)",
        "remediation": "tag shadow/light palette colours with role/ramp to enable this check",
        "measurements": { "palette_colors_with_role": 0 }
      }
    ]
  }
}
```

Validation is unaffected (the same pre-existing `PIX010` info finding as before —
`report.blocking` is `false`).

### Rolling it back

Applying the recorded `inverse` verbatim (its own `restore` snapshot, not a
recomputed `delta`) reverses it exactly:

```bash
pixel-forge revise engineer \
  --operation resize_region \
  --param region=backpack \
  --param 'restore={"0":[-4,-2,12,20],"1":[-2,4,8,3]}' \
  --param 'shape_indices=[0,1]' \
  --protect feet --protect weapon \
  --timestamp 2026-08-05T12:05:00Z \
  --root examples
```

```
e30759578a94: resize_region on engineer at 2026-08-05T12:05:00Z
  hash: 9d610b2bf7826ed355ac822f9720c38e1e9914a106871ebd85d8794ba5f1a6d4 -> 48115e20f0cdba34c624cbf22cfc79ae2e1fd5ff282775b28100b081e7a6d8d5
  regions: backpack
```

`hash_after` of the rollback (`48115e20f0cd...`) equals `hash_before` of the original
operation exactly — confirming `apply(op)` followed by `apply(inverse)` round-trips
byte-for-byte, per this module's core guarantee. Note this is a *third* revision on
the log (`e30759578a94`, parented on `f72fc1c55151`), not a deletion of the second —
the revision log never rewrites history.
