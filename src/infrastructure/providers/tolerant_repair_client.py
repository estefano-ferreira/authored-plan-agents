"""Measurement instrumentation decorator: reproduces, on purpose and deterministically, the
tolerant fallback parser that `ai/agents/correspondence/read_and_reply.py` used to have before the
strict output-contract guard (`_parse_strict` + `retry_once_then_fail`) replaced it -- see that
module's docstring and NOTES.md ("Structured output correction") for the removal and its
motivation.

That removed fallback, byte-for-byte, built `{"request_type": "support", "summary":
content[:200], "reply_body": content}` out of whatever raw text the model returned, whenever the
content did not parse as a bare JSON object -- silently absorbing anything (markdown-fenced JSON,
plain prose, truncated output) into a "valid enough" dict, which the ERP would then persist as-is
(see NOTES.md's "Real-model findings": 100% of real-model generations came back valid-JSON-in-
markdown-fences, silently absorbed into truncated ERP `summary` rows).

This decorator wraps any `IModelClient` and, for `purpose == "generation"` calls only,
re-implements exactly that substitution at the RESPONSE boundary. It does NOT touch the strict
parser in `ai/` (which still runs, unmodified, downstream, on whatever content it receives) -- it
just hands that parser a replacement `content` string that its `json.loads`-only check will
always accept (all three of the strict guard's required keys are present by construction). The
result is that the strict guard "sees" a conforming response and lets the tolerant fallback's
garbage ride through it exactly as it did before the guard existed -- this is the whole point:
proving that the guard, on its own, cannot tell a byte-faithful replica of the historical fallback
from a genuinely well-formed generation.

Used by the integrity matrix (scripts/run_plans.py --matrix-cell) to build the "guard would have
accepted the old fallback's garbage" cells (A and C) on demand. Measurement-only tooling: NEVER
wire this into a production build_platform() call (see `configurations.py::build_platform`'s
`tolerant_repair` parameter, which exists solely for `scripts/run_plans.py --matrix-cell`).
"""
import json

from core.ports import IModelClient, ModelRequest, ModelResponse


def _parses_as_json_object(content: str) -> bool:
    """True only if `content` is valid JSON that decodes to a `dict` -- the same "raw JSON
    object" test the strict guard (`read_and_reply.py::_parse_strict`) starts with, used here to
    decide whether a repair is even necessary (already-conforming content is left untouched, so
    `self.repairs` only counts responses this decorator actually rewrote)."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict)


class TolerantRepairClient(IModelClient):
    """Decorates an IModelClient: delegates `complete()`, then for `purpose == "generation"`
    responses whose `content` does not parse as a raw JSON object, replaces `content` with the
    historical tolerant-fallback shape -- `json.dumps({"request_type": "support", "summary":
    content[:200], "reply_body": content})`, where `content` is the original (unrepaired)
    response text, fences and all. `purpose == "selection"` responses, and any generation
    response that already parses as a JSON object (nothing to repair), pass through unchanged.

    Every substitution is recorded in `self.repairs` (a list of `{"original_preview":
    content[:120]}`, one entry per repair) so callers -- the integrity-matrix runner -- can count
    how many responses this decorator actually touched, per execution.

    Measurement instrumentation only -- see module docstring. Never use in production.
    """

    def __init__(self, delegate: IModelClient) -> None:
        self._delegate = delegate
        self.repairs: list[dict] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._delegate.complete(request)
        if request.purpose != "generation":
            return response

        content = response.content
        if _parses_as_json_object(content):
            return response

        self.repairs.append({"original_preview": content[:120]})
        repaired_content = json.dumps(
            {"request_type": "support", "summary": content[:200], "reply_body": content},
            ensure_ascii=False,
        )
        return ModelResponse(
            content=repaired_content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            model=response.model,
        )
