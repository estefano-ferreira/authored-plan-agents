"""Measurement instrumentation decorator: reproduces, on purpose and deterministically, the class
of output-contract violation that the original "guard-only" cell of the integrity matrix hit BY
ACCIDENT (see NOTES.md, "Structured output correction" -- the Gemini `response_schema` dialect bug
that rejected `additionalProperties` and made every inbound generation call fail visibly).

That accident's *symptom*, from the model's/parser's point of view, was not "no output" -- it was
"the provider still returned otherwise-valid content, but not as the bare JSON object the strict
decode-level parser (`ai/agents/correspondence/read_and_reply.py::_parse_strict`) requires." This
decorator reproduces exactly that symptom, synthetically: it wraps any `IModelClient` and, for
`purpose == "generation"` calls only, re-fences already-valid JSON content in markdown code fences
before returning it -- the single most common shape of "valid JSON, wrong envelope" failure
observed against real providers. `purpose == "selection"` responses pass through untouched, since
the cell under study is specifically the generation contract guarded by `retry_once_then_fail`.

This makes the "only the architectural guard, no working structured-output enforcement" cell of the
matrix reproducible on demand, against the free/deterministic local provider, instead of waiting for
another provider-side dialect surprise. It is measurement-only tooling for that purpose: NEVER wire
this into a production build_platform() call (see `configurations.py::build_platform`'s
`inject_generation_fault` parameter, which exists solely for `scripts/run_plans.py
--inject-generation-fault`).
"""
from core.ports import IModelClient, ModelRequest, ModelResponse


class GenerationFaultInjectionClient(IModelClient):
    """Decorates an IModelClient: delegates `complete()`, then corrupts `purpose=="generation"`
    responses by wrapping their `content` in markdown JSON fences (```json ... ```) -- valid JSON,
    invalid envelope for the strict `json.loads`-only parser downstream. `purpose=="selection"`
    responses, and any response whose `content` isn't reachable/replaceable, pass through unchanged.

    Measurement instrumentation only -- see module docstring. Never use in production.
    """

    def __init__(self, delegate: IModelClient) -> None:
        self._delegate = delegate

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._delegate.complete(request)
        if request.purpose != "generation":
            return response

        corrupted_content = f"```json\n{response.content}\n```"
        return ModelResponse(
            content=corrupted_content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            model=response.model,
        )
