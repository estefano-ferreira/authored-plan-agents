"""Real provider via the Anthropic SDK. Lazy import: only requires the lib when instantiated."""
import os

from core.ports import IModelClient, ModelRequest, ModelResponse

_DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicModelClient(IModelClient):
    """Real Anthropic API client, with token usage (incl. cache) reported by the API."""

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
            raise RuntimeError("'anthropic' SDK not installed.") from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not configured: set the environment variable to use AnthropicModelClient."
            )
        self._model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, request: ModelRequest) -> ModelResponse:
        kwargs = dict(
            model=self._model,
            max_tokens=request.max_tokens,
            messages=[{"role": m["role"], "content": m["content"]} for m in request.messages],
        )
        if request.response_schema is not None:
            # Decoding-level structured output; the schema is expected to already carry
            # additionalProperties: false and required (enforced by the calling capability).
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": request.response_schema},
            }
        response = self._client.messages.create(**kwargs)

        content = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        return ModelResponse(
            content=content,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            model=self._model,
        )
