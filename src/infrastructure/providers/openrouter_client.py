"""Real provider via a direct HTTPS call to OpenRouter (OpenAI-compatible REST surface).

OpenRouter (`https://openrouter.ai/api/v1`) fronts many underlying model families -- Llama,
Qwen, Claude, and others -- behind a single API key and a single OpenAI-shaped
`POST /chat/completions` endpoint. That is the entire reason this adapter exists: it is the one
provider in this codebase whose `model` selects which *family* answers the call, which is exactly
what cross-model arms of an experiment need. Structured output for the model actually chosen is
declared by wrapping `response_schema` in the OpenAI-style `response_format` `json_schema`
envelope with `strict: true`; whether the underlying model *honors* that envelope varies by model
and is not something this adapter can verify in advance -- it sends the wrapper and lets an
unsupported model fail loudly (a non-2xx from OpenRouter, propagated by `raise_for_status()`)
rather than silently degrading to unconstrained output.

No first-party OpenRouter SDK is declared in pyproject.toml, and (mirroring
`infrastructure/providers/meta_client.py`'s own reasoning) the verified API surface is a plain
REST endpoint -- so this client talks to it directly over `httpx` (already a project dependency)
rather than pulling in a new SDK, or repurposing the `openai` SDK's own strict, Pydantic-validated
response models against a server they were not built for. Response/usage parsing stays fully
under this module's control: OpenRouter's usage payload is OpenAI-shaped
(`usage.prompt_tokens`/`usage.completion_tokens`), but every field below is still read
defensively (`dict.get`, never attribute access) -- see `complete`.
"""
import os
import time

import httpx

from core.ports import IModelClient, ModelRequest, ModelResponse
from infrastructure.providers.meta_client import _to_strict_schema

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterModelClient(IModelClient):
    """Real OpenRouter client, direct HTTPS via httpx -- see module docstring for why no SDK.

    Reuses `meta_client._to_strict_schema` rather than duplicating it: both providers speak the
    same OpenAI-derived `response_format` strict-JSON-schema dialect (single-shot `strict: true`
    wrapper, recursive `additionalProperties: false` + full `required` array on every object
    node), so this is the same dialect family, not a coincidence -- see that function's own
    docstring for the full provider-dialect inventory this codebase tracks.
    """

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set; refusing to run without measurement: "
                "set the environment variable to use OpenRouterModelClient."
            )
        self._model = model or os.environ.get("OPENROUTER_MODEL")
        if not self._model:
            raise RuntimeError(
                "no OpenRouter model was given (constructor arg or env OPENROUTER_MODEL): "
                "unlike the other providers, OpenRouterModelClient has no default model -- which "
                "underlying family (Llama, Qwen, Claude, ...) answers the call is itself an "
                "experimental variable this repository pre-registers per experiment, so it must "
                "be set explicitly (OPENROUTER_MODEL=<namespace/model>, e.g. "
                "'meta-llama/llama-3.1-70b-instruct' or 'qwen/qwen-2.5-72b-instruct'), never "
                "defaulted."
            )
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    def _post_with_rate_limit_retry(self, payload: dict, max_attempts: int = 10) -> dict:
        """Retries on HTTP 429, honoring the response's `Retry-After` header when present.

        Same shape as `MetaModelClient._post_with_rate_limit_retry` (see that method's docstring):
        a rate limit is pacing, not failure, and the standard HTTP header is read directly rather
        than regex-parsing an SDK exception message, since this client talks to the REST endpoint
        itself instead of going through an SDK. Any other HTTP error status propagates untouched
        via `raise_for_status()`.
        """
        for attempt in range(max_attempts):
            response = self._client.post("/chat/completions", json=payload)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt == max_attempts - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) + 1.0 if retry_after else 15.0 * (attempt + 1)
            except ValueError:
                delay = 15.0 * (attempt + 1)
            print(
                f"[openrouter] rate limited (429); retrying in {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_attempts})"
            )
            time.sleep(min(delay, 70.0))
        raise RuntimeError("unreachable")

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = dict(
            model=self._model,
            max_tokens=request.max_tokens,
            messages=[{"role": m["role"], "content": m["content"]} for m in request.messages],
        )
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": _to_strict_schema(request.response_schema),
                    "strict": True,
                },
            }

        data = self._post_with_rate_limit_retry(payload)

        # Defensive throughout: the endpoint is OpenAI-shaped and OpenRouter documents the usage
        # fields used below, but which underlying model answered can still vary the exact payload
        # -- every field falls back to 0/"" rather than raising. Mirrors MetaModelClient.complete.
        choices = data.get("choices") or [{}]
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content") or ""

        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", 0) or 0
        output_tokens = usage.get("completion_tokens", 0) or 0
        cache_read_tokens = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cache_read_tokens = details.get("cached_tokens", 0) or 0

        return ModelResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            model=self._model,
        )
