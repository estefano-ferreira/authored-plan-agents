"""Real provider via a direct HTTPS call to Meta's Llama API (OpenAI-compatible REST surface).

No first-party "Llama API" SDK is declared in pyproject.toml, and the verified API surface
(`POST {base_url}/chat/completions`, `Authorization: Bearer <key>`, an OpenAI-style
`response_format` wrapper for structured output) is a plain REST endpoint -- so this client talks
to it directly over `httpx` (already a project dependency, used the same way by
`infrastructure/connectors/rest/rest_connector.py`) rather than pulling in a new SDK, or
repurposing the `openai` SDK's own strict, Pydantic-validated response models against a server
they were not built for. That choice also keeps response/usage parsing fully under this module's
control, which matters here: Meta's usage payload shape is not confirmed by the docs, so every
field below is read defensively (`dict.get`, never attribute access) -- see `complete`.
"""
import copy
import os
import time

import httpx

from core.ports import IModelClient, ModelRequest, ModelResponse

_DEFAULT_MODEL = "muse-spark-1.2"
_BASE_URL = "https://api.meta.ai/v1"


def _inject_strict_json_schema(node: object) -> None:
    """Recursively mutates `node` IN PLACE, injecting the two keys Meta's strict structured-output
    mode requires on every object node, where not already present: `additionalProperties: false`
    and a `required` array listing every key declared in `properties`. Only ever called on a
    private deep copy (see `_to_strict_schema`) -- never on the caller's own schema dict.
    """
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if isinstance(properties, dict):
        node.setdefault("additionalProperties", False)
        if "required" not in node:
            node["required"] = list(properties.keys())
        for value in properties.values():
            _inject_strict_json_schema(value)
    items = node.get("items")
    if isinstance(items, dict):
        _inject_strict_json_schema(items)
    elif isinstance(items, list):
        for item in items:
            _inject_strict_json_schema(item)
    for combinator in ("anyOf", "oneOf", "allOf"):
        branches = node.get(combinator)
        if isinstance(branches, list):
            for branch in branches:
                _inject_strict_json_schema(branch)
    defs = node.get("$defs")
    if not isinstance(defs, dict):
        defs = node.get("definitions")
    if isinstance(defs, dict):
        for value in defs.values():
            _inject_strict_json_schema(value)


def _to_strict_schema(schema: dict) -> dict:
    """Deep-copies `schema` (the caller's dict is never mutated) and injects strict-mode keys
    throughout the tree, returning the copy.

    This is the fourth provider dialect this study's codebase records for the same
    `ModelRequest.response_schema`: Gemini's two-field dialect (`response_mime_type` +
    `response_json_schema`, `gemini_client.py`), Anthropic's `output_config.format` (which assumes
    the caller-declared schema already satisfies its constraints, `anthropic_client.py`), OpenAI's
    single-shot `strict: true` wrapper (ditto, `openai_client.py`), and now Meta's own
    `strict: true` wrapper -- which, per the verified docs, actively MANDATES
    `additionalProperties: false` and a full `required` array on every object node, not only the
    top level. Recursing here is new relative to the OpenAI/Anthropic adapters precisely because
    Meta's server enforces the constraint recursively; the capability-declared top-level schema
    (see `ai/agents/correspondence/read_and_reply.py`) already satisfies it at the top level, so in
    the one schema this codebase actually ships, this function has no nested work left to do --
    it exists for schemas with nested objects, which strict mode would otherwise reject.
    """
    schema_copy = copy.deepcopy(schema)
    _inject_strict_json_schema(schema_copy)
    return schema_copy


class MetaModelClient(IModelClient):
    """Real Meta (Llama API) client, direct HTTPS via httpx -- see module docstring for why no SDK."""

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "META_API_KEY (or MODEL_API_KEY) is not set; refusing to run without measurement: "
                "set the environment variable to use MetaModelClient."
            )
        self._model = model or os.environ.get("META_MODEL", _DEFAULT_MODEL)
        self._client = httpx.Client(
            base_url=_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    def _post_with_rate_limit_retry(self, payload: dict, max_attempts: int = 10) -> dict:
        """Retries on HTTP 429, honoring the response's `Retry-After` header when present.

        Mirrors `GeminiModelClient._generate_with_rate_limit_retry`'s intent (a rate limit is
        pacing, not failure) but reads the standard HTTP header directly rather than
        regex-parsing an SDK exception message, since this client talks to the REST endpoint
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
                f"[meta] rate limited (429); retrying in {delay:.0f}s "
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

        # Defensive throughout: the endpoint is OpenAI-shaped but the exact usage payload is not
        # confirmed by the docs -- every field below falls back to 0/"" rather than raising.
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
