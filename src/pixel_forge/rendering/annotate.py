"""Diagnostic overlays for the vision loop (Contract 5, bitmap-regions-and-vision-loop).

An agent authoring `at: [-10, -14]` coordinates never sees the frame it just described.
These functions draw baselines, anchors, and the silhouette bounding box onto a *copy*
of a frame or contact sheet -- never onto a canvas that might later be exported -- using
colours chosen to be implausible in any hand-authored or imported sprite palette
(saturated pink, green, amber, and a desaturated blue-grey), so an overlay pixel can
never be mistaken for art.
"""

from __future__ import annotations

from collections.abc import Mapping

from pixel_forge.rendering.canvas import RGBA, Canvas, Vec2
from pixel_forge.rendering.font import draw_text, text_width
from pixel_forge.rendering.sheet import SheetCell, SpriteSheet, build_contact_sheet

OVERLAY_BASELINE: RGBA = (255, 64, 128, 255)
OVERLAY_ANCHOR: RGBA = (64, 255, 128, 255)
OVERLAY_BBOX: RGBA = (255, 200, 32, 255)
OVERLAY_GRID: RGBA = (72, 72, 96, 255)

_LABEL_GLYPH_H = 5


def _draw_grid(canvas: Canvas, step: int) -> None:
    for x in range(0, canvas.width, step):
        canvas.draw_line((x, 0), (x, canvas.height - 1), OVERLAY_GRID)
    for y in range(0, canvas.height, step):
        canvas.draw_line((0, y), (canvas.width - 1, y), OVERLAY_GRID)


def _draw_crosshair(canvas: Canvas, x: int, y: int) -> None:
    for px, py in ((x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        canvas.set_pixel(px, py, OVERLAY_ANCHOR)


def _draw_anchor_label(canvas: Canvas, name: str, x: int, y: int) -> None:
    # ponytail: no layout solver for overlapping labels -- on a small 1x canvas,
    # neighbouring anchor labels will collide. The only guarantee here is staying
    # inside the canvas bounds; the readable view is the upscaled one from
    # build_annotated_contact / upscale_view.
    tw = text_width(name)
    lx = max(0, min(x + 3, canvas.width - tw))
    ly = max(0, min(y - 2, canvas.height - _LABEL_GLYPH_H))
    draw_text(canvas, name, (lx, ly), OVERLAY_ANCHOR)


def annotate_frame(
    frame: Canvas,
    *,
    baseline_y: int | None = None,
    anchors: Mapping[str, Vec2] | None = None,
    bbox: bool = True,
    grid: int = 0,
    labels: bool = True,
) -> Canvas:
    """Draw diagnostic overlays on a copy of `frame`; `frame` itself is never modified.

    Draw order -- grid, bbox, baseline, anchors -- keeps later marks visible over
    earlier ones. The silhouette bbox is measured from `frame`'s own opaque pixels
    before any overlay is drawn, so the grid can never widen it.
    """
    canvas = frame.copy()
    silhouette = canvas.bbox() if bbox else None

    if grid > 0:
        _draw_grid(canvas, grid)

    if silhouette is not None:
        x0, y0, x1, y1 = silhouette
        canvas.draw_rect((x0, y0), (x1 - x0, y1 - y0), OVERLAY_BBOX, fill=False)

    if baseline_y is not None:
        canvas.draw_line((0, baseline_y), (canvas.width - 1, baseline_y), OVERLAY_BASELINE)

    if anchors:
        for name, (ax, ay) in anchors.items():
            _draw_crosshair(canvas, ax, ay)
            if labels:
                _draw_anchor_label(canvas, name, ax, ay)

    return canvas


def upscale_view(frame: Canvas, scale: int) -> Canvas:
    """Integer nearest-neighbour upscale for viewing. `Canvas.scale` already raises for
    `scale < 1`."""
    return frame.scale(scale)


def build_annotated_contact(
    sheet: SpriteSheet,
    *,
    baseline_y: int | None = None,
    anchors: Mapping[str, Vec2] | None = None,
    scale: int = 4,
    labels: Mapping[tuple[str, str], str] | None = None,
    background: RGBA = (24, 24, 32, 255),
) -> Canvas:
    """Contact sheet with diagnostic overlays, one baseline/anchor set applied to every
    cell. `sheet` (and its `.image`) is never mutated.

    Upscales the packed sheet image first, then draws overlays directly at that view
    scale (baseline/anchor coordinates multiplied by `scale`), so a 1px baseline stays
    1px and crisp instead of `Canvas.scale` blowing a pre-drawn 1px line up into a
    `scale`x`scale` slab.
    """
    upscaled = upscale_view(sheet.image, scale)
    scaled_cells = tuple(
        SheetCell(
            direction=c.direction,
            animation=c.animation,
            index=c.index,
            x=c.x * scale,
            y=c.y * scale,
            w=c.w * scale,
            h=c.h * scale,
        )
        for c in sheet.cells
    )
    scaled_anchors = (
        {name: (x * scale, y * scale) for name, (x, y) in anchors.items()}
        if anchors is not None
        else None
    )
    scaled_baseline = baseline_y * scale if baseline_y is not None else None

    for cell in scaled_cells:
        view = Canvas(cell.w, cell.h)
        view.array[:] = upscaled.array[cell.y : cell.y + cell.h, cell.x : cell.x + cell.w]
        annotated = annotate_frame(
            view, baseline_y=scaled_baseline, anchors=scaled_anchors, bbox=True, grid=0
        )
        upscaled.blit(annotated, (cell.x, cell.y))

    scaled_sheet = SpriteSheet(
        image=upscaled, cells=scaled_cells, columns=sheet.columns, rows=sheet.rows
    )
    return build_contact_sheet(scaled_sheet, labels=labels, scale=1, background=background)
