"""Publish-ready image pipeline: download → QA → AI transform → deterministic finish.

The AI step (background cleanup / framing on the configured image model) is tightly constrained
so it never alters the *product itself* — that would misrepresent what ships. The finish step
(Pillow) guarantees an exact, consistent output (square, target size, JPEG) regardless of what
the model returns. ``finish_image``/``assess_source``/``build_transform_prompt`` are pure; only
``download`` and ``process_image`` touch the network.
"""

from __future__ import annotations

import io

import httpx

from ..observability.errors import ErrorCode, ScannerError
from .openrouter import LLMTransport

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise ScannerError(
        ErrorCode.IMAGE_PROCESSING_ERROR, "Pillow is required for image prep."
    ) from exc


def build_transform_prompt(config) -> str:
    """Product-faithful restaging: keep the product, strip everything else (props/text/bg)."""
    return (
        "Turn this into a premium e-commerce product photo. Keep the PRODUCT exactly as it is — "
        "identical shape, colour, materials, texture, proportions, and any text or logo that is "
        "physically printed ON the product. Remove everything that is NOT the product: the "
        "original background, any surrounding props, hands or people, and any overlaid "
        "promotional text, captions, watermarks, or brand logos that sit on the image rather "
        "than on the product itself. Place the product centred on a soft, calm pastel studio "
        "backdrop whose tone gently complements the product's colours (not stark white, not "
        "grey). Add soft directional studio lighting from the upper-left and a subtle natural "
        "contact shadow beneath the product for depth. Square 1:1 composition, photorealistic, "
        "sharp focus. Output a single image."
    )


def _background_color(im: Image.Image) -> tuple[int, int, int]:
    """Median colour of a thin border frame — the background surrounding a centred product."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    xs = range(0, w, max(1, w // 64))
    ys = range(0, h, max(1, h // 64))
    samples = [px[x, 0] for x in xs] + [px[x, h - 1] for x in xs]
    samples += [px[0, y] for y in ys] + [px[w - 1, y] for y in ys]
    mid = len(samples) // 2
    return tuple(sorted(s[c] for s in samples)[mid] for c in range(3))  # type: ignore[return-value]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def assess_source(image_bytes: bytes, config) -> dict:
    """QA the *source* image: resolution + low-res flag (quality can't be invented)."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        w, h = im.size
    smallest = min(w, h)
    low = smallest < config.publishing.images.min_source_px
    notes = []
    if low:
        notes.append(f"low-res source {w}×{h} (min {config.publishing.images.min_source_px}px)")
    return {"source_px": f"{w}×{h}", "low_res": low, "notes": notes}


def border_std(image_bytes: bytes) -> float:
    """Mean per-channel std of a border frame — low = plain studio bg, high = busy/lifestyle."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    b = max(2, min(w, h) // 25)
    frame = []
    for y in list(range(b)) + list(range(h - b, h)):
        frame += [px[x, y] for x in range(0, w, max(1, w // 48))]
    for x in list(range(b)) + list(range(w - b, w)):
        frame += [px[x, y] for y in range(0, h, max(1, h // 48))]
    n = len(frame)
    stds = []
    for c in range(3):
        vals = [s[c] for s in frame]
        mean = sum(vals) / n
        stds.append((sum((v - mean) ** 2 for v in vals) / n) ** 0.5)
    return sum(stds) / 3


def classify_shot(image_bytes: bytes, config) -> str:
    """Route an image: 'clean' (plain background → cutout) or 'lifestyle' (busy → generative)."""
    threshold = config.publishing.images.lifestyle_border_std
    return "clean" if border_std(image_bytes) < threshold else "lifestyle"


def finish_image(image_bytes: bytes, config) -> bytes:
    """Deterministic finish: flatten → pad to square on the pad colour → resize → JPEG."""
    img = config.publishing.images
    try:
        src = Image.open(io.BytesIO(image_bytes))
        src.load()
    except Exception as exc:  # noqa: BLE001 - any decode failure is terminal here
        raise ScannerError(ErrorCode.IMAGE_PROCESSING_ERROR, f"Cannot decode image: {exc}") from exc

    # Fill colour for alpha-flatten + square padding: sampled background (seamless) or fixed.
    if img.pad_mode == "auto":
        pad = _background_color(src)
    else:
        pad = _hex_to_rgb(img.pad_color)

    if src.mode in ("RGBA", "LA", "P"):
        rgba = src.convert("RGBA")
        flat = Image.new("RGB", rgba.size, pad)
        flat.paste(rgba, mask=rgba.split()[-1])
        src = flat
    else:
        src = src.convert("RGB")

    side = max(src.size)
    canvas = Image.new("RGB", (side, side), pad)
    canvas.paste(src, ((side - src.width) // 2, (side - src.height) // 2))
    canvas = canvas.resize((img.target_size, img.target_size), Image.LANCZOS)

    out = io.BytesIO()
    canvas.save(out, format=img.format, quality=img.quality, optimize=True)
    return out.getvalue()


def download(url: str, *, timeout: float = 30.0) -> bytes:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        # Some supplier CDNs ship broken TLS certs (hostname mismatch). Product images are
        # public, non-sensitive assets — retry once insecurely rather than lose the product.
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True, verify=False)  # noqa: S501
        except httpx.HTTPError as exc:
            raise ScannerError(
                ErrorCode.IMAGE_PROCESSING_ERROR, f"Image download failed: {exc}"
            ) from exc
    if r.status_code != 200 or not r.content:
        raise ScannerError(
            ErrorCode.IMAGE_PROCESSING_ERROR, f"Image download {url[:60]} → {r.status_code}"
        )
    return r.content


def _route(source: bytes, config) -> str:
    """Decide the image path: 'cutout' or 'generative' (honours the configured method)."""
    method = config.publishing.images.method
    if method == "cutout":
        return "cutout"
    if method == "generative":
        return "generative"
    # hybrid: clean studio shots → deterministic cutout; busy/lifestyle → generative.
    if not config.publishing.images.cutout.enabled:
        return "generative"
    return "cutout" if classify_shot(source, config) == "clean" else "generative"


def _generative(source: bytes, transport: LLMTransport, config, qa: dict) -> bytes:
    tconf = config.publishing.images.transform
    if not tconf.enabled:
        qa["transformed"] = False
        return finish_image(source, config)
    try:
        edited = transport.edit_image(tconf.model, build_transform_prompt(config), source)
        qa["transformed"] = True
        return finish_image(edited, config)
    except ScannerError as exc:
        qa["transformed"] = False
        qa["notes"].append(f"AI transform failed ({exc.code.value}); used original")
        return finish_image(source, config)


def process_image(url: str, transport: LLMTransport, config) -> tuple[bytes, dict]:
    """Full pipeline for one image URL. Returns (finished JPEG bytes, QA dict)."""
    from .cutout import cutout_on_white

    source = download(url)
    qa = assess_source(source, config)
    method = _route(source, config)
    qa["method"] = method

    if method == "cutout":
        try:
            return cutout_on_white(source, config), qa
        except ScannerError as exc:
            # Cutout failed (e.g. rembg missing) → fall back to generative + flag it.
            qa["notes"].append(f"cutout failed ({exc.code.value}); used generative")
            qa["method"] = "generative"
            return _generative(source, transport, config, qa), qa

    return _generative(source, transport, config, qa), qa
