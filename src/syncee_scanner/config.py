"""Configuration loading and validation (spec §32, §5.5).

All tunables (gates, weights, thresholds, margin, selection, markets) come from YAML so
that scoring stays deterministic, version-controlled and testable. Baserow secrets come
from the environment (spec §16.1) and never from YAML.

``load_config`` merges, in increasing priority:
  1. ``config/default.yaml``
  2. ``config/scoring.yaml``
  3. an optional user ``--config`` file
  4. environment overrides for Baserow credentials

A deterministic :meth:`AppConfig.config_hash` fingerprints the scoring-relevant config
for run tracking (spec §37.9, §12).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from .observability.errors import ConfigurationError

CONFIG_DIR = Path("config")
DEFAULT_FILES = (CONFIG_DIR / "default.yaml", CONFIG_DIR / "scoring.yaml")


# --- Sub-models --------------------------------------------------------------------


class AuthConfig(BaseModel):
    """Auth-page recognition signals (spec §7). Calibrated after discovery (§8.4).

    ``probe_path`` is the authenticated-only page validate/login navigate to. The marker
    lists let you tune what "logged in" vs "login required" looks like without code changes.
    """

    probe_path: str = "/"
    settle_seconds: float = 3.0
    authenticated_markers: list[str] = Field(
        default_factory=lambda: ["Marketplace", "Add to import list", "My account", "Log out"]
    )
    login_markers: list[str] = Field(
        default_factory=lambda: ["Log in", "Sign in", "Password"]
    )
    login_url_fragments: list[str] = Field(
        default_factory=lambda: ["/login", "/signin", "/sign-in", "/auth"]
    )
    access_denied_fragments: list[str] = Field(
        default_factory=lambda: ["/403", "access-denied", "forbidden"]
    )


class SynceeConfig(BaseModel):
    category: str = "Home & Kitchen"
    base_url: str = "https://syncee.com"
    headless: bool = True
    browser_timeout_seconds: int = 60
    page_delay_seconds: float = 2
    detail_page_delay_seconds: float = 2
    request_jitter_seconds: float = 1.0
    max_retries: int = 3
    concurrency: int = 1
    storage_state_path: str = "data/auth/storage_state.json"
    auth: AuthConfig = Field(default_factory=AuthConfig)


class BaserowSettings(BaseModel):
    create_batch_size: int = 100
    update_batch_size: int = 100
    request_concurrency: int = 2
    max_retries: int = 3
    retry_backoff_seconds: float = 2


class SupplierGates(BaseModel):
    max_shipping_days: int = 10
    require_target_market: bool = True
    require_shipping_policy: bool = False
    require_return_policy: bool = False
    require_known_shipping: bool = False  # when data lacks shipping days, don't hard-fail
    # Supplier must dispatch from a country in markets.dispatch_allowed (EU-only).
    require_dispatch_in_allowed: bool = False
    minimum_data_completeness_pct: float = 60


class ProductGates(BaseModel):
    max_shipping_days: int = 10
    minimum_margin_pct: float = 45
    require_known_supplier_price: bool = True
    require_image: bool = True
    require_in_stock: bool = True
    # Content-based scope exclusions: title/description regexes for product types RB Home
    # does not stock — consumables/food (expiry, food-safety, reorder friction) and
    # refill/replacement-part-only listings (not standalone products). Matched case-insensitively.
    exclude_keywords: list[str] = []


class CurrencyConfig(BaseModel):
    """Convert supplier prices (any currency) to EUR before margin (spec §23)."""

    target: str = "EUR"
    auto_update: bool = True  # fetch daily ECB rates; cache to file, refetch when stale
    provider_url: str = "https://api.frankfurter.app/latest?base=EUR"
    cache_path: str = "data/fx_rates.json"
    max_age_hours: float = 24
    # EUR value of 1 unit — used only if the live fetch fails. Live rates override these.
    fallback_rates: dict[str, float] = Field(default_factory=dict)


class MarginConfig(BaseModel):
    minimum_margin_pct: float = 45
    target_margin_pct: float = 55
    estimated_payment_fee_pct: float = 3
    estimated_platform_fee_pct: float = 2
    expected_return_allowance_pct: float = 5
    # When shipping cost is unknown (Syncee list API), estimate it as a % of supplier price
    # so products can still be scored/ranked (flagged as estimated).
    estimate_shipping_when_unknown: bool = True
    assumed_shipping_pct_of_price: float = 15
    # How RB Home sets each product's retail price for margin evaluation:
    #   "target_margin" -> price = (cost+shipping) / (1 - fees - target_margin) so every
    #                      product hits target margin; rank by competitiveness vs Syncee RRP.
    #   "markup"        -> price = cost * markup_multiple.
    #   "rrp"           -> evaluate at Syncee's suggested retail price.
    pricing_mode: Literal["target_margin", "markup", "rrp"] = "rrp"
    rrp_discount_pct: float = 0  # sell this % below RRP (0 = exactly at market)
    markup_multiple: float = 3.0
    # Flag as uncompetitive when the target-margin price exceeds this multiple of Syncee's RRP.
    uncompetitive_over_rrp: float = 1.3


class WeightedScoring(BaseModel):
    """Scoring thresholds + weights; weights must sum to 100 (spec §20.5, §24.2)."""

    version: str
    reject_below: float
    manual_review_from: float
    weights: dict[str, float]
    # product uses shortlist_from, supplier uses approve_from — accept either.
    approve_from: float | None = None
    shortlist_from: float | None = None

    @model_validator(mode="after")
    def _weights_sum_to_100(self) -> WeightedScoring:
        total = round(sum(self.weights.values()), 6)
        if total != 100:
            raise ValueError(
                f"scoring weights for '{self.version}' sum to {total}, expected 100"
            )
        return self


class IncrementalScan(BaseModel):
    stop_after_known_products: int = 200
    stop_after_known_pages: int = 3


class LLMConfig(BaseModel):
    """Optional LLM fallback config (§25.4, §31).

    Access is only ever via OpenRouter or a subscription CLI — never a direct provider API.
    ``provider`` selects the transport; ``model`` is an OpenRouter-style ``provider/model``
    id (or the CLI's own model identifier).
    """

    enabled: bool = False
    provider: Literal["openrouter", "cli"] = "openrouter"
    model: str = "anthropic/claude-sonnet-5"
    base_url: str = "https://openrouter.ai/api/v1"
    cli_command: str | None = None  # for provider="cli", e.g. "llm" or a wrapper script
    prompt_version: str = "v1"


class ClassificationConfig(BaseModel):
    minimum_confidence: float = 0.70
    llm: LLMConfig = Field(default_factory=LLMConfig)
    # Map a scanned Syncee category id (as string) to a human label, and a subcategory label
    # to an RB Home collection. When a product was scanned under a known subcategory, this
    # gives a reliable collection instead of keyword-guessing.
    category_labels: dict[str, str] = Field(default_factory=dict)
    category_collection_map: dict[str, str] = Field(default_factory=dict)


class SelectionConfig(BaseModel):
    initial_total_min: int = 18
    initial_total_max: int = 40  # 4 collections × ~10 (Kitchen, Dining, Home Comfort, Bathroom)
    target_per_collection_min: int = 6
    target_per_collection_max: int = 10
    max_supplier_share_pct: float = 30
    new_arrivals_batch_size: int = 4
    # Keep the initial assortment in one affordable price band (retail EUR). Products whose
    # target-margin retail exceeds this are excluded from selection (add premium later).
    max_retail_price: float | None = None
    min_retail_price: float | None = None


class PersistenceConfig(BaseModel):
    batch_size: int = 100
    checkpoint_every_products: int = 250


class SafetyConfig(BaseModel):
    max_pages: int = 5000


class MarketsConfig(BaseModel):
    target: list[str] = Field(default_factory=list)
    # ISO-3166 alpha-2 codes for the target markets, used to match per-country shipping zones
    # (Syncee's product SHIPPING array keys locations by code).
    target_codes: list[str] = Field(default_factory=list)
    # Allowed supplier dispatch/warehouse countries — EU-origin avoids import VAT/customs and
    # ships faster within Europe. Used by the supplier dispatch gate.
    dispatch_allowed: list[str] = Field(default_factory=list)


class BaserowCredentials(BaseModel):
    """Secrets + table IDs from the environment (spec §16.1). Never from YAML."""

    api_url: str = "https://api.baserow.io"
    database_token: str | None = None
    suppliers_table_id: str | None = None
    products_table_id: str | None = None
    scan_runs_table_id: str | None = None
    product_changes_table_id: str | None = None
    manual_decisions_table_id: str | None = None
    selection_batches_table_id: str | None = None
    # Optional — only for the table-creation setup helper.
    user_email: str | None = None
    user_password: str | None = None
    workspace_id: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> BaserowCredentials:
        env = env or dict(os.environ)
        return cls(
            api_url=env.get("BASEROW_API_URL", "https://api.baserow.io"),
            database_token=env.get("BASEROW_DATABASE_TOKEN") or None,
            suppliers_table_id=env.get("BASEROW_SUPPLIERS_TABLE_ID") or None,
            products_table_id=env.get("BASEROW_PRODUCTS_TABLE_ID") or None,
            scan_runs_table_id=env.get("BASEROW_SCAN_RUNS_TABLE_ID") or None,
            product_changes_table_id=env.get("BASEROW_PRODUCT_CHANGES_TABLE_ID") or None,
            manual_decisions_table_id=env.get("BASEROW_MANUAL_DECISIONS_TABLE_ID") or None,
            selection_batches_table_id=env.get("BASEROW_SELECTION_BATCHES_TABLE_ID") or None,
            user_email=env.get("BASEROW_USER_EMAIL") or None,
            user_password=env.get("BASEROW_USER_PASSWORD") or None,
            workspace_id=env.get("BASEROW_WORKSPACE_ID") or None,
        )

    def require_tables(self) -> None:
        """Raise if credentials/table IDs needed for a scan are missing."""
        missing = [
            name
            for name, value in {
                "BASEROW_DATABASE_TOKEN": self.database_token,
                "BASEROW_SUPPLIERS_TABLE_ID": self.suppliers_table_id,
                "BASEROW_PRODUCTS_TABLE_ID": self.products_table_id,
                "BASEROW_SCAN_RUNS_TABLE_ID": self.scan_runs_table_id,
                "BASEROW_PRODUCT_CHANGES_TABLE_ID": self.product_changes_table_id,
                "BASEROW_MANUAL_DECISIONS_TABLE_ID": self.manual_decisions_table_id,
                "BASEROW_SELECTION_BATCHES_TABLE_ID": self.selection_batches_table_id,
            }.items()
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required Baserow environment variables: " + ", ".join(missing)
            )


# --- Publish-prep (approved → Shopify-ready) ---------------------------------------


class SeoConfig(BaseModel):
    """SEO/content generation for approved products (publish-prep phase).

    LLM access is ONLY via OpenRouter (``OPENROUTER_API_KEY`` + ``base_url``) or a subscription
    CLI (``cli_command``) — NEVER a direct provider API. Outputs are cached by
    (model, prompt_version, input fingerprint) so re-runs are deterministic and cheap.
    """

    enabled: bool = True
    provider: str = "openrouter"  # "openrouter" | "cli"
    model: str = "anthropic/claude-sonnet-5"
    base_url: str = "https://openrouter.ai/api/v1"
    cli_command: str | None = None
    prompt_version: str = "seo-v1"
    temperature: float = 0.4
    brand_voice: str = (
        "RB Home — warm, practical, unfussy. Speak to everyday home & kitchen problems and the "
        "small daily wins the product delivers. Concrete and benefit-led, never hypey."
    )
    max_title_len: int = 60
    max_meta_len: int = 155
    max_tags: int = 8


class ImageTransformConfig(BaseModel):
    """AI image enhancement — background cleanup, upscale, consistent framing.

    Runs on a dedicated IMAGE model via OpenRouter (or a subscription CLI) — NOT Sonnet
    (text-only; Sonnet writes SEO copy) and NEVER a direct provider API.

    GUARDRAIL: transforms must not alter the product's real appearance — that would misrepresent
    what the customer receives. Keep scope to background/crop/framing/upscale; the Gallery QA
    gate is where any product-altering result is caught and rejected before it reaches the store.
    """

    enabled: bool = True
    provider: str = "openrouter"  # "openrouter" | "cli"
    # Nano Banana Pro (Gemini 3 Pro Image): keeps products intact, removes props + overlaid
    # promo text, restages on a calm backdrop. Chosen over 2.5-flash + deterministic cutout,
    # which cropped away parts of products (user, 2026-07-21).
    model: str = "google/gemini-3-pro-image"
    base_url: str = "https://openrouter.ai/api/v1"
    cli_command: str | None = None
    prompt_version: str = "img-v1"
    operations: list[str] = Field(
        default_factory=lambda: ["background_cleanup", "upscale", "square_framing"]
    )


class CutoutConfig(BaseModel):
    """Deterministic background removal (rembg) → product on pure white + soft shadow.

    Faithful by construction — it only isolates the product and composites it; it never adds,
    removes, or restyles anything. Best for clean studio shots; lifestyle shots route to the
    generative path instead (see ImageConfig.method / lifestyle_border_std).
    """

    enabled: bool = True
    model: str = "isnet-general-use"  # rembg model; cached locally after first download
    margin_pct: float = 0.10  # margin around the product on the square canvas
    # Background: "auto_tint" picks a calm pastel from `pastel_palette` that harmonises with
    # the product, over a gentle radial light gradient; "white"/"fixed" uses `background`.
    background_mode: str = "auto_tint"
    # Curated calm pastels (oat, sage, blush, powder blue, clay, lavender, soft mint). The
    # backdrop is chosen from these by product hue — never a muddy grey average.
    pastel_palette: list[str] = Field(
        default_factory=lambda: [
            "#EAE3D6", "#DCE3D3", "#F0E2DF", "#DBE3E9", "#E9DBD0", "#E3DDE8", "#D8E5DF",
        ]
    )
    gradient_strength: float = 0.12  # radial light falloff — depth, not flat
    background: str = "#F2F0ED"  # fallback when background_mode != "auto_tint"
    # Directional contact shadow (light from upper-left → shadow to lower-right) for depth.
    shadow: bool = True
    shadow_opacity: float = 0.28
    shadow_blur_pct: float = 0.025
    shadow_offset_pct: float = 0.02


class ImageConfig(BaseModel):
    """Publish-ready image pipeline. Hybrid: cutout for clean shots, generative for lifestyle."""

    # "generative" (all-LLM, default) uses the image model for every product — the deterministic
    # rembg cutout cropped parts off some products, so it's disabled. "hybrid"/"cutout" re-enable
    # it if ever wanted.
    method: str = "generative"
    # Source border-colour std above which a shot is treated as lifestyle (→ generative).
    lifestyle_border_std: float = 35.0
    cutout: CutoutConfig = Field(default_factory=CutoutConfig)
    transform: ImageTransformConfig = Field(default_factory=ImageTransformConfig)
    # Deterministic finishing step (Pillow) — applied after any AI transform for an exact,
    # consistent output regardless of what the model returns.
    target_size: int = 2048  # square canvas edge (px)
    format: str = "JPEG"
    quality: int = 85
    # "auto" samples the real background colour from the image edges so square-padding is
    # seamless (no white bands round a grey photo); "fixed" always uses pad_color.
    pad_mode: str = "auto"
    pad_color: str = "#FFFFFF"
    # Flag (never silently upscale) source images smaller than this — quality can't be invented.
    min_source_px: int = 800
    max_images_per_product: int = 6


class ShopifyConfig(BaseModel):
    """Shopify Admin API polish target. Token/domain come from env, never YAML."""

    store_domain: str | None = None  # e.g. "rb-home.myshopify.com"
    api_version: str = "2026-07"  # verified live against the store
    match_by: str = "sku"  # how to map a Baserow product row → the Syncee-imported Shopify product


class PublishingConfig(BaseModel):
    seo: SeoConfig = Field(default_factory=SeoConfig)
    images: ImageConfig = Field(default_factory=ImageConfig)
    shopify: ShopifyConfig = Field(default_factory=ShopifyConfig)


# --- Root model --------------------------------------------------------------------


class AppConfig(BaseModel):
    scanner_version: str = "0.1.0"
    syncee: SynceeConfig = Field(default_factory=SynceeConfig)
    baserow: BaserowSettings = Field(default_factory=BaserowSettings)
    markets: MarketsConfig = Field(default_factory=MarketsConfig)
    supplier_gates: SupplierGates = Field(default_factory=SupplierGates)
    product_gates: ProductGates = Field(default_factory=ProductGates)
    currency: CurrencyConfig = Field(default_factory=CurrencyConfig)
    margin: MarginConfig = Field(default_factory=MarginConfig)
    supplier_scoring: WeightedScoring
    product_scoring: WeightedScoring
    incremental_scan: IncrementalScan = Field(default_factory=IncrementalScan)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)

    # Not part of the hash; secrets loaded from env at runtime.
    baserow_credentials: BaserowCredentials = Field(default_factory=BaserowCredentials)

    def config_hash(self) -> str:
        """Deterministic fingerprint of scoring-relevant config (spec §37.9)."""
        payload = self.model_dump(exclude={"baserow_credentials", "scanner_version"})
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# --- Loading -----------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config file {path} must be a mapping")
    return data


def load_config(
    user_config: Path | str | None = None,
    *,
    default_files: tuple[Path, ...] = DEFAULT_FILES,
    env: dict[str, str] | None = None,
) -> AppConfig:
    """Load, merge and validate configuration.

    Raises:
        ConfigurationError: on missing files, invalid YAML, or schema/weight errors.
    """
    merged: dict[str, Any] = {}
    for path in default_files:
        merged = _deep_merge(merged, _read_yaml(Path(path)))
    if user_config:
        merged = _deep_merge(merged, _read_yaml(Path(user_config)))

    try:
        config = AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc

    config.baserow_credentials = BaserowCredentials.from_env(env)
    # Keep the Baserow tuning block's api_url in sync if env provided one.
    return config
