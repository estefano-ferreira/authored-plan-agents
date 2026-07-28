"""Real provider via the OpenAI SDK. Lazy import: only requires the lib when instantiated."""
import os

from core.ports import IModelClient, ModelRequest, ModelResponse

_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIModelClient(IModelClient):
    """Real OpenAI API client, with token usage (incl. cache when available) reported by the API."""

    def __init__(self) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
            raise RuntimeError("'openai' SDK not installed.") from exc

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not configured: set the environment variable to use OpenAIModelClient."
            )
        self._model = os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)
        self._client = openai.OpenAI(api_key=api_key)

    def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs = dict(
            model=self._model,
            max_tokens=request.max_tokens,
            messages=[{"role": m["role"], "content": m["content"]} for m in request.messages],
        )
        if request.response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": request.response_schema},
            }
        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        usage = response.usage
        cached_tokens = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", 0) or 0

        return ModelResponse(
            content=content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cache_read_tokens=cached_tokens,
            model=self._model,
        )
