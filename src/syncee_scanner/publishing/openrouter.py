"""OpenRouter transport for publish-prep LLM calls — SEO text + AI image edit.

Repo constraint (load-bearing): LLM access is ALWAYS via OpenRouter (``OPENROUTER_API_KEY`` +
``base_url``) or a subscription CLI — NEVER a direct provider API. This is the only live-only
module in :mod:`publishing`; every orchestrator accepts it by injection so tests run offline
with a fake (mirrors the extraction transport seam).

Verified response shapes:
  * chat  → ``choices[0].message.content`` (str)
  * image → ``choices[0].message.images[0].image_url.url`` (``data:image/png;base64,…``)
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..observability.errors import ErrorCode, ScannerError


class LLMTransport(Protocol):
    """The surface the SEO/image modules depend on (real = OpenRouter, test = fake)."""

    def chat(self, model: str, messages: list[dict], *, temperature: float = 0.4,
             max_tokens: int = 1200, json_mode: bool = False) -> str: ...

    def edit_image(self, model: str, prompt: str, image_bytes: bytes, *,
                   mime: str = "image/png") -> bytes: ...


@dataclass
class OpenRouterClient:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout: float = 180.0

    @classmethod
    def from_env(cls, base_url: str, *, env: dict[str, str] | None = None) -> OpenRouterClient:
        env = env or dict(os.environ)
        key = env.get("OPENROUTER_API_KEY")
        if not key:
            raise ScannerError(
                ErrorCode.CONFIGURATION_ERROR,
                "OPENROUTER_API_KEY is not set — publish-prep LLM calls require OpenRouter.",
            )
        return cls(api_key=key, base_url=base_url.rstrip("/"))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, body: dict) -> dict:
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(), json=body, timeout=self.timeout,
            )
        except httpx.HTTPError as exc:  # network failure — retryable
            raise ScannerError(
                ErrorCode.LLM_API_ERROR, f"OpenRouter request failed: {exc}"
            ) from exc
        if r.status_code != 200:
            raise ScannerError(
                ErrorCode.LLM_API_ERROR,
                f"OpenRouter {body.get('model')} returned {r.status_code}: {r.text[:200]}",
            )
        return r.json()

    def chat(self, model: str, messages: list[dict], *, temperature: float = 0.4,
             max_tokens: int = 1200, json_mode: bool = False) -> str:
        body: dict = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        msg = self._post(body)["choices"][0]["message"]
        return msg.get("content") or ""

    def edit_image(self, model: str, prompt: str, image_bytes: bytes, *,
                   mime: str = "image/png") -> bytes:
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        body = {
            "model": model,
            "modalities": ["image", "text"],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        }
        msg = self._post(body)["choices"][0]["message"]
        images = msg.get("images") or []
        if not images:
            raise ScannerError(
                ErrorCode.LLM_API_ERROR,
                f"{model} returned no image (refusal={msg.get('refusal')!r}).",
            )
        url = images[0].get("image_url", {}).get("url", "")
        if "," not in url:
            raise ScannerError(ErrorCode.LLM_API_ERROR, "Malformed image data URL from OpenRouter.")
        return base64.b64decode(url.split(",", 1)[1])
