"""Direction projection: one layered front view (+ optional back view) -> 8 directions.

This is the pivot piece: the user authors a single layered front-view character
(regions named by the usual conventions — ``torso``/``head``/``arm_left``/
``arm_right``/``leg_left``/``leg_right``, optional ``hair``/``weapon``/``shadow``,
optional face-detail regions) and this module derives the other seven views
programmatically. Direction names follow the repo's compass convention
(``sheet_import._COMPASS8``): ``north``, ``north_east``, ``east``, ``south_east``,
``south``, ``south_west``, ``west``, ``north_west``.

The projection model, per view:

- **south** reproduces the authored front view exactly (rest pose: anchors +
  authored layers, no animation transforms or direction overrides).
- **north** is the horizontal mirror of the front with face-detail regions
  stripped and embedded face features removed from the head region bitmap
  (eyes/mouth/visor never appear on the back of a head — whether they were
  authored as their own region or painted into the head itself). When the user
  supplies a `BackView`, its per-region canvases replace the mirrored fallback,
  region by region.
- **east** is the classic sprite side projection: every region is squashed
  horizontally toward the canvas centre axis (integer nearest-neighbour), which
  both narrows the silhouette and walks the limbs in toward the body line. The
  far-side arm/leg — the character's left pair when facing screen-right — is
  occluded entirely, so the profile shows exactly one arm and one leg (the near
  pair, drawn in front of the torso). Embedded face features on the far side of
  the head's own centre axis are removed before the squash, so the profile
  shows exactly the near-side eye instead of two eyes squeezed into a 3px span.
  Each ramp family in the palette is flipped light-end-to-dark-end (hi<->lo)
  before compositing, so the front's upper-left light re-orients to the
  near/chest side instead of staying glued to the character's back.
- **west / south_west / north_west** are exact ``mirror_x`` flips of
  east / south_east / north_east for `mirror_safe` regions — symmetry is by
  construction, not by hoping the math commutes. Non-`mirror_safe` regions are
  never flipped (same rule as `LocalRenderBackend._render_mirrored`); they are
  re-projected from the unflipped region canvas with the facing sign reversed.
- **diagonals** interpolate the parameters between front/back and side: a 3/4
  squash (between 1/1 and 1/2), the side-view limb occlusion order (both limbs
  kept — the far pair behind the torso, the near pair in front), face detail
  kept on the front diagonal and stripped on the back diagonal. The far-side
  limb pair is shaded one ramp step darker, so near and far limbs read as
  separate depths instead of a flat silhouette. When a `BackView` is supplied,
  the back diagonals squash *its* canvases, so a real authored back genuinely
  informs NE/NW.

Determinism contract (same as `RenderBackend`): all math is integer. The
squash maps every destination column to exactly one source column (inverse
nearest-neighbour, no holes, no collision rule) using doubled-integer centre
coordinates and half-away-from-zero rounding; flips are `np.fliplr`; shifts are
integer translates. Projected pixels only ever move existing pixels around —
no resampling, no blending, no colour ever appears that the palette did not
already paint into a region canvas. `project_directions` called twice on the
same inputs returns byte-identical canvases.

Known limitations (deliberate v1 scope): embedded face detail is found by
heuristic — interior ink-coloured clusters in the head bitmap (all four
orthogonal neighbours opaque) — so a helmet or hat whose interior uses the
outline colour could be misread as a face feature. The rest pose only is
projected; animation parameterisation per direction is a later integration
piece.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from pixel_forge.domain.palette import ResolvedPalette, cielab_lightness, rgb_to_hsl
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import RGBA, Canvas, Vec2
from pixel_forge.rendering.compositor import composite, plan_layers
from pixel_forge.schemas.animation import FrameSpec
from pixel_forge.schemas.asset import SpriteAssetBase
from pixel_forge.schemas.common import RegionTransform

#: All eight output directions, clockwise from north (compass convention).
DIRECTIONS: tuple[str, ...] = (
    "north",
    "north_east",
    "east",
    "south_east",
    "south",
    "south_west",
    "west",
    "north_west",
)

_FACE_TOKENS = frozenset({"face", "eye", "eyes", "visor", "mouth", "nose", "muzzle"})

# --- ramp inference thresholds (see `_infer_ramps` for the full contract) ----------
#: Hue-gap clustering tolerance: colours within this many degrees of their
#: neighbours on the hue wheel belong to one ramp family. 5.0 sits between the
#: demo skin ramp's widest internal gap (4.13 deg) and the skin-to-hair gap
#: (6.70 deg), so adjacent materials stay separate while each ramp stays whole.
_RAMP_HUE_TOL_DEG = 5.0
#: A family's hue spread (the arc its members cover) must stay within this —
#: a chain of *different* materials (skin, a leather pack, a hair colour) can
#: bridge a 5 deg tolerance run 10+ deg wide, which no single material ramp
#: spans (demo families cover 0.3-6.7 deg).
_RAMP_MAX_HUE_SPREAD_DEG = 8.0
#: Near-black floor (CIE L*): outline/detail colours — ink, the ground shadow,
#: derived outlines (which sit at HSL lightness 0.06-0.14, L* <= ~15) — never
#: join a ramp family, so a far limb can never shade "one step darker" into
#: black.
_RAMP_MIN_L_STAR = 20.0
#: Achromatic floor: pure/near-grey colours carry no hue family (saturation 0
#: reports hue 0, so every grey would otherwise merge into one "ramp").
_RAMP_MIN_SAT = 0.05
#: A family needs at least this much CIE L* spread to read as a ramp: the
#: demo's narrowest two-step family (pants hi/mid) spans 10.05 L*, while
#: same-hue pairs closer than ~8 L* are banding-level, visually a single tone.
_RAMP_MIN_SPREAD_L_STAR = 8.0
#: A TWO-member family must not span more than this (CIE L*): two members are
#: kept only when they look like *adjacent* ramp steps (build_ramp's per-step
#: delta is 16-25 L*), not a hi+lo pair with a missing mid — the way unrelated
#: same-hue materials (a face tone and a leather pack, hue 27-30, L* 76 vs 37)
#: accidentally pair up. The demo's two-step pants family (10.05 L*) passes.
_RAMP_MAX_TWO_STEP_SPREAD_L_STAR = 28.0
#: A ramp's steps share the material's saturation; value-greyed flat art
#: desaturates its shadows instead. The demo's real ramps stay within 1.83x
#: saturation of their extremes; the beige robot's five body tones span hue
#: 34.6-38.3 (one hue run) but saturation 0.12-0.60 (5.0x) — that set is a
#: flat-shaded body, not a ramp, and must render untouched.
_RAMP_MAX_SAT_RATIO = 2.5


@dataclass(frozen=True)
class CharacterRoles:
    """Region-name role discovery, mirroring `animation.cycles` conventions.

    `static` regions (named ``shadow`` or anchored at ``feet``) are never
    limb-shifted; `face` regions are stripped from back-facing views.
    """

    torso: str | None
    head: str | None
    arm_left: str | None
    arm_right: str | None
    leg_left: str | None
    leg_right: str | None
    face: frozenset[str]
    static: frozenset[str]


def _side(name: str) -> str | None:
    """Left/right token split, identical convention to `animation.cycles._side`:
    handles both ``arm_left`` and ``left_arm`` naming."""
    for token in name.split("_"):
        if token in ("l", "left"):
            return "left"
        if token in ("r", "right"):
            return "right"
    return None


def _is_face_detail(name: str) -> bool:
    lower = name.lower()
    return any(token in _FACE_TOKENS for token in lower.split("_"))


def discover_roles(
    doc: SpriteAssetBase, *, face_regions: Collection[str] | None = None
) -> CharacterRoles:
    """Classify `doc`'s regions into projection roles by name convention.

    `face_regions` overrides face-detail classification when given (the caller
    knows which regions are front-only detail); otherwise any region with a
    ``face``/``eye``/``visor``/``mouth``/``nose``/``muzzle`` name token counts.
    """
    torso: str | None = None
    head: str | None = None
    arm_left: str | None = None
    arm_right: str | None = None
    leg_left: str | None = None
    leg_right: str | None = None
    face: set[str] = set()
    static: set[str] = set()
    feet_anchor = "feet" if "feet" in doc.anchors else None

    for name, region in doc.regions.items():
        lower = name.lower()
        if "shadow" in lower or (feet_anchor is not None and region.anchor == feet_anchor):
            static.add(name)
        elif _is_face_detail(lower):
            face.add(name)
        elif torso is None and ("torso" in lower or "body" in lower):
            torso = name
        elif head is None and "head" in lower:
            head = name

    for name in doc.regions:
        if name in static or name in face:
            continue
        lower = name.lower()
        side = _side(lower)
        if "arm" in lower:
            if side == "left" and arm_left is None:
                arm_left = name
            elif side == "right" and arm_right is None:
                arm_right = name
        elif "leg" in lower:
            if side == "left" and leg_left is None:
                leg_left = name
            elif side == "right" and leg_right is None:
                leg_right = name

    return CharacterRoles(
        torso=torso,
        head=head,
        arm_left=arm_left,
        arm_right=arm_right,
        leg_left=leg_left,
        leg_right=leg_right,
        face=frozenset(doc.regions) & frozenset(face_regions)
        if face_regions is not None
        else frozenset(face),
        static=frozenset(static),
    )


@dataclass(frozen=True)
class BackView:
    """Optional user-supplied back view: region name -> canvas-sized, world-positioned
    `Canvas` (the same contract as the per-region front canvases this module renders).

    A region present here replaces the mirrored-front fallback in the north and
    back-diagonal views; a face-detail region present here is kept (the user drew
    it deliberately), one absent is stripped.
    """

    regions: Mapping[str, Canvas]


@dataclass(frozen=True)
class ProjectedRegion:
    """One region of one direction: the transformed, world-positioned canvas plus
    the effective draw layer for this direction (limb occlusion reorder applied).

    `anchor` is the world-space rotation pivot for this region in this view —
    the region's own anchor, mirrored when the view flips the region's content
    (`None` falls back to the doc's anchor, preserving callers that construct
    regions without one). `mirrored` is True when this region's canvas content
    is flipped relative to the authored front (a mirrored view, or a
    mirrored-back view): rotations and horizontal offsets must be negated
    during animation so opposite-facing walks stay exact mirrors.
    """

    name: str
    canvas: Canvas
    layer: int
    anchor: tuple[int, int] | None = None
    mirrored: bool = False


@dataclass(frozen=True)
class ProjectedView:
    """One direction of the character: per-region transformed canvases in draw
    order, plus where the view was derived from (`mirrored_from` is set when the
    view is a horizontal flip of another projected view, `None` otherwise)."""

    direction: str
    regions: tuple[ProjectedRegion, ...]
    mirrored_from: str | None

    def composite(self, canvas_size: Vec2) -> Canvas:
        """Flatten the view to a single `Canvas` by source-over blitting the
        region canvases in draw order."""
        canvas = Canvas(*canvas_size)
        for region in self.regions:
            canvas.blit(region.canvas, (0, 0))
        return canvas

    def region(self, name: str) -> ProjectedRegion:
        for region in self.regions:
            if region.name == name:
                return region
        raise ForgeError(f"direction {self.direction!r} has no region {name!r}")


@dataclass(frozen=True)
class _ViewParams:
    """Per-direction-family projection parameters (all integer)."""

    squash_num: int  # horizontal squash ratio num/den about the canvas centre axis
    squash_den: int
    far_shift: int  # extra px the far-side limbs move toward the centre axis
    near_shift: int  # extra px the near-side limbs move away from the centre axis
    reorder: bool  # far limbs behind the torso, near limbs in front of it
    face: bool  # face-detail regions visible in this view
    profile: bool  # true side view: strip far-side embedded face features first
    occlude_far_limbs: bool  # true side views hide the far-side limbs entirely so
    # the profile shows one arm + one leg; diagonals and front keep both
    shade_far_limbs: bool  # diagonals shade the far-side limb pair one ramp step
    # darker (depth cue); front/side/back views never do
    flip_light_side: bool  # re-orient the light end (hi<->lo) of every ramp so
    # the front's upper-left light lands on the near/chest side in this view
    flip_limbs: bool  # whether the light flip also applies to limb regions (true
    # only for true side views, where the near limb's authored light should
    # re-orient with the body; diagonals flip body regions only — their limbs
    # carry the far/near depth shading instead, and flipping a limb after
    # darkening would cancel the depth cue)
    region_squash: Mapping[str, tuple[int, int]] | None = None
    # Per-region squash overrides: keys are role categories ("head", "hair",
    # "torso", "arm", "leg"); a matching region uses that (num, den) instead
    # of the view's global squash_num/squash_den.  Unlisted categories fall
    # back to the view default.  None means no overrides (all regions use the
    # global ratio).  Keeps side-view volume (head/sphere ~4/5, torso ~2/3,
    # thin limbs ~1/2) rather than the flat global 1/2 that produces
    # cardboard cutouts.
    shade_far_half: bool = False
    # true side views darken the camera-FAR half of every body region one ramp
    # step after the light flip, so the profile reads as a lit chest over a
    # shaded back instead of a flat stripe (round-3 gauntlet critic: "no
    # volume — a flat 2D cardboard cutout turned sideways").


_FRONT = _ViewParams(
    squash_num=1,
    squash_den=1,
    far_shift=0,
    near_shift=0,
    reorder=False,
    face=True,
    profile=False,
    occlude_far_limbs=False,
    shade_far_limbs=False,
    flip_light_side=False,
    flip_limbs=False,
)
_SIDE = _ViewParams(
    squash_num=1,
    squash_den=2,
    far_shift=1,
    near_shift=0,
    reorder=True,
    face=True,
    profile=True,
    occlude_far_limbs=False,
    shade_far_limbs=True,
    flip_light_side=True,
    flip_limbs=True,
    region_squash={
        "head": (4, 5),
        "hair": (4, 5),
        "torso": (3, 4),
        "arm": (2, 3),
        "leg": (2, 3),
    },
    shade_far_half=True,
)
_DIAG_FRONT = _ViewParams(
    squash_num=3,
    squash_den=4,
    far_shift=0,
    near_shift=0,
    reorder=True,
    face=True,
    profile=False,
    occlude_far_limbs=False,
    shade_far_limbs=True,
    flip_light_side=True,
    flip_limbs=False,
)
_DIAG_BACK = _ViewParams(
    squash_num=3,
    squash_den=4,
    far_shift=0,
    near_shift=0,
    reorder=True,
    face=False,
    profile=False,
    occlude_far_limbs=False,
    shade_far_limbs=True,
    flip_light_side=True,
    flip_limbs=False,
)


def _iround(num: int, den: int) -> int:
    """round(num / den), halves away from zero, exact integer math (`den` > 0)."""
    if num >= 0:
        return (2 * num + den) // (2 * den)
    return -((-2 * num + den) // (2 * den))


def _squash_x(canvas: Canvas, num: int, den: int) -> Canvas:
    """Horizontal nearest-neighbour squash about the canvas's vertical centre axis.

    Inverse-mapped: every destination column samples exactly one source column,
    so the result has no holes and needs no collision rule. Centre coordinates
    are doubled (column x covers 2x + 1; the canvas centre axis is `width`), so
    no float ever touches a pixel decision. Squashing about the centre axis both
    narrows the silhouette and walks off-centre limbs in toward the body line —
    the whole side-view limb collapse falls out of the one mapping.
    """
    if num == den:
        return canvas.copy()
    if not 0 < num < den:
        raise ForgeError(f"_squash_x ratio must satisfy 0 < num < den, got {num}/{den}")
    w = canvas.width
    out = Canvas(w, canvas.height)
    for dx in range(w):
        sx2 = w + _iround((2 * dx + 1 - w) * den, num)
        sx = (sx2 - 1) // 2
        if 0 <= sx < w:
            out.array[:, dx] = canvas.array[:, sx]
    return out


def _away_sign(canvas: Canvas) -> int:
    """+1 when the canvas's content sits right of the centre axis, -1 when left,
    0 when empty or exactly centred. Doubled-integer comparison, no floats."""
    bbox = canvas.bbox()
    if bbox is None:
        return 0
    x0, _, x1, _ = bbox
    centre2 = x0 + x1 - 1  # doubled bbox centre column
    if centre2 > canvas.width:
        return 1
    if centre2 < canvas.width:
        return -1
    return 0


def _boundary_ink(canvas: Canvas) -> RGBA | None:
    """The region's 'ink' colour: the darkest colour on its boundary — an opaque
    pixel with at least one transparent 4-neighbour (canvas edges count as
    transparent). Falls back to the darkest colour anywhere on the canvas when
    no boundary pixel qualifies. None for an empty canvas."""
    rows = canvas.array.tolist()
    h, w = len(rows), len(rows[0])
    boundary: set[RGBA] = set()
    all_colors: set[RGBA] = set()
    for y in range(h):
        for x in range(w):
            r, g, b, a = rows[y][x]
            if a == 0:
                continue
            rgba = (r, g, b, a)
            all_colors.add(rgba)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if not (0 <= ny < h and 0 <= nx < w) or rows[ny][nx][3] == 0:
                    boundary.add(rgba)
                    break
    if not all_colors:
        return None
    source = boundary if boundary else all_colors
    return min(source, key=lambda c: (c[0] + c[1] + c[2], c))


def _interior_ink_clusters(canvas: Canvas) -> list[list[tuple[int, int]]]:
    """4-connected clusters of 'face-feature' pixels in `canvas`.

    A face-feature pixel is one whose colour equals the region's ink colour
    (see `_boundary_ink`) and whose four orthogonal neighbours are all opaque.
    An outline-ring pixel always touches transparency, so only embedded detail
    (eyes, mouth) qualifies. Cluster seeds and members are emitted in sorted
    order, so the result is a pure function of the pixels.
    """
    ink = _boundary_ink(canvas)
    if ink is None:
        return []
    rows = canvas.array.tolist()
    h, w = len(rows), len(rows[0])

    interior: set[tuple[int, int]] = set()
    for y in range(h):
        for x in range(w):
            if (rows[y][x][0], rows[y][x][1], rows[y][x][2], rows[y][x][3]) != ink:
                continue
            if all(
                0 <= y + dy < h and 0 <= x + dx < w and rows[y + dy][x + dx][3] != 0
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ):
                interior.add((x, y))

    clusters: list[list[tuple[int, int]]] = []
    remaining = set(interior)
    while remaining:
        start = min(remaining)  # deterministic seed
        remaining.discard(start)
        cluster: list[tuple[int, int]] = []
        stack = [start]
        while stack:
            px, py = stack.pop()
            cluster.append((px, py))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (px + dx, py + dy)
                if nxt in remaining:
                    remaining.discard(nxt)
                    stack.append(nxt)
        clusters.append(sorted(cluster))
    return clusters


def _repaint_color(canvas: Canvas, cluster: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    """The local fill colour to repaint a stripped face-feature cluster with.

    Modal opaque colour among the cluster's orthogonal neighbours (the pixels
    the feature was drawn on top of), deterministic tie-break by colour tuple.
    Falls back to the modal opaque colour of the whole canvas (excluding the
    ink colour) when the cluster has no opaque neighbours — a feature the strip
    should never meet, but the back view must never punch a hole in a head.
    """
    members = set(cluster)
    counts: dict[tuple[int, int, int, int], int] = {}
    for x, y in cluster:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= ny < canvas.height and 0 <= nx < canvas.width):
                continue
            if (nx, ny) in members:
                continue
            rgba = tuple(canvas.array[ny, nx])
            if rgba[3] == 0:
                continue
            counts[rgba] = counts.get(rgba, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    ink = _boundary_ink(canvas)
    all_counts: dict[tuple[int, int, int, int], int] = {}
    for y in range(canvas.height):
        for x in range(canvas.width):
            rgba = tuple(canvas.array[y, x])
            if rgba[3] == 0 or rgba == ink:
                continue
            all_counts[rgba] = all_counts.get(rgba, 0) + 1
    if all_counts:
        return max(all_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    raise ForgeError("cannot determine a fill colour to repaint stripped face detail")


def _strip_embedded_face(canvas: Canvas) -> Canvas:
    """Return `canvas` with every interior ink cluster (embedded face detail)
    repainted with its local fill colour — the back of a head carries no eyes
    or mouth, but the pixels it replaces must not become transparent holes
    (which render as eye-shaped dots on a background). Returns the input
    unchanged when there is nothing to strip. Only ever moves existing colours;
    no colour is invented."""
    clusters = _interior_ink_clusters(canvas)
    if not clusters:
        return canvas
    out = canvas.copy()
    for cluster in clusters:
        fill = _repaint_color(canvas, cluster)
        for x, y in cluster:
            out.set_pixel(x, y, fill)
    return out


def _strip_far_side_face(canvas: Canvas, facing: int) -> Canvas:
    """Remove embedded face clusters on the far side of the region's own centre
    axis, so a side-view squash keeps exactly the near-side feature — a true
    profile shows one eye, not two squeezed together. East (facing > 0) faces
    screen-right: far is left of the centre axis; west mirrors the rule. The
    centre axis is the region content's doubled-integer bbox centre, the same
    convention as `_away_sign`. Returns the input unchanged when nothing is
    stripped."""
    clusters = _interior_ink_clusters(canvas)
    if not clusters:
        return canvas
    bbox = canvas.bbox()
    if bbox is None:
        return canvas
    region_centre2 = bbox[0] + bbox[2] - 1
    strip: set[tuple[int, int]] = set()
    for cluster in clusters:
        xs = [x for x, _ in cluster]
        cluster_centre2 = min(xs) + max(xs)  # doubled bbox centre column
        if (facing > 0 and cluster_centre2 < region_centre2) or (
            facing < 0 and cluster_centre2 > region_centre2
        ):
            strip.update(cluster)
    if not strip:
        return canvas
    out = canvas.copy()
    for cluster in clusters:
        xs = [x for x, _ in cluster]
        cluster_centre2 = min(xs) + max(xs)
        is_far = (facing > 0 and cluster_centre2 < region_centre2) or (
            facing < 0 and cluster_centre2 > region_centre2
        )
        if not is_far:
            continue
        fill = _repaint_color(canvas, cluster)
        for x, y in cluster:
            out.set_pixel(x, y, fill)
    return out


def _shade_far_half(canvas: Canvas, facing: int, darker: Mapping[RGBA, RGBA]) -> Canvas:
    """Darken the camera-FAR half of a region canvas one ramp step.

    The side profile's depth cue: after the light flip re-orients the ramps so
    the near/chest side carries the light end, this shades the far/back half
    one step darker, so the profile reads as a lit chest over a shaded back
    instead of a flat stripe. A no-op when the palette has no ramp map.
    """
    if not darker:
        return canvas
    out = canvas.copy()
    w = out.width
    for y in range(out.height):
        for x in range(w):
            far_side = x < w // 2 if facing > 0 else x >= w // 2
            if not far_side:
                continue
            rgba = tuple(out.array[y, x])
            if rgba in darker:
                out.set_pixel(x, y, darker[rgba])
    return out


def _strip_far_side_detail(canvas: Canvas, facing: int) -> Canvas:
    """Remove all opaque pixels on the far side of a face-detail region's
    content centre axis, so a side-view profile shows exactly one eye at any
    squash ratio.  Unlike ``_strip_far_side_face`` (which targets interior-ink
    clusters inside the *head* region), this operates on face-detail regions
    whose entire content IS the face feature.  Returns the input unchanged
    when there is nothing to strip."""
    bbox = canvas.bbox()
    if bbox is None:
        return canvas
    region_centre2 = bbox[0] + bbox[2] - 1  # doubled content centre column
    strip: list[tuple[int, int]] = []
    for y in range(canvas.height):
        for x in range(canvas.width):
            if canvas.array[y, x][3] == 0:
                continue
            px2 = 2 * x  # doubled pixel centre
            if (facing > 0 and px2 < region_centre2) or (facing < 0 and px2 > region_centre2):
                strip.append((x, y))
    if not strip:
        return canvas
    out = canvas.copy()
    for x, y in strip:
        out.set_pixel(x, y, (0, 0, 0, 0))
    return out


def _region_squash_category(name: str, roles: CharacterRoles) -> str | None:
    """Map a region name to its squash-override category.

    Returns one of ``"head"``, ``"hair"``, ``"torso"``, ``"arm"``, ``"leg"``
    when the region matches a known role, or ``None`` for unmapped regions
    (static/face/shadow/weapon/…).  Hair detection is name-heuristic (any
    region whose lowercased name contains ``"hair"``).
    """
    if roles.head is not None and name == roles.head:
        return "head"
    if roles.torso is not None and name == roles.torso:
        return "torso"
    if name in (roles.arm_left, roles.arm_right):
        return "arm"
    if name in (roles.leg_left, roles.leg_right):
        return "leg"
    if "hair" in name.lower():
        return "hair"
    return None


def _limb_sets(roles: CharacterRoles, facing: int) -> tuple[frozenset[str], frozenset[str]]:
    """(far, near) limb region names for `facing` (+1 = faces screen-right).

    East-facing convention: the character's left side is away from the viewer,
    so ``*_left`` limbs are far. West-facing swaps the pair. Static regions are
    never limbs."""
    if facing > 0:
        far = {roles.arm_left, roles.leg_left}
        near = {roles.arm_right, roles.leg_right}
    else:
        far = {roles.arm_right, roles.leg_right}
        near = {roles.arm_left, roles.leg_left}
    return (
        frozenset(n for n in far if n is not None),
        frozenset(n for n in near if n is not None),
    )


@dataclass(frozen=True)
class _RampMap:
    """Inferred `*_hi/_mid/_lo` ramp families, keyed for pixel shading.

    Ramp inference works purely from the palette's hex colours: imported
    palettes carry generated ids (`c00..` — no role names survive import), so
    families are found by clustering, not by name. `darker` maps each colour
    id to the next step darker in its family (the darkest member maps to
    itself — far limbs clamp at `lo`); `flip` maps each id to the colour at
    the opposite end of its family (the side-view hi<->lo light re-orientation;
    a 3-step ramp's mid maps to itself). `rgba_darker`/`rgba_flip` are the
    same maps keyed by RGBA for direct `Canvas` remaps.
    """

    darker: Mapping[str, str]
    flip: Mapping[str, str]
    rgba_darker: Mapping[RGBA, RGBA]
    rgba_flip: Mapping[RGBA, RGBA]
    families: tuple[tuple[str, ...], ...]


def _rgba_map(palette: ResolvedPalette, id_map: Mapping[str, str]) -> dict[RGBA, RGBA]:
    """Resolve an id-keyed colour map to RGBA keys. Identity entries are dropped
    (remapping a colour to itself is a no-op); on a duplicated hex the first
    declared id wins, keeping the map deterministic."""
    out: dict[RGBA, RGBA] = {}
    for src_id, dst_id in id_map.items():
        if src_id == dst_id:
            continue
        src, dst = palette.rgba(src_id), palette.rgba(dst_id)
        if src not in out:
            out[src] = dst
    return out


def _infer_ramps(palette: ResolvedPalette) -> _RampMap:
    """Cluster the palette's opaque colours into ramp families by hue proximity.

    A ramp family is a run of colours that (1) sit close together on the hue
    wheel — each colour within `_RAMP_HUE_TOL_DEG` of its neighbours, (2) are
    not near-black (CIE L* >= `_RAMP_MIN_L_STAR`: ink, ground shadows and
    derived outlines never join a ramp, so a far limb can never shade one step
    darker into black), and (3) are not achromatic (saturation >=
    `_RAMP_MIN_SAT`: a grey reports hue 0, so every grey would otherwise merge
    into one bogus "ramp"). The run is kept as a ramp only when it has >= 2
    members whose CIE L* spread is >= `_RAMP_MIN_SPREAD_L_STAR` (the demo's
    narrowest two-step family spans 10.05 L*; same-hue pairs closer than ~8 L*
    are banding-level, visually one tone) and — for exactly two members — whose
    spread is <= `_RAMP_MAX_TWO_STEP_SPREAD_L_STAR` (two members must read as
    *adjacent* ramp steps, not a hi+lo pair with a missing mid, which is how
    unrelated same-hue materials accidentally pair up), whose hue spread stays
    within
    `_RAMP_MAX_HUE_SPREAD_DEG` (a chain of *different* materials — skin, a
    leather pack, a hair colour — can bridge a 5 deg tolerance run 10+ deg
    wide, which no single material ramp spans), and whose saturation ratio
    stays within `_RAMP_MAX_SAT_RATIO` (a ramp's steps share the material's
    saturation; value-greyed flat art desaturates its shadows instead — the
    beige robot's five body tones span hue 34.6-38.3 but saturation 0.12-0.60,
    a flat-shaded body, not a ramp).

    Members are ordered lightest-first (CIE L*, ties by id) so a family reads
    `(hi, mid, lo)`; families are returned sorted by (lowest hue, lightest id).
    Every decision is a deterministic sort or an exact comparison — same
    palette, same ramps, always.
    """
    entries: list[tuple[str, float, float, float]] = []  # (id, hue, sat, lstar)
    for cid in palette.ids:
        rgba = palette.rgba(cid)
        if rgba[3] != 255:
            continue
        lstar = cielab_lightness(rgba)
        if lstar < _RAMP_MIN_L_STAR:
            continue
        hue, sat, _ = rgb_to_hsl(rgba[:3])
        if sat < _RAMP_MIN_SAT:
            continue
        entries.append((cid, hue, sat, lstar))
    if len(entries) < 2:
        return _RampMap(
            darker={},
            flip={},
            rgba_darker={},
            rgba_flip={},
            families=(),
        )
    entries.sort(key=lambda entry: (entry[1], entry[0]))
    n = len(entries)
    gaps = [
        entries[i + 1][1] - entries[i][1] if i + 1 < n else entries[0][1] + 360.0 - entries[i][1]
        for i in range(n)
    ]
    # Rotate the hue circle so the largest gap is the run boundary: a ramp
    # straddling 0/360 (e.g. a red at hues 355..5) stays one family, and the
    # walk below never has to handle a wrap inside a run.
    start = gaps.index(max(gaps))
    rotated = entries[start + 1 :] + entries[: start + 1]
    runs: list[list[tuple[str, float, float, float]]] = []
    current: list[tuple[str, float, float, float]] = [rotated[0]]
    for entry in rotated[1:]:
        prev = current[-1]
        gap = entry[1] - prev[1] if entry[1] >= prev[1] else entry[1] + 360.0 - prev[1]
        if gap >= _RAMP_HUE_TOL_DEG:
            runs.append(current)
            current = []
        current.append(entry)
    runs.append(current)

    families: list[tuple[str, ...]] = []
    for run in runs:
        if len(run) < 2:
            continue
        l_stars = [entry[3] for entry in run]
        sats = [entry[2] for entry in run]
        base = run[0][1]
        norm_hues = [h if h >= base else h + 360.0 for h in (entry[1] for entry in run)]
        if max(l_stars) - min(l_stars) < _RAMP_MIN_SPREAD_L_STAR:
            continue
        if len(run) == 2 and max(l_stars) - min(l_stars) > _RAMP_MAX_TWO_STEP_SPREAD_L_STAR:
            continue
        if max(norm_hues) - min(norm_hues) > _RAMP_MAX_HUE_SPREAD_DEG:
            continue
        if max(sats) / min(sats) > _RAMP_MAX_SAT_RATIO:
            continue
        families.append(tuple(entry[0] for entry in sorted(run, key=lambda e: (-e[3], e[0]))))

    darker: dict[str, str] = {}
    flip: dict[str, str] = {}
    for family in families:
        for i, cid in enumerate(family):
            darker[cid] = family[min(i + 1, len(family) - 1)]
            flip[cid] = family[len(family) - 1 - i]
    return _RampMap(
        darker=darker,
        flip=flip,
        rgba_darker=_rgba_map(palette, darker),
        rgba_flip=_rgba_map(palette, flip),
        families=tuple(families),
    )


def _remap_colors(canvas: Canvas, mapping: Mapping[RGBA, RGBA]) -> Canvas:
    """Single-pass colour remap of `canvas` through `mapping`.

    One pass over the original pixels, so a permutation (a->b and b->a at the
    same time, e.g. the side-view hi<->lo light flip) is exact — a sequential
    `replace_color` chain would double-apply. Colours absent from `mapping`
    (outline ink, flat fills, transparency) are untouched. Pure colour remap:
    no pixels move, no colour is invented, and an empty mapping returns the
    input unchanged.
    """
    if not mapping:
        return canvas
    out = canvas.copy()
    arr = out.array
    for y in range(canvas.height):
        for x in range(canvas.width):
            r, g, b, a = (int(v) for v in arr[y, x])
            dst = mapping.get((r, g, b, a))
            if dst is not None:
                arr[y, x] = dst
    return out


def _render_region_canvases(doc: SpriteAssetBase, palette: ResolvedPalette) -> dict[str, Canvas]:
    """Each region composited alone onto a canvas-sized scratch (world position,
    rest pose), via the same `plan_layers`/`composite` path the renderer uses."""
    canvases: dict[str, Canvas] = {}
    for name, region in doc.regions.items():
        layers = plan_layers(doc, {name: region}, doc.anchors, {}, palette)
        canvases[name] = composite(doc.asset.canvas, layers, palette)
    return canvases


def _build_view(
    direction: str,
    base: Mapping[str, Canvas],
    doc: SpriteAssetBase,
    roles: CharacterRoles,
    params: _ViewParams,
    facing: int,
    ramps: _RampMap,
    *,
    mirrored_from: str | None = None,
    mirrored: bool = False,
) -> ProjectedView:
    """Project `base` region canvases into one direction: squash, ramp
    shading, limb shifts, and the occlusion reorder (far limbs one step below
    the torso layer, near limbs one step above — only when a torso was
    discovered to anchor the order). True side views (`params.occlude_far_limbs`)
    skip the far-side limb pair entirely instead of reordering it: the region
    never enters the projected view, so the profile reads as one arm + one leg.

    `ramps` drives the two depth/volume shading behaviours (hex-inferred, see
    `_infer_ramps`): diagonal views (`params.shade_far_limbs`) shade the
    far-side limb pair one ramp step darker after the squash, so near/far
    limbs separate; true side views (`params.flip_light_side`) flip each ramp
    family's light end (hi<->lo) so the front's upper-left light re-orients
    to the near/chest side in profile. Both are pure colour remaps — no pixels
    move — and both are exact no-ops on flat palettes (no inferred ramps) and
    on regions with no ramp colours (e.g. the ground shadow).

    `mirrored` marks views built from a mirrored-back base (north and the back
    diagonals when no `BackView` was supplied): their region content is flipped
    relative to the authored front, so animation must negate rotations/offsets
    and pivot about the mirrored anchors (see `ProjectedRegion`).
    """
    far, near = _limb_sets(roles, facing)
    torso_layer = doc.regions[roles.torso].layer if roles.torso is not None else None
    reorder = params.reorder and torso_layer is not None
    projected: list[ProjectedRegion] = []
    for name, canvas in base.items():
        if not params.face and name in roles.face:
            continue
        # True side views hide the far-side limb pair entirely: a profile shows
        # one arm and one leg (the near pair). The region never enters the
        # projected view, so animation transforms referencing it no-op naturally
        # and the mirrored opposite view inherits the occlusion by symmetry
        # (east hides *_left, west hides *_right — mirror images of each other).
        if params.occlude_far_limbs and name in far:
            continue
        # A true side view strips embedded face detail on the far side of the
        # head's own centre axis BEFORE the squash, so exactly the near-side
        # feature survives (a profile shows one eye, not two squeezed together).
        if name == roles.head and params.profile:
            canvas = _strip_far_side_face(canvas, facing)
        # Face-detail regions (eyes, visor, …) in true side views: strip the
        # far-side content explicitly so exactly one eye survives at ANY squash
        # ratio (ratio-independent — the far eye must never leak through).
        if name in roles.face and params.profile:
            canvas = _strip_far_side_detail(canvas, facing)
        # Per-region squash override: head/hair keep more volume in side views.
        num, den = params.squash_num, params.squash_den
        if params.region_squash is not None:
            cat = _region_squash_category(name, roles)
            if cat is not None and cat in params.region_squash:
                num, den = params.region_squash[cat]
        result = _squash_x(canvas, num, den)
        # Depth/volume shading (after the squash, before the limb shifts —
        # both are colour remaps, so order against the shifts is immaterial).
        if params.shade_far_limbs and name in far:
            result = _remap_colors(result, ramps.rgba_darker)
        if params.flip_light_side and (params.flip_limbs or (name not in far and name not in near)):
            result = _remap_colors(result, ramps.rgba_flip)
        if params.shade_far_half:
            result = _shade_far_half(result, facing, ramps.rgba_darker)
        # Face detail is authored facing the viewer; a squashed view must keep
        # the eye/visor on the side the character turns TOWARD. The squash's
        # inverse mapping can make the near-side feature's source column
        # unreachable, leaving only the far-side member of a symmetric pair
        # (e.g. the left eye on a right-facing diagonal — the classic
        # cross-eyed tell). When the surviving face content sits on the far
        # side of the canvas centre axis, mirror it across to the near side.
        if name in roles.face and num != den:
            sign = _away_sign(result)
            if sign and ((facing > 0 and sign < 0) or (facing < 0 and sign > 0)):
                result = result.mirror_x()
        layer = doc.regions[name].layer
        if name in far:
            sign = _away_sign(canvas)
            if params.far_shift and sign:
                result = result.translate((-sign * params.far_shift, 0))
            if reorder:
                assert torso_layer is not None
                layer = torso_layer - 1
        elif name in near:
            sign = _away_sign(canvas)
            if params.near_shift and sign:
                result = result.translate((sign * params.near_shift, 0))
            if reorder:
                assert torso_layer is not None
                layer = torso_layer + 1
        projected.append(
            ProjectedRegion(
                name=name,
                canvas=result,
                layer=layer,
                anchor=(
                    (
                        result.width - 1 - doc.anchors[doc.regions[name].anchor][0],
                        doc.anchors[doc.regions[name].anchor][1],
                    )
                    if mirrored
                    else doc.anchors[doc.regions[name].anchor]
                ),
                mirrored=mirrored,
            )
        )
    projected.sort(key=lambda region: (region.layer, region.name))
    return ProjectedView(direction=direction, regions=tuple(projected), mirrored_from=mirrored_from)


def _mirror_view(
    source: ProjectedView,
    direction: str,
    base: Mapping[str, Canvas],
    doc: SpriteAssetBase,
    roles: CharacterRoles,
    params: _ViewParams,
    facing: int,
    ramps: _RampMap,
) -> ProjectedView:
    """Derive the opposite-facing view by flipping `source` region-by-region.

    `mirror_safe` regions flip with `Canvas.mirror_x`, so opposite directions are
    exact mirrors by construction. Unsafe regions are never flipped (the rule
    from `LocalRenderBackend._render_mirrored`): they are re-projected from the
    unflipped base canvas with the facing sign reversed — squash about the
    centre axis is content-independent, only the limb-shift direction mirrors,
    and the same ramp shading (`ramps`, see `_build_view`) is re-applied so a
    mirror-unsafe limb shades exactly like its source-side counterpart.
    """
    projected: list[ProjectedRegion] = []
    for region in source.regions:
        if doc.regions[region.name].mirror_safe:
            # Flipped content: pivot about the mirrored anchor and negate
            # rotations/offsets at animation time, so the walk stays the exact
            # mirror of the source view's walk.
            base_anchor = (
                region.anchor
                if region.anchor is not None
                else doc.anchors[doc.regions[region.name].anchor]
            )
            projected.append(
                ProjectedRegion(
                    name=region.name,
                    canvas=region.canvas.mirror_x(),
                    layer=region.layer,
                    anchor=(region.canvas.width - 1 - base_anchor[0], base_anchor[1]),
                    mirrored=not region.mirrored,
                )
            )
            continue
        source_canvas = base[region.name]
        if region.name == roles.head and params.profile:
            source_canvas = _strip_far_side_face(source_canvas, facing)
        # Face-detail regions in true side views: strip far-side content.
        if region.name in roles.face and params.profile:
            source_canvas = _strip_far_side_detail(source_canvas, facing)
        # Per-region squash override (same resolution as _build_view).
        num, den = params.squash_num, params.squash_den
        if params.region_squash is not None:
            cat = _region_squash_category(region.name, roles)
            if cat is not None and cat in params.region_squash:
                num, den = params.region_squash[cat]
        result = _squash_x(source_canvas, num, den)
        far, near = _limb_sets(roles, facing)
        if params.shade_far_limbs and region.name in far:
            result = _remap_colors(result, ramps.rgba_darker)
        if params.flip_light_side and (
            params.flip_limbs or (region.name not in far and region.name not in near)
        ):
            result = _remap_colors(result, ramps.rgba_flip)
        if params.shade_far_half:
            result = _shade_far_half(result, facing, ramps.rgba_darker)
        shift = 0
        if region.name in far:
            shift = -_away_sign(base[region.name]) * params.far_shift
        elif region.name in near:
            shift = _away_sign(base[region.name]) * params.near_shift
        if shift:
            result = result.translate((shift, 0))
        projected.append(
            ProjectedRegion(
                name=region.name,
                canvas=result,
                layer=region.layer,
                anchor=doc.anchors[doc.regions[region.name].anchor],
            )
        )
    projected.sort(key=lambda region: (region.layer, region.name))
    return ProjectedView(
        direction=direction, regions=tuple(projected), mirrored_from=source.direction
    )


def _back_base(
    base: Mapping[str, Canvas], roles: CharacterRoles, back: BackView | None
) -> dict[str, Canvas]:
    """The region canvases back-facing views build from: a supplied `BackView`
    region wins; otherwise the mirrored front canvas, with face-detail regions
    stripped (a face on the back of the head is the classic auto-rotation tell).
    """
    out: dict[str, Canvas] = {}
    for name, canvas in base.items():
        if back is not None and name in back.regions:
            out[name] = back.regions[name]
        elif name in roles.face:
            continue
        elif name == roles.head:
            # The back of a head carries no face: strip embedded interior-ink
            # clusters (eyes painted into the head bitmap) before mirroring.
            out[name] = _strip_embedded_face(canvas).mirror_x()
        else:
            out[name] = canvas.mirror_x()
    return out


def project_directions(
    doc: SpriteAssetBase,
    palette: ResolvedPalette,
    *,
    back: BackView | None = None,
    face_regions: Collection[str] | None = None,
) -> dict[str, ProjectedView]:
    """Project the authored layered front view of `doc` into all 8 directions.

    Keys are the compass names in `DIRECTIONS` order. The character's rest pose
    is projected (anchors + authored layers; animation frames and
    `direction_overrides` are the integration layer's business, not this
    module's). Pure function of its inputs: no clock, no randomness, repeat
    calls return byte-identical canvases.
    """
    if not doc.regions:
        raise ForgeError(
            f"direction projection requires drawn regions; asset {doc.asset.id!r} has none "
            "(external-source assets already carry per-direction pixels)"
        )
    roles = discover_roles(doc, face_regions=face_regions)
    base = _render_region_canvases(doc, palette)
    back_base = _back_base(base, roles, back)
    ramps = _infer_ramps(palette)

    views: dict[str, ProjectedView] = {}
    views["south"] = _build_view("south", base, doc, roles, _FRONT, facing=1, ramps=ramps)
    mirrored_from: str | None = "south" if back is None else None
    back_mirrored = (
        back is None
    )  # a supplied BackView is authored space; the fallback is mirrored front
    views["north"] = _build_view(
        "north",
        back_base,
        doc,
        roles,
        _FRONT,
        facing=1,
        ramps=ramps,
        mirrored_from=mirrored_from,
        mirrored=back_mirrored,
    )
    views["east"] = _build_view("east", base, doc, roles, _SIDE, facing=1, ramps=ramps)
    views["south_east"] = _build_view(
        "south_east", base, doc, roles, _DIAG_FRONT, facing=1, ramps=ramps
    )
    views["north_east"] = _build_view(
        "north_east",
        back_base,
        doc,
        roles,
        _DIAG_BACK,
        facing=1,
        ramps=ramps,
        mirrored=back_mirrored,
    )
    views["west"] = _mirror_view(
        views["east"], "west", base, doc, roles, _SIDE, facing=-1, ramps=ramps
    )
    views["south_west"] = _mirror_view(
        views["south_east"], "south_west", base, doc, roles, _DIAG_FRONT, facing=-1, ramps=ramps
    )
    views["north_west"] = _mirror_view(
        views["north_east"], "north_west", back_base, doc, roles, _DIAG_BACK, facing=-1, ramps=ramps
    )
    return {direction: views[direction] for direction in DIRECTIONS}


def project_frames(
    doc: SpriteAssetBase,
    palette: ResolvedPalette,
    *,
    back: BackView | None = None,
    face_regions: Collection[str] | None = None,
) -> dict[str, Canvas]:
    """Convenience wrapper: `project_directions` with every view composited to a
    single `Canvas` — the 8 directional rest-pose frames, keyed by direction."""
    return {
        direction: view.composite(doc.asset.canvas)
        for direction, view in project_directions(
            doc, palette, back=back, face_regions=face_regions
        ).items()
    }


def _apply_frame_transform(
    canvas: Canvas,
    transform: RegionTransform,
    anchor: tuple[int, int],
    palette: ResolvedPalette,
    *,
    mirrored: bool = False,
) -> Canvas | None:
    """Apply one region's `RegionTransform` to its projected region canvas.

    `canvas` is the world-positioned projected canvas for the region; `anchor`
    is the region's world rotation pivot for this view (already mirrored for
    mirrored views). `offset` translates; `rotate` pivots about the anchor (+
    the spec's region-local pivot); `visible=False` drops the region;
    `color_swap` remaps palette colours. `mirrored` negates the rotation angle
    and horizontal offset so a mirrored view animates as the exact mirror of
    its source view (same physical limb motion seen from the other side).
    Returns `None` when the region must be hidden. Pure integer canvas math —
    byte-identical on repeat calls.
    """
    if transform.visible is False:
        return None
    result = canvas
    if transform.color_swap:
        for src_id, dst_id in transform.color_swap.items():
            src = palette.rgba(src_id)
            dst = palette.rgba(dst_id)
            if src in result.colors():
                result = result.replace_color(src, dst)
    if transform.offset != (0, 0):
        dx, dy = transform.offset
        if mirrored:
            dx = -dx
        result = result.translate((dx, dy))
    if transform.rotate is not None and transform.rotate.angle_deg % 360.0 != 0.0:
        pivot = transform.rotate.pivot or (0, 0)
        angle_deg = transform.rotate.angle_deg
        if mirrored:
            angle_deg = -angle_deg
        result = result.rotate((anchor[0] + pivot[0], anchor[1] + pivot[1]), angle_deg)
    return result


def project_animated_frames(
    doc: SpriteAssetBase,
    palette: ResolvedPalette,
    frames: Sequence[FrameSpec],
    *,
    back: BackView | None = None,
    face_regions: Collection[str] | None = None,
) -> dict[str, list[Canvas]]:
    """Render a sequence of animation frames across all 8 projected directions.

    This is the sprite factory's animation spine: the user authors ONE layered
    front view, `project_directions` derives the 8 directional rest poses, and
    this function applies each `FrameSpec`'s per-region transforms to every
    projected view — so a walk cycle authored once in spec space runs through
    all 8 angles. Returns `{direction: [canvas per frame]}`, byte-identical on
    repeat calls.

    Each frame's transform is applied to the region's *projected* canvas (offset
    -> translate, rotate -> pivot about the joint anchor, visible/color_swap),
    then the view composites in its projected layer order — so the side view's
    near-arm-in-front occlusion reorder is preserved while the limb articulates.
    Regions not named in a frame's transforms keep their projected rest pose.
    """
    views = project_directions(doc, palette, back=back, face_regions=face_regions)
    out: dict[str, list[Canvas]] = {}
    for direction, view in views.items():
        direction_frames: list[Canvas] = []
        for frame in frames:
            canvas = Canvas(*doc.asset.canvas)
            for region in view.regions:
                transform = frame.transforms.get(region.name, RegionTransform())
                anchor = (
                    region.anchor
                    if region.anchor is not None
                    else doc.anchors[doc.regions[region.name].anchor]
                )
                rendered = _apply_frame_transform(
                    region.canvas, transform, anchor, palette, mirrored=region.mirrored
                )
                if rendered is not None:
                    canvas.blit(rendered, (0, 0))
            direction_frames.append(canvas)
        out[direction] = direction_frames
    return out
