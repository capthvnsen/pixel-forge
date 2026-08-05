"""Revision operations and the append-only revision log (Task 10)."""

from __future__ import annotations

from pixel_forge.revisions.operations import (
    OperationInfo,
    affected_targets,
    apply_operation,
    available_operations,
    check_protection,
)
from pixel_forge.revisions.store import (
    compare_revisions,
    head_revision,
    load_revisions,
    record_revision,
    revert_revision,
)

__all__ = [
    "OperationInfo",
    "affected_targets",
    "apply_operation",
    "available_operations",
    "check_protection",
    "compare_revisions",
    "head_revision",
    "load_revisions",
    "record_revision",
    "revert_revision",
]
