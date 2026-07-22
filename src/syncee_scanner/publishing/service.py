"""Publish-prep orchestration: approved/selected products → Shopify-ready Baserow records.

For each target product: deterministic normalize → SEO copy (OpenRouter) → image transform +
finish → upload, then set ``Publish-Prep Status``. Nothing publishes; this only fills the
Baserow fields the Gallery QA view renders. The transport is injected so tests run offline.
"""

from __future__ import annotations

from ..extraction.normalization import slugify
from ..models import PublishPrepStatus, SelectionStatus
from .images import process_image
from .normalize import normalize_fields
from .openrouter import LLMTransport
from .seo import generate_seo

# Products eligible for publish-prep: chosen for an assortment (candidate) or already selected.
_ELIGIBLE = {
    SelectionStatus.INITIAL_ASSORTMENT_CANDIDATE.value,
    SelectionStatus.INITIAL_ASSORTMENT_SELECTED.value,
    SelectionStatus.NEW_ARRIVAL_CANDIDATE.value,
    SelectionStatus.NEW_ARRIVAL_SELECTED.value,
}


def iter_targets(
    persistence, *, keys: list[str] | None = None, limit: int | None = None
) -> list[dict]:
    rows = [r for r in persistence.iter_products() if r.get("Selection Status") in _ELIGIBLE]
    if keys:
        wanted = set(keys)
        rows = [r for r in rows if r.get("Product Key") in wanted or str(r.get("id")) in wanted]
    rows.sort(key=lambda r: (r.get("Collection") or "", -(r.get("Product Score") or 0)))
    return rows[:limit] if limit else rows


def _derive_status(
    content_done: bool, image_done: bool, qa: dict | None, *, errored: bool = False
) -> str:
    failed_note = bool(qa and any("failed" in n for n in (qa.get("notes") or [])))
    flagged = errored or bool(qa and (qa.get("low_res") or failed_note))
    if content_done and image_done and not flagged:
        return PublishPrepStatus.READY_TO_PUBLISH.value
    if flagged:
        return PublishPrepStatus.NEEDS_ATTENTION.value
    if image_done:
        return PublishPrepStatus.IMAGES_READY.value
    if content_done:
        return PublishPrepStatus.CONTENT_READY.value
    return PublishPrepStatus.NOT_STARTED.value


def prep_product(
    row: dict, persistence, transport: LLMTransport, config, *,
    do_content: bool = True, do_images: bool = True, force: bool = False,
) -> dict:
    """Run publish-prep for one product; write fields + image; return a summary."""
    key = row.get("Product Key")
    normalized = normalize_fields(row)
    fields: dict = dict(normalized)
    summary: dict = {"key": key, "name": row.get("Product Name"), "content": False,
                     "image": False, "qa": None, "errors": []}

    content_version = f"{config.publishing.seo.model}:{config.publishing.seo.prompt_version}"
    content_current = row.get("Content Version") == content_version and row.get("Cleaned Title")

    if do_content and config.publishing.seo.enabled and (force or not content_current):
        try:
            fields.update(generate_seo(row, normalized, transport, config))
            summary["content"] = True
        except Exception as exc:  # noqa: BLE001 - surface, don't abort the batch
            summary["errors"].append(f"seo: {exc}")
    elif content_current:
        summary["content"] = True

    qa = None
    image_url = row.get("Main Image URL") or normalized.get("Original Image URL")
    already_imaged = bool(row.get("Processed Image")) and not force
    if do_images and image_url and not already_imaged:
        try:
            content, qa = process_image(image_url, transport, config)
            filename = f"{slugify(key)}.jpg"
            persistence.set_product_image(row["id"], content, filename)
            method = qa.get("method", "?")
            notes = "; ".join(qa.get("notes") or [])
            qa_text = f"{method} · {qa.get('source_px')}"
            fields["Image QA"] = f"{qa_text} · {notes}" if notes else qa_text
            summary["image"] = True
            summary["qa"] = qa
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"image: {exc}")
    elif already_imaged:
        summary["image"] = True

    errored = bool(summary["errors"])
    if errored and not fields.get("Image QA"):
        fields["Image QA"] = "; ".join(summary["errors"])
    fields["Publish-Prep Status"] = _derive_status(
        summary["content"], summary["image"], qa, errored=errored
    )
    persistence.update_product(row["id"], {k: v for k, v in fields.items() if v is not None})
    summary["status"] = fields["Publish-Prep Status"]
    return summary


def run_publish_prep(
    persistence, config, transport: LLMTransport, *,
    keys: list[str] | None = None, limit: int | None = None,
    do_content: bool = True, do_images: bool = True, force: bool = False,
) -> list[dict]:
    targets = iter_targets(persistence, keys=keys, limit=limit)
    return [
        prep_product(r, persistence, transport, config,
                     do_content=do_content, do_images=do_images, force=force)
        for r in targets
    ]
