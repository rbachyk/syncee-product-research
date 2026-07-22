"""Deterministic background removal → product on pure white + soft shadow.

Faithful by construction: rembg isolates the product; we crop to it, centre it on a white
square with a consistent margin, and add a soft synthetic drop shadow. Nothing about the
product is added, removed, or restyled — the guardrail is satisfied structurally, unlike the
generative path. ``composite_on_white`` is pure (inject the mask); only ``remove_background``
loads rembg + its model.
"""

from __future__ import annotations

import io

from ..observability.errors import ErrorCode, ScannerError

try:
    from PIL import Image, ImageFilter
except ImportError as exc:  # pragma: no cover
    raise ScannerError(ErrorCode.IMAGE_PROCESSING_ERROR, "Pillow is required.") from exc

_SESSIONS: dict = {}


def remove_background(source_bytes: bytes, model: str) -> Image.Image:
    """Run rembg → RGBA product on transparent. Live-only (loads the model, cached per model)."""
    try:
        from rembg import new_session, remove
    except ImportError as exc:  # pragma: no cover
        raise ScannerError(
            ErrorCode.IMAGE_PROCESSING_ERROR,
            "rembg is not installed — needed for the deterministic cutout path.",
        ) from exc
    if model not in _SESSIONS:
        _SESSIONS[model] = new_session(model)
    cut = remove(source_bytes, session=_SESSIONS[model])
    return Image.open(io.BytesIO(cut)).convert("RGBA")


def _hex(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def _tight_bbox(rgba: Image.Image) -> tuple | None:
    """Bounding box of solidly-opaque pixels — ignores rembg's faint edge fringe so the
    product centres precisely instead of being skewed by stray semi-transparent pixels."""
    solid = rgba.split()[-1].point(lambda a: 255 if a > 128 else 0)
    return solid.getbbox()


def _dominant_color(product: Image.Image) -> tuple[int, int, int]:
    """Average colour of the product's opaque pixels (small sample for speed)."""
    small = product.resize((64, 64))
    px = small.load()
    r = g = b = n = 0
    for y in range(64):
        for x in range(64):
            pr, pg, pb, pa = px[x, y]
            if pa > 60:
                r, g, b, n = r + pr, g + pg, b + pb, n + 1
    if not n:
        return (190, 188, 185)
    return (r // n, g // n, b // n)


_DEFAULT_PASTELS = ["#EAE3D6", "#DCE3D3", "#F0E2DF", "#DBE3E9", "#E9DBD0", "#E3DDE8", "#D8E5DF"]


def _backdrop_tone(product: Image.Image, cut) -> tuple[int, int, int]:
    """Pick a calm pastel that harmonises with the product (never a muddy grey average)."""
    import colorsys

    tones = [_hex(p) for p in (getattr(cut, "pastel_palette", None) or _DEFAULT_PASTELS)]
    dr, dg, db = (c / 255 for c in _dominant_color(product))
    h, _l, s = colorsys.rgb_to_hls(dr, dg, db)

    if s < 0.12:
        # Near-neutral product (wood, grey, black/white) → the calmest, most neutral pastel.
        return min(tones, key=lambda t: colorsys.rgb_to_hls(*(c / 255 for c in t))[2])

    def hue_dist(t: tuple[int, int, int]) -> float:
        th = colorsys.rgb_to_hls(*(c / 255 for c in t))[0]
        d = abs(th - h)
        return min(d, 1 - d)

    # Colourful product → the pastel closest in hue (analogous = calm, tonal harmony).
    return min(tones, key=hue_dist)


def _lit_canvas(size: int, base: tuple[int, int, int], strength: float) -> Image.Image:
    """Square backdrop with a soft radial light gradient (brighter upper-centre) for depth."""
    import numpy as np

    yy, xx = np.mgrid[0:size, 0:size].astype("float32")
    cx, cy = size * 0.5, size * 0.36  # light source: upper-centre
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (size * 0.92)
    mult = (1.0 + strength) - (2.0 * strength) * np.clip(dist, 0.0, 1.0)
    arr = np.clip(np.array(base, dtype="float32")[None, None, :] * mult[:, :, None], 0, 255)
    return Image.fromarray(arr.astype("uint8"), "RGB")


def composite_on_white(rgba: Image.Image, config) -> bytes:
    """Centre the product on a palette-tinted, softly-lit backdrop with a directional shadow."""
    img = config.publishing.images
    cut = config.publishing.images.cutout
    size = img.target_size

    bbox = _tight_bbox(rgba) or rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)

    inner = int(size * (1 - 2 * cut.margin_pct))
    scale = min(inner / rgba.width, inner / rgba.height)
    product = rgba.resize(
        (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))), Image.LANCZOS
    )
    ox, oy = (size - product.width) // 2, (size - product.height) // 2  # precise centre

    if cut.background_mode == "auto_tint":
        base = _backdrop_tone(product, cut)
        canvas = _lit_canvas(size, base, cut.gradient_strength)
    else:
        canvas = Image.new("RGB", (size, size), _hex(cut.background))

    if cut.shadow:
        alpha = product.split()[-1]
        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        blot = Image.new("RGBA", product.size, (30, 26, 22, int(255 * cut.shadow_opacity)))
        off = int(size * cut.shadow_offset_pct)  # light upper-left → shadow lower-right
        shadow.paste(blot, (ox + off, oy + off), alpha)
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, int(size * cut.shadow_blur_pct))))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")

    canvas.paste(product, (ox, oy), product)

    out = io.BytesIO()
    canvas.save(out, format=img.format, quality=img.quality, optimize=True)
    return out.getvalue()


def cutout_on_white(source_bytes: bytes, config, *, remover=None) -> bytes:
    """Full cutout path: remove background → composite. ``remover`` is injectable for tests."""
    remove_fn = remover or (lambda b: remove_background(b, config.publishing.images.cutout.model))
    return composite_on_white(remove_fn(source_bytes), config)
