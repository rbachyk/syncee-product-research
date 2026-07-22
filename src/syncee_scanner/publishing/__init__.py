"""Publish-prep phase: approved products → Shopify-ready records (spec §26.6 follow-on).

Deterministic normalization + OpenRouter-backed SEO copy + AI image transform, all landing
in Baserow for a manual Gallery QA gate before anything is pushed to the store. Nothing here
publishes automatically; the Shopify push is a separate, explicitly-confirmed step.
"""
