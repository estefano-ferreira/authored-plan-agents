"""Deterministic, zero-cost provider: used by default in tests and scripts."""
import hashlib
import json
import re

from core.ports import IModelClient, ModelRequest, ModelResponse

_MODEL_NAME = "local-deterministic"

# capability/plan id -> keywords that trigger it (order = priority).
_SELECTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("book", ("book", "agendar", "marcar", "schedule an appointment")),
    ("schedule-appointment", ("schedule", "book")),
    ("reschedule", ("reschedule", "remarcar")),
    ("read-and-reply", ("read", "reply", "email", "e-mail")),
    ("inbound-email-to-erp", ("inbound email", "email recebido", "received email", "solicitação por email", "email request")),
    ("register-request", ("register",)),
    ("cancel-request", ("cancel",)),
)


def _prompt_text(request: ModelRequest) -> str:
    """Concatenates the content of all messages into a single text for analysis."""
    return "\n".join(str(m.get("content", "")) for m in request.messages)


# Both plan-selection (ai/orchestrator.py) and capability-selection (ai/agents/runtime.py) prompts
# render their catalog as one "- <id>: <description...>" line per offered candidate.
_CATALOG_ID_RE = re.compile(r"(?m)^- ([\w.-]+):")
_INTENT_RE = re.compile(r"Intent:\s*(.*)")


def _offered_ids(text: str) -> set[str]:
    """Ids actually offered in this prompt's catalog (vs. merely appearing somewhere in the text)."""
    return set(_CATALOG_ID_RE.findall(text))


def _intent_text(text: str) -> str:
    """Isolates the 'Intent: ...' line; falls back to the full text if the prompt doesn't have one."""
    match = _INTENT_RE.search(text)
    return match.group(1) if match else text


def _select(request: ModelRequest) -> str:
    """Chooses an id (capability or plan) by keyword *in the intent*, restricted to catalog-offered ids.

    Two guards, both required: (1) a candidate is only eligible if its id is actually offered in this
    prompt's catalog -- e.g. "book" (a capability id) must not win a *plan*-selection prompt just
    because it appears inside another plan's description/triggers; (2) the keyword match runs against
    the intent text only, not the full prompt -- otherwise a candidate's own catalog line (which
    typically echoes its own trigger words) can satisfy the match regardless of what the caller
    actually asked for. Both leaks are invisible when the two catalog entries never share vocabulary
    with each other's descriptions and the caller always passes an explicit id (the original use case);
    they surface once multiple plans are offered together and selection is driven purely by a
    natural-language intent (see scripts/run_plans.py --repeat).

    Single-candidate catalogs (e.g. an agent with exactly one capability) are a degenerate case with
    no ambiguity to resolve: the offered id wins outright, without requiring a keyword match. This
    matters for compensation flows, whose intent ("send correction about failed processing") does not
    restate the capability's own trigger words -- a real model would recognize the sole capability as
    the obvious (only) choice; this stub does too, deterministically, rather than misreporting "none".
    """
    full_text = _prompt_text(request)
    offered = _offered_ids(full_text)
    if len(offered) == 1:
        return next(iter(offered))
    intent_lower = _intent_text(full_text).lower()
    for candidate_id, keywords in _SELECTION_KEYWORDS:
        if candidate_id not in offered:
            continue
        if any(kw in intent_lower for kw in keywords):
            return candidate_id
    return "none"


def _first_words(text: str, n: int = 8) -> str:
    words = text.split()
    return " ".join(words[:n])


def _generate(request: ModelRequest) -> str:
    """Produces a canonical JSON deterministic function of the prompt, or correction text.

    When `request.response_schema` is present, this simulates a provider honoring native
    structured output at the decoding level: the return is ALWAYS a raw JSON object (no markdown
    fences, no loose text in any branch) containing every key the schema marks as required --
    including the correction branch, whose message now lives inside `reply_body` instead of being
    returned as plain text. Without a schema, the pre-existing behavior (plain correction text,
    JSON string otherwise) is preserved for callers that never request structured output.
    """
    text = _prompt_text(request)
    lower = text.lower()
    # the actual correction prompt (capabilities/CorrespondenceAgent/ReadAndReply) is now written in
    # English ("correction mode" / "correction/retraction") and always contains both "mode" and
    # "correction"; we keep the legacy pt-BR detection ("correção"/"retratação") for backward
    # compatibility with any prompts still assembled in Portuguese.
    is_correction = ("mode" in lower and "correction" in lower) or ("correção" in lower and "retratação" in lower)

    if is_correction:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        correction_text = (
            "Dear customer, we identified a failure while processing your previous request "
            f"and are correcting it (ref. {digest}). We apologize for the inconvenience."
        )
        if request.response_schema is None:
            return correction_text
        payload = {
            "request_type": "support",
            "summary": f"correction (ref. {digest})",
            "reply_body": correction_text,
        }
        return _with_required_keys(payload, request.response_schema)

    body_match = re.search(r"body[\"']?\s*[:=]\s*[\"']?([^\"'\n]+)", text, re.IGNORECASE)
    body = body_match.group(1) if body_match else text
    request_type = "support"
    if any(kw in lower for kw in ("cancel", "cancelamento")):
        request_type = "cancellation"
    elif any(kw in lower for kw in ("order", "pedido de compra", "compra")):
        request_type = "order"
    summary = _first_words(body.strip())
    payload = {
        "request_type": request_type,
        "summary": summary,
        "reply_body": f"We received your message: \"{summary}\". We will follow up shortly with a solution.",
    }
    if request.response_schema is not None:
        return _with_required_keys(payload, request.response_schema)
    return json.dumps(payload, ensure_ascii=False)


def _with_required_keys(payload: dict, schema: dict) -> str:
    """Guarantees every key the schema marks as required is present, then serializes to raw JSON.

    Used only when `response_schema` is set (purpose=="generation"): the caller expects a provider
    that honors native structured output, so the returned string is always a bare JSON object --
    never wrapped in markdown fences, never a loose-text fallback.
    """
    complete = dict(payload)
    for key in schema.get("required", ()):
        complete.setdefault(key, "")
    return json.dumps(complete, ensure_ascii=False)


class LocalModelClient(IModelClient):
    """Network-free client: keyword selection and canonical generation, zero cost."""

    def __init__(self) -> None:
        self._seen_prefixes: set[str] = set()

    def complete(self, request: ModelRequest) -> ModelResponse:
        prompt = _prompt_text(request)
        if request.purpose == "selection":
            content = _select(request)
        else:
            content = _generate(request)

        input_tokens = len(prompt) // 4
        output_tokens = len(content) // 4

        prefix_key = hashlib.sha256(prompt[: max(len(prompt) // 2, 1)].encode("utf-8")).hexdigest()
        if prefix_key in self._seen_prefixes:
            cache_read_tokens = input_tokens // 2
        else:
            cache_read_tokens = 0
            self._seen_prefixes.add(prefix_key)

        return ModelResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            model=_MODEL_NAME,
        )
