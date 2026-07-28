"""Real provider via the google-genai SDK. Lazy import: only requires the lib when instantiated."""
import os

from core.ports import IModelClient, ModelRequest, ModelResponse

_DEFAULT_MODEL = "gemini-3.1-flash-lite"  # the study's measured model — a fresh clone reproduces its condition


class GeminiModelClient(IModelClient):
    """Real Gemini API client, with token usage (incl. cache) reported by the API."""

    def __init__(self) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
            raise RuntimeError("'google-genai' SDK not installed.") from exc

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set; refusing to run without measurement: "
                "set the environment variable to use GeminiModelClient."
            )
        self._model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        self._types = types
        self._client = genai.Client(api_key=api_key)

    def _generate_with_rate_limit_retry(self, contents, config, max_attempts: int = 10):
        """Retries on 429 RESOURCE_EXHAUSTED honoring the server's retryDelay hint.

        Required on the free tier (e.g. 5 requests/min for gemini-3.6-flash); any other
        API error propagates untouched — a rate limit is pacing, not failure.
        """
        import re as _re
        import time as _time

        for attempt in range(max_attempts):
            try:
                return self._client.models.generate_content(
                    model=self._model, contents=contents, config=config,
                )
            except Exception as exc:
                message = str(exc)
                if "RESOURCE_EXHAUSTED" not in message and " 429" not in message:
                    raise
                if attempt == max_attempts - 1:
                    raise
                hint = _re.search(r"retry in (\d+(?:\.\d+)?)s", message, _re.IGNORECASE)
                delay = float(hint.group(1)) + 1.0 if hint else 15.0 * (attempt + 1)
                print(f"[gemini] rate limited (429); retrying in {delay:.0f}s "
                      f"(attempt {attempt + 1}/{max_attempts})")
                _time.sleep(min(delay, 70.0))
        raise RuntimeError("unreachable")

    def complete(self, request: ModelRequest) -> ModelResponse:
        types = self._types
        system_instruction = None
        contents = []
        for message in request.messages:
            role = message["role"]
            text = message["content"]
            if role == "system":
                system_instruction = text
                continue
            sdk_role = "model" if role == "assistant" else "user"
            contents.append(types.Content(role=sdk_role, parts=[types.Part.from_text(text=text)]))

        config_kwargs = dict(
            system_instruction=system_instruction,
            max_output_tokens=request.max_tokens,
        )
        if request.response_schema is not None:
            # Decoding-level structured output. NOTE the field choice: `response_schema` takes
            # Gemini's OpenAPI-subset dialect and rejects standard JSON Schema keys such as
            # `additionalProperties` (observed live: 400 INVALID_ARGUMENT); `response_json_schema`
            # accepts real JSON Schema verbatim. Schema portability across providers is a
            # dialect-translation concern that belongs here, in the adapter.
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = request.response_schema
        config = types.GenerateContentConfig(**config_kwargs)

        response = self._generate_with_rate_limit_retry(contents, config)

        # Defensive fallback only for a missing/empty text field — API errors still raise.
        content = response.text
        if content is None:
            content = ""

        usage = response.usage_metadata
        input_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage is not None else 0
        # Gemini 3.x flash models are thinking models: thought tokens are billed as output
        # but reported separately in thoughts_token_count, NOT in candidates_token_count.
        output_tokens = (
            ((getattr(usage, "candidates_token_count", 0) or 0)
             + (getattr(usage, "thoughts_token_count", 0) or 0))
            if usage is not None else 0
        )
        cache_read_tokens = (getattr(usage, "cached_content_token_count", None) or 0) if usage is not None else 0

        return ModelResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            model=self._model,
        )
