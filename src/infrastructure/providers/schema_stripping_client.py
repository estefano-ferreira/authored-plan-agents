"""Measurement instrumentation decorator: strips `response_schema` from the REQUEST before
delegating, emulating an "enforcement=none" provider configuration without touching the
capability itself. The capability that composed the request (e.g.
`ai/agents/correspondence/read_and_reply.py::interpret_and_draft`) still declares its
`_RESPONSE_SCHEMA` on every `ModelRequest` it builds -- unchanged, since `ai/` is out of scope for
this instrumentation; only the request the underlying provider actually *receives* is edited.

Why this belongs at the request boundary, not the provider: `ModelRequest.response_schema`
constrains generation at the decoding level (native structured output, see `core/ports.py`'s
docstring and NOTES.md "Structured output correction") -- when present, the provider MUST
restrict output to the schema. Dropping it here reproduces, deterministically and against the
free/local provider, exactly the "capability declares a schema but the deployed configuration
never wires structured-output enforcement" condition: the model falls back to whatever its
default (typically prompt-only) decoding behavior is, without editing a single line of `ai/`.

Used by the integrity matrix (scripts/run_plans.py --matrix-cell) to build the "enforcement=none"
cells (A and B) on demand. Measurement-only tooling: NEVER wire this into a production
build_platform() call (see `configurations.py::build_platform`'s `strip_response_schema`
parameter, which exists solely for `scripts/run_plans.py --matrix-cell`).
"""
import dataclasses

from core.ports import IModelClient, ModelRequest, ModelResponse


class SchemaStrippingClient(IModelClient):
    """Decorates an IModelClient: intercepts the REQUEST and delegates `complete()` with
    `response_schema=None` (via `dataclasses.replace`, since `ModelRequest` is frozen), whenever
    the caller had set one. Requests that already carry no schema (e.g. `purpose=="selection"`
    calls, which never set one) pass through unmodified. The response is never touched here --
    only the outgoing request is edited.

    Measurement instrumentation only -- see module docstring. Never use in production.
    """

    def __init__(self, delegate: IModelClient) -> None:
        self._delegate = delegate

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.response_schema is not None:
            request = dataclasses.replace(request, response_schema=None)
        return self._delegate.complete(request)
