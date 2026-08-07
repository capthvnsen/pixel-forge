"""Unit tests for `api.import_layered` anchor derivation: exact integer
positions for `feet`, the shoulder/hip joint anchors, `head_top`, and `root`,
hand-computed from a minimal 10x12 layer geometry, plus the inner-edge rule
for shoulders and an explicit-canvas (unshifted) case.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge import api
from pixel_forge.schemas import BitmapShape
from pixel_forge.schemas.asset import CharacterAsset

TS = "2026-08-06T00:00:00Z"
INK = (40, 40, 40, 255)

# Half-open rects per layer on a shared 10x12 space. Deliberately asymmetric
# and offset from the origin so the derived canvas exercises the shift.
_RECTS = {
    "torso": (4, 4, 7, 8),
    "head": (4, 1, 7, 4),
    "arm_left": (1, 4, 3, 8),
    "arm_right": (8, 4, 10, 8),
    "leg_left": (4, 8, 6, 12),
    "leg_right": (6, 8, 8, 12),
}


def _write_layers(root: Path, rects: dict[str, tuple[int, int, int, int]]) -> dict[str, Path]:
    paths = {}
    for name, (x0, y0, x1, y1) in rects.items():
        img = Image.new("RGBA", (10, 12), (0, 0, 0, 0))
        for y in range(y0, y1):
            for x in range(x0, x1):
                img.putpixel((x, y), INK)
        img.save(root / f"{name}.png")
        paths[name] = Path(f"{name}.png")
    return paths


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    api.init_project(project, "demo")
    return project


def test_anchor_positions_are_derived_from_bboxes(root: Path) -> None:
    layers = _write_layers(root, _RECTS)
    result = api.import_layered(root, "hero", layers, timestamp=TS)

    # Union bbox (1, 1)-(10, 12) -> canvas (9, 11), shift (-1, -1).
    assert result.canvas == (9, 11)
    assert result.anchors == {
        "root": (4, 5),  # torso centre
        "feet": (5, 10),  # bottom-centre of the union of both leg bboxes
        "head_top": (4, 0),  # top-centre of the head bbox
        "shoulder_left": (1, 3),  # top-INNER corner of the left arm bbox
        "shoulder_right": (7, 3),  # top-INNER corner of the right arm bbox
        "hip_left": (4, 7),  # top-centre of the left leg bbox
        "hip_right": (6, 7),  # top-centre of the right leg bbox
    }

    doc = api.get_asset(root, "hero")
    assert isinstance(doc, CharacterAsset)
    assert doc.asset.baseline_y == 10  # feet y


def test_shoulder_inner_edge_faces_the_canvas_centre(root: Path) -> None:
    """The inner edge is the one nearest the canvas centre line, whichever
    side of it the arm bbox falls on."""
    layers = _write_layers(root, _RECTS)
    result = api.import_layered(root, "hero", layers, timestamp=TS)
    left = result.anchors["shoulder_left"]
    right = result.anchors["shoulder_right"]
    centre_x = result.canvas[0] // 2  # 4
    # Left arm sits left of centre -> inner edge is its RIGHT edge (x1 - 1).
    assert left[0] > 1 - 1 and left[0] < centre_x
    # Right arm sits right of centre -> inner edge is its LEFT edge (x0).
    assert right[0] > centre_x
    assert left[1] == right[1] == 3  # both at the arm bbox top


def test_explicit_canvas_keeps_layer_coordinates_unshifted(root: Path) -> None:
    layers = _write_layers(root, _RECTS)
    result = api.import_layered(root, "hero", layers, canvas=(16, 16), timestamp=TS)
    # No shift: feet derive from the raw leg bboxes — union (4, 8)-(8, 12).
    assert result.anchors["feet"] == (6, 11)
    assert result.anchors["root"] == (5, 6)


def test_bitmap_at_is_anchor_relative(root: Path) -> None:
    """Each region's bitmap sits at (layer bbox top-left) - (region anchor)."""
    layers = _write_layers(root, _RECTS)
    api.import_layered(root, "hero", layers, timestamp=TS)
    doc = api.get_asset(root, "hero")
    assert isinstance(doc, CharacterAsset)

    expected_topleft = {  # canvas coords (shifted by (-1, -1))
        "torso": (3, 3),
        "head": (3, 0),
        "arm_left": (0, 3),
        "arm_right": (7, 3),
        "leg_left": (3, 7),
        "leg_right": (5, 7),
    }
    for name, topleft in expected_topleft.items():
        region = doc.regions[name]
        anchor = doc.anchors[region.anchor]
        shape = region.shapes[0]
        assert isinstance(shape, BitmapShape)
        assert (shape.at[0] + anchor[0], shape.at[1] + anchor[1]) == topleft
