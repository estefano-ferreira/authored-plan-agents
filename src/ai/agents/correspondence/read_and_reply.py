"""ReadAndReply capability: reads the email, interprets the request and drafts/sends the reply (or correction).

Bundles the capability's declarative steps together with the tool functions it owns (`read_email`,
`send_reply`) and the connector target they invoke, defined inline as a local frozen value object
(no shared `targets.py` anymore).

Output contract (2026-07-27): `interpret_and_draft` constrains generation at the decoding level via
`ModelRequest.response_schema` and parses the response strictly (`json.loads`, no markdown-fence
stripping, no tolerant fallback). A violation is retried exactly once against the same prompt and
schema (`retry_once_then_fail`); a second violation raises `OutputContractViolation` and nothing is
persisted downstream -- see NOTES.md "Real-model findings" for the measurement that motivated this
(100% of real-model generations came back valid-JSON-in-markdown-fences, silently absorbed by the
old tolerant parser into truncated ERP `summary` rows).
"""
import json

from core.capabilities import Capability, Step, StepKind, ToolType, tool
from core.ports import AuditEvent, ConnectorTarget, ModelRequest

GMAIL = ConnectorTarget(name="gmail", kind="mcp")

# JSON Schema passed as `ModelRequest.response_schema`: constrains generation at the decoding
# level (native structured output on providers that support it -- see infrastructure/providers/*).
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "request_type": {"type": "string", "enum": ["support", "order", "cancellation"]},
        "summary": {"type": "string"},
        "reply_body": {"type": "string"},
    },
    "required": ["request_type", "summary", "reply_body"],
    "additionalProperties": False,
}


class OutputContractViolation(Exception):
    """Typed failure of `retry_once_then_fail`: `interpret_and_draft`'s generation call did not
    honor `_RESPONSE_SCHEMA` even after one retry against the identical prompt and schema.

    Carries `attempts` (always 2 -- the retry policy is fixed) and `preview` (the last
    non-conforming content, truncated to 200 chars, matching what was already audited). Raising
    this means nothing from either attempt is persisted: the step fails, and the Orchestrator's
    normal compensation path takes over exactly as for any other step exception.
    """

    def __init__(self, attempts: int, preview: str):
        super().__init__(f"output contract violated after {attempts} attempt(s): {preview!r}")
        self.attempts = attempts
        self.preview = preview


class ReadAndReply(Capability):
    """Reads an email, interprets the request via the model and sends the drafted reply."""
    id = "read-and-reply"
    description = "Reads the customer email, interprets the request and sends a reply drafted by the model."
    steps = (
        Step(id="read", kind=StepKind.TOOL, tool="read_email"),
        Step(id="interpret", kind=StepKind.INFERENCE, inference="interpret_and_draft", depends_on=("read",)),
        Step(id="reply", kind=StepKind.TOOL, tool="send_reply", depends_on=("interpret",)),
    )


@tool(ToolType.READ_ONLY, GMAIL)
def read_email(ctx, email_id: str, **_kwargs) -> dict:
    """Reads the email `email_id` (sender, subject and body)."""
    return ctx.connectors["mcp"].invoke(GMAIL, "read_email", {"email_id": email_id}, ctx.principal)


@tool(ToolType.IRREVERSIBLE, GMAIL)
def send_reply(ctx, email_id: str, results: dict | None = None, **_kwargs) -> dict:
    """Sends the reply (`results['interpret']['reply_body']`) for email `email_id`."""
    body = (results or {}).get("interpret", {}).get("reply_body", "")
    return ctx.connectors["mcp"].invoke(
        GMAIL, "send_reply", {"email_id": email_id, "body": body}, ctx.principal,
    )


def interpret_and_draft(ctx, payload: dict) -> dict:
    """Interprets the email that was read (or drafts a correction, if `payload['mode'] == 'correction'`) via the model.

    `ctx` is the ToolContext (has `.model`, `.audit` and `.context_builder`); `payload` is the full
    step payload, including `results['read']` with the email read by the previous step. Generation
    is constrained by `_RESPONSE_SCHEMA` and parsed strictly (`_parse_strict`); a violation is
    retried once against the identical prompt and schema before raising `OutputContractViolation`
    (see module docstring).
    """
    email = payload.get("results", {}).get("read", {})
    if payload.get("mode") == "correction":
        prompt = (
            "Draft a brief correction/retraction message (correction mode) about the previous processing "
            f"of email {payload.get('email_id', '')}. Respond in JSON with the keys "
            "request_type, summary, reply_body."
        )
    else:
        prompt = (
            "Interpret the customer email below and draft a reply.\n"
            f"From: {email.get('sender', '')}\nSubject: {email.get('subject', '')}\nBody: {email.get('body', '')}\n"
            "Respond in JSON with the keys request_type (support|order|cancellation), summary and reply_body."
        )
    head = ()
    if ctx.context_builder is not None:
        head = ctx.context_builder.build(ctx.agent_spec, ctx.session_id, {"email_id": payload.get("email_id")}, 2000)
    messages = tuple(head) + ({"role": "user", "content": prompt},)
    request = ModelRequest(messages=messages, purpose="generation", response_schema=_RESPONSE_SCHEMA)

    data = _complete_with_retry(ctx, request)
    return {
        "request_type": data.get("request_type", "support"),
        "summary": data.get("summary", ""),
        "reply_body": data.get("reply_body", ""),
        "sender": email.get("sender", ""),
    }


def _complete_with_retry(ctx, request: ModelRequest) -> dict:
    """retry_once_then_fail: calls the model, parses strictly; on violation, audits and retries the
    identical (prompt, schema) call exactly once; a second violation is audited as "failed" and
    raises `OutputContractViolation` -- no content from either attempt is ever persisted."""
    response = ctx.model.complete(request)
    try:
        return _parse_strict(response.content)
    except ValueError:
        pass

    _audit_output_contract(ctx, {"action": "violation", "attempt": 1, "content_preview": response.content[:200]})
    _audit_output_contract(ctx, {"action": "retry", "attempt": 1})

    response = ctx.model.complete(request)
    try:
        return _parse_strict(response.content)
    except ValueError:
        _audit_output_contract(ctx, {"action": "failed", "attempt": 2, "content_preview": response.content[:200]})
        raise OutputContractViolation(attempts=2, preview=response.content[:200])


def _audit_output_contract(ctx, detail: dict) -> None:
    ctx.audit.record(AuditEvent(
        execution_id=ctx.execution_id, plan_id=ctx.plan_id, step_id=ctx.step_id,
        principal_id=ctx.principal.id, kind="output_contract", detail=detail,
    ))


def _parse_strict(content: str) -> dict:
    """Strict decode-level parse: `json.loads` only -- no markdown-fence stripping, no tolerant
    fallback. Raises ValueError (treated by the caller as an output-contract violation) if the
    content is not valid JSON, is not an object, or is missing any of `_RESPONSE_SCHEMA`'s
    required keys."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("parsed content is not a JSON object")
    missing = [key for key in _RESPONSE_SCHEMA["required"] if key not in data]
    if missing:
        raise ValueError(f"missing required keys: {missing}")
    return data
