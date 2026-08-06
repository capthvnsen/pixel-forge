"""`ExternalFrameBackend`: a `RenderBackend` that loads pinned PNGs instead of drawing.

The shape DSL and a diffusion model sit at opposite ends of a detail-per-effort curve.
This backend is how art from the far end gets in: the spec keeps describing *structure*
-- directions, animations, frame timing, anchors, mirroring, palette -- and the pixels
come from files. Every validation rule, the sheet packer, the preview writer and the
Godot exporter run unchanged, because they all consume rendered `Canvas` objects and
have never known where those came from.

Mirroring keeps `LocalRenderBackend`'s exact semantics: a mirrored direction is defined
as its source direction's raster flipped, so it reads the *source's* file and calls
`Canvas.mirror_x()`. It never looks for a file of its own -- if a direction has real
art, it should not be declared in `mirror`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pixel_forge.animation.resolver import ResolvedFrame, resolve_frames
from pixel_forge.domain.hashing import file_hash
from pixel_forge.domain.palette import ResolvedPalette
from pixel_forge.domain.paths import safe_join
from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas.asset import SpriteAssetBase
from pixel_forge.schemas.source import pin_key


def _check_frame_file(
    path: Path,
    name: str,
    frames_dir: str,
    animation: str,
    direction: str,
    index: int,
    pinned: str | None,
) -> None:
    """Raise `RenderError` if `path` is missing, or (when `pinned` is given) if its
    sha256 no longer matches. Shared by `ExternalFrameBackend._load` and `verify_pins`
    so a pin mismatch raises identically whether discovered while actually rendering
    or while deciding if a cached build can still be trusted."""
    if not path.is_file():
        raise RenderError(
            f"external frame not found for {animation}/{direction}/{index}: "
            f"expected {frames_dir}/{name} under the asset directory"
        )
    if pinned is not None:
        digest = file_hash(path)
        if digest != pinned:
            raise RenderError(
                f"external frame {name!r} does not match its pin "
                f"(spec pins {pinned[:12]}..., file is {digest[:12]}...). The art "
                "changed underneath this spec; re-run `pixel-forge source pin` to "
                "accept the new pixels, which records it as a revision"
            )


def verify_pins(doc: SpriteAssetBase, asset_dir: Path) -> None:
    """Raise `RenderError` if any pinned external frame is missing or has drifted from
    its recorded sha256, without decoding any image.

    This is what lets a cache hit on `spec_hash` stay honest: `spec_hash` never moves
    when a file on disk changes without a re-pin, so `api.py` calls this before
    trusting a cached build for a `source:` asset. A doc with no `source` block or no
    pins yet is a no-op -- an unpinned asset has nothing here to verify.
    """
    source = doc.source
    if source is None or not source.pins:
        return
    for frame in resolve_frames(doc):
        if frame.mirrored_from is not None:
            continue
        key = pin_key(frame.animation, frame.direction, frame.index)
        pinned = source.pins.get(key)
        if pinned is None:
            continue
        name = source.filename(frame.animation, frame.direction, frame.index)
        path = safe_join(asset_dir, source.frames_dir, name)
        _check_frame_file(
            path, name, source.frames_dir, frame.animation, frame.direction, frame.index, pinned
        )


class ExternalFrameBackend:
    name = "external"

    def __init__(self, asset_dir: Path) -> None:
        self._asset_dir = asset_dir
        self._cache: dict[Path, Canvas] = {}

    def render_frame(
        self, doc: SpriteAssetBase, frame: ResolvedFrame, palette: ResolvedPalette
    ) -> Canvas:
        source = doc.source
        if source is None:
            raise RenderError(
                f"asset {doc.asset.id!r} has no `source:` block; ExternalFrameBackend "
                "renders only assets that declare one"
            )
        direction = frame.mirrored_from or frame.direction
        canvas = self._load(doc, direction, frame.animation, frame.index)
        return canvas.mirror_x() if frame.mirrored_from is not None else canvas

    def _load(self, doc: SpriteAssetBase, direction: str, animation: str, index: int) -> Canvas:
        source = doc.source
        assert source is not None
        name = source.filename(animation, direction, index)
        path = safe_join(self._asset_dir, source.frames_dir, name)

        cached = self._cache.get(path)
        if cached is not None:
            return cached.copy()

        key = pin_key(animation, direction, index)
        _check_frame_file(
            path, name, source.frames_dir, animation, direction, index, source.pins.get(key)
        )

        with Image.open(path) as img:
            canvas = Canvas.from_image(img.convert("RGBA"))
        expected = tuple(doc.asset.canvas)
        if (canvas.width, canvas.height) != expected:
            raise RenderError(
                f"external frame {name!r} is {canvas.width}x{canvas.height}, but "
                f"{doc.asset.id!r} declares canvas {expected[0]}x{expected[1]}"
            )
        self._cache[path] = canvas
        return canvas.copy()


def compute_source_pins(doc: SpriteAssetBase, asset_dir: Path) -> dict[str, str]:
    """sha256 of every file the spec's authored frames reference, keyed by `pin_key`.

    Missing files raise rather than being skipped: a pin set that silently omits the
    frames it could not find would look complete and pin nothing.
    """
    source = doc.source
    if source is None:
        raise RenderError(f"asset {doc.asset.id!r} has no `source:` block to pin")
    pins: dict[str, str] = {}
    for frame in resolve_frames(doc):
        if frame.mirrored_from is not None:
            continue
        name = source.filename(frame.animation, frame.direction, frame.index)
        path = safe_join(asset_dir, source.frames_dir, name)
        if not path.is_file():
            raise RenderError(
                f"cannot pin {doc.asset.id!r}: missing {source.frames_dir}/{name} "
                f"for {frame.animation}/{frame.direction}/{frame.index}"
            )
        pins[pin_key(frame.animation, frame.direction, frame.index)] = file_hash(path)
    return pins
