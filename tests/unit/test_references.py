"""Tests for references/profile.py (Task 11)."""

from __future__ import annotations

import pytest

from pixel_forge.domain.paths import safe_join
from pixel_forge.errors import ForgeError, PathSecurityError
from pixel_forge.references.profile import (
    _POLICY_PARAGRAPH,
    _REFERENCE_SUBDIRS,
    create_profile,
    list_references,
    load_profile,
    scaffold_references,
    update_profile,
)
from pixel_forge.schemas.style import ProvenanceEntry, StyleProfile


def test_scaffold_references_creates_dirs_and_readmes(tmp_path):
    dirs = scaffold_references(tmp_path)
    assert len(dirs) == 5
    for subdir in _REFERENCE_SUBDIRS:
        readme = tmp_path / "references" / subdir / "README.md"
        assert readme.is_file()
        assert _POLICY_PARAGRAPH in readme.read_text(encoding="utf-8")


def test_scaffold_references_is_idempotent(tmp_path):
    scaffold_references(tmp_path)
    scaffold_references(tmp_path)
    for subdir in _REFERENCE_SUBDIRS:
        assert (tmp_path / "references" / subdir / "README.md").is_file()


def test_scaffold_references_never_overwrites_approved_file(tmp_path):
    scaffold_references(tmp_path)
    approved_readme = tmp_path / "references" / "approved" / "README.md"
    approved_readme.write_text("hand-authored content", encoding="utf-8")
    scaffold_references(tmp_path)
    assert approved_readme.read_text(encoding="utf-8") == "hand-authored content"


def test_create_profile_refuses_to_clobber(tmp_path):
    scaffold_references(tmp_path)
    create_profile(tmp_path, StyleProfile(perspective="top_down"))
    with pytest.raises(ForgeError):
        create_profile(tmp_path, StyleProfile(perspective="side"))
    create_profile(tmp_path, StyleProfile(perspective="side"), overwrite=True)
    assert load_profile(tmp_path).perspective == "side"


def test_update_profile_merges_dedupes_provenance_and_round_trips(tmp_path):
    create_profile(tmp_path, StyleProfile(perspective="top_down"))

    p1 = update_profile(
        tmp_path,
        {"palette_tendencies": "warm, muted"},
        provenance=[ProvenanceEntry(source_path="references/approved/a.png", role="approved")],
    )
    assert p1.palette_tendencies == "warm, muted"
    assert len(p1.provenance) == 1

    p2 = update_profile(
        tmp_path,
        {"outline_style": "1px black"},
        provenance=[
            ProvenanceEntry(
                source_path="references/approved/a.png", role="approved"
            ),  # dup, dropped
            ProvenanceEntry(source_path="references/inspiration/b.png", role="inspiration"),
        ],
    )
    assert p2.outline_style == "1px black"
    assert p2.palette_tendencies == "warm, muted"  # earlier change preserved (shallow merge)
    assert [e.source_path for e in p2.provenance] == [
        "references/approved/a.png",
        "references/inspiration/b.png",
    ]

    reloaded = load_profile(tmp_path)
    assert reloaded == p2


def test_list_references_sorted_and_excludes_readme(tmp_path):
    scaffold_references(tmp_path)
    (tmp_path / "references" / "approved" / "b.png").write_bytes(b"")
    (tmp_path / "references" / "approved" / "a.png").write_bytes(b"")
    listing = list_references(tmp_path)
    assert listing["approved"] == ["a.png", "b.png"]
    assert "README.md" not in listing["approved"]
    assert set(listing) == set(_REFERENCE_SUBDIRS)


def test_reference_path_traversal_rejected_by_safe_join(tmp_path):
    scaffold_references(tmp_path)
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "references", "approved", "..", "..", "..", "escape.txt")
