"""Append-only revision log: read/write `assets/<id>/revisions.jsonl` and diff it."""

from __future__ import annotations

from pixel_forge.domain.hashing import content_hash, short
from pixel_forge.domain.loader import append_jsonl, load_jsonl
from pixel_forge.domain.paths import ProjectPaths
from pixel_forge.errors import ForgeError, OperationError
from pixel_forge.revisions.operations import affected_targets, apply_operation
from pixel_forge.schemas.asset import AssetDocUnion
from pixel_forge.schemas.revision import OperationSpec, RevisionDiff, RevisionRecord
from pixel_forge.schemas.validation import ValidationReport


def load_revisions(paths: ProjectPaths, asset_id: str) -> list[RevisionRecord]:
    """All revisions for `asset_id`, in the order they were recorded."""
    records = load_jsonl(paths.asset_revisions(asset_id))
    return [RevisionRecord.model_validate(r) for r in records]


def head_revision(paths: ProjectPaths, asset_id: str) -> RevisionRecord | None:
    revisions = load_revisions(paths, asset_id)
    return revisions[-1] if revisions else None


def record_revision(
    paths: ProjectPaths,
    asset_id: str,
    *,
    operation: OperationSpec,
    inverse: OperationSpec | None,
    doc_before: AssetDocUnion,
    doc_after: AssetDocUnion,
    timestamp: str,
    validation: ValidationReport | None = None,
) -> RevisionRecord:
    """Append a new revision recording `operation` having transformed `doc_before` into `doc_after`.

    `revision_id` is deterministic: it depends only on the parent revision id,
    the operation itself, and the resulting hash, never on the clock or a
    random source. `timestamp` is supplied by the caller and stored verbatim.
    """
    parent = head_revision(paths, asset_id)
    parent_id = parent.revision_id if parent is not None else None
    hash_before = content_hash(doc_before)
    hash_after = content_hash(doc_after)
    revision_id = short(
        content_hash(
            {
                "parent": parent_id,
                "operation": operation.model_dump(mode="json"),
                "hash_after": hash_after,
            }
        )
    )
    affected = affected_targets(doc_before, operation)
    record = RevisionRecord(
        revision_id=revision_id,
        parent_revision=parent_id,
        timestamp=timestamp,
        operation=operation,
        inverse=inverse,
        asset_id=asset_id,
        affected_regions=affected.get("regions", []),
        affected_frames=[int(frame) for frame in affected.get("frames", [])],
        affected_directions=affected.get("directions", []),
        hash_before=hash_before,
        hash_after=hash_after,
        validation=validation,
    )
    append_jsonl(paths.asset_revisions(asset_id), record.model_dump(mode="json"))
    return record


def compare_revisions(paths: ProjectPaths, asset_id: str, rev_a: str, rev_b: str) -> RevisionDiff:
    """Diff between two revisions: operations applied between them, and what they touched."""
    revisions = load_revisions(paths, asset_id)
    by_id = {rev.revision_id: rev for rev in revisions}
    known_ids = sorted(by_id)
    if rev_a not in by_id:
        raise ForgeError(f"unknown revision {rev_a!r} for asset {asset_id!r}; known: {known_ids}")
    if rev_b not in by_id:
        raise ForgeError(f"unknown revision {rev_b!r} for asset {asset_id!r}; known: {known_ids}")

    order = {rev.revision_id: i for i, rev in enumerate(revisions)}
    lo, hi = sorted((order[rev_a], order[rev_b]))
    between = revisions[lo + 1 : hi + 1]

    regions: list[str] = []
    frames: list[int] = []
    directions: list[str] = []
    for rev in between:
        for name in rev.affected_regions:
            if name not in regions:
                regions.append(name)
        for idx in rev.affected_frames:
            if idx not in frames:
                frames.append(idx)
        for direction in rev.affected_directions:
            if direction not in directions:
                directions.append(direction)

    return RevisionDiff(
        asset_id=asset_id,
        revision_a=rev_a,
        revision_b=rev_b,
        operations=[rev.operation for rev in between],
        affected_regions=sorted(regions),
        affected_frames=sorted(frames),
        affected_directions=sorted(directions),
        hash_a=by_id[rev_a].hash_after,
        hash_b=by_id[rev_b].hash_after,
    )


def revert_revision(
    paths: ProjectPaths, asset_id: str, revision_id: str, doc: AssetDocUnion
) -> tuple[AssetDocUnion, OperationSpec]:
    """Apply the stored inverse of `revision_id` to `doc`.

    Returns `(new_doc, inverse_of_inverse)`.
    """
    revisions = load_revisions(paths, asset_id)
    by_id = {rev.revision_id: rev for rev in revisions}
    if revision_id not in by_id:
        raise ForgeError(
            f"unknown revision {revision_id!r} for asset {asset_id!r}; known: {sorted(by_id)}"
        )
    record = by_id[revision_id]
    if record.inverse is None:
        raise OperationError(
            f"revision {revision_id!r} has no recorded inverse and cannot be reverted"
        )
    return apply_operation(doc, record.inverse)
