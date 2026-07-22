"""SEO / product-copy generation via OpenRouter (Sonnet by default).

``build_messages`` is pure and unit-tested; ``generate_seo`` is a thin call through the
injected :class:`~.openrouter.LLMTransport`. The model must ground every claim in the provided
facts — no invented materials, dimensions, or certifications — and translate non-English source
copy. Output is cached by ``Content Version`` = ``model:prompt_version`` so re-runs are cheap.
"""

from __future__ import annotations

import json
import re

from ..extraction.normalization import strip_html
from ..observability.errors import ErrorCode, ScannerError
from .normalize import base_tags, handle_from
from .openrouter import LLMTransport

_KEYS = ("title", "seo_title", "meta_description", "description_html", "image_alt_text", "tags")


def build_messages(row: dict, normalized: dict, config) -> list[dict]:
    seo = config.publishing.seo
    facts = {
        "raw_title": row.get("Product Name") or "",
        "raw_description": (strip_html(row.get("Description")) or "")[:1500],
        "collection": row.get("Collection"),
        "category": row.get("Syncee Category"),
        "product_type": normalized.get("Product Type"),
        "material": normalized.get("Material"),
        "dimensions": normalized.get("Dimensions"),
        "weight": normalized.get("Weight"),
        "price_eur": row.get("Proposed Retail Price"),
    }
    guidance = {
        "title": "clean product title, ≤70 chars, Title Case, no ALL-CAPS, no supplier codes/SKUs",
        "seo_title": f"≤{seo.max_title_len} chars, leads with the main keyword",
        "meta_description": f"≤{seo.max_meta_len} chars, one benefit-led sentence",
        "description_html": (
            "simple HTML (<p>,<ul>,<li>,<strong> only): a benefit-led opening, a short features "
            "list, specs (material/dimensions/weight if known), and a brief care line"
        ),
        "image_alt_text": "≤125 chars, plainly describes the product for accessibility",
        "tags": f"array of up to {seo.max_tags} lowercase keyword tags",
    }
    system = (
        "You are a senior e-commerce copywriter for RB Home, a European home & kitchen store. "
        f"Brand voice: {seo.brand_voice} Write in British English. Base every claim ONLY on the "
        "given facts — never invent materials, dimensions, certifications, or features. If the "
        "source text is in another language, translate it. Return STRICT JSON only, no prose."
    )
    user = (
        "FACTS:\n" + json.dumps(facts, ensure_ascii=False)
        + "\n\nReturn a JSON object with exactly these keys: " + json.dumps(list(_KEYS))
        + "\nField guidance:\n" + json.dumps(guidance, ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse(raw: str) -> dict:
    # Strip a ```json … ``` markdown fence the model sometimes adds despite json_mode.
    s = re.sub(r"^\s*```(?:json)?\s*", "", raw.strip())
    s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise ScannerError(ErrorCode.LLM_API_ERROR, "SEO model did not return valid JSON.")


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip(" ,.;:-")


def generate_seo(
    row: dict, normalized: dict, transport: LLMTransport, config, *, attempts: int = 2
) -> dict:
    """Generate SEO/content fields for one product. Returns Baserow-ready field dict."""
    seo = config.publishing.seo
    messages = build_messages(row, normalized, config)
    data = None
    last_err: ScannerError | None = None
    for _ in range(max(1, attempts)):
        raw = transport.chat(
            seo.model, messages, temperature=seo.temperature, max_tokens=2200, json_mode=True,
        )
        try:
            data = _parse(raw)
            break
        except ScannerError as exc:  # occasional malformed JSON — retry once
            last_err = exc
    if data is None:
        raise last_err or ScannerError(ErrorCode.LLM_API_ERROR, "SEO generation failed.")
    title = (data.get("title") or row.get("Product Name") or "").strip()

    existing = base_tags(row, normalized.get("Material"))  # Collection, Material, RB Home
    model_tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    tags: list[str] = []
    for t in existing + model_tags:
        if t.lower() not in {x.lower() for x in tags}:
            tags.append(t)

    return {
        "Cleaned Title": title[:255],
        "SEO Title": _clip(data.get("seo_title") or title, seo.max_title_len),
        "Meta Description": _clip(data.get("meta_description") or "", seo.max_meta_len),
        "Description HTML": (data.get("description_html") or "").strip(),
        "Image Alt Text": _clip(data.get("image_alt_text") or title, 125),
        "Handle": handle_from(title, row.get("Product Key", "")),
        "Publish Tags": ", ".join(tags),
        "Content Version": f"{seo.model}:{seo.prompt_version}",
    }
