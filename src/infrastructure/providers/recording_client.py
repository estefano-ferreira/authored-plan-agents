"""Measurement instrumentation decorator: wraps any IModelClient and records each call.

Used by the runner for post-hoc analysis of the output contract (selection/generation)
without touching the `ai` layer — the orchestrator/runtime keep calling `model.complete(...)`
exactly as before; this decorator is transparent to them and only appends to `self.log`.
"""
from core.ports import IModelClient, ModelRequest, ModelResponse


class RecordingModelClient(IModelClient):
    """Decorates an IModelClient, delegating `complete()` and logging each call/response pair."""

    def __init__(self, delegate: IModelClient) -> None:
        self._delegate = delegate
        self.log: list[dict] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._delegate.complete(request)

        prompt = "\n".join(str(m.get("content", "")) for m in request.messages)
        self.log.append({
            "purpose": request.purpose,
            "prompt": prompt,
            "content": response.content,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cache_read_tokens": response.cache_read_tokens,
        })

        return response
