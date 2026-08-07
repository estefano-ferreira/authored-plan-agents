"""Measurement instrumentation decorator: reproduces, on purpose and deterministically, the
practitioner "code fence removal" repair strategy -- the extraction-based repair the v0.5.0
external review (M1) pointed out this study had never measured, having only measured the
*absorbing* repairer (`TolerantRepairClient`, which stuffs the raw response into the contract's
fields regardless of shape). See `docs/preregistration-extractor-repair-cells.md` (cells G and H),
committed before this module existed, for the full determination and the predictions it fixed in
advance.

This decorator wraps any `IModelClient` and, for `purpose == "generation"` calls only, does exactly
one thing and nothing else: if `content` matches a single markdown fence envelope (optional
language tag) -- `^\\s*```[a-zA-Z]*\\n(.*)\\n```\\s*$`, `re.DOTALL`, one pass, no recursion -- it
replaces `content` with the captured inner text. Anything that does not match that single-fence
shape (bare JSON, prose, a double-fenced envelope where only the outer layer matches) passes
through unchanged by the match, though a double-fenced envelope IS matched once at the outer layer
and returns still-fenced inner content -- there is no second pass to notice or strip that. No JSON
parsing, no absorption, no field-stuffing: this is deliberately narrower than `TolerantRepairClient`
and only ever removes one markdown fence layer, exactly the practitioner strategy it mirrors and
nothing beyond it.

Used by the integrity matrix (scripts/run_plans.py --matrix-cell) to build cells G and H (see the
pre-registration above). Measurement-only tooling: NEVER wire this into a production
build_platform() call (see `configurations.py::build_platform`'s `extractor_repair` parameter,
which exists solely for `scripts/run_plans.py --matrix-cell`).
"""
import re

from core.ports import IModelClient, ModelRequest, ModelResponse

# Single markdown fence envelope, optional language tag, single pass (no recursion): matches the
# fence the injector (`GenerationFaultInjectionClient`) wraps content in, and nothing beyond one
# such layer -- see the module docstring's double-fence note.
_SINGLE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\n(.*)\n```\s*$", re.DOTALL)


class ExtractorRepairClient(IModelClient):
    """Decorates an IModelClient: delegates `complete()`, then for `purpose == "generation"`
    responses whose `content` matches `_SINGLE_FENCE_RE`, replaces `content` with the captured
    inner group -- a single pass of markdown-fence removal, nothing more. Content that does not
    match (bare JSON objects, prose, anything not wrapped in exactly one recognizable fence
    envelope), and `purpose == "selection"` responses, pass through unchanged.

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
        match = _SINGLE_FENCE_RE.match(content)
        if match is None:
            return response

        self.repairs.append({"original_preview": content[:120]})
        return ModelResponse(
            content=match.group(1),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            model=response.model,
        )
