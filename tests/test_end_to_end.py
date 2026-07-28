"""End to end with the local provider (zero cost): the 2 plans complete; compensation scenario;
full audit trail; cost/token metrics."""
import httpx
import pytest

from core.identity import Principal
from infrastructure.configurations import build_platform
from infrastructure.connectors.mcp.mcp_connector import McpConnector
from erp_service import api as erp_app_module


@pytest.fixture
def platform(tmp_path):
    """Full platform (local provider), ERP mocked in-process, audit trail isolated in tmp_path."""
    erp_app_module.reset()
    McpConnector.stub_outbox.clear()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    audit_path = tmp_path / "audit.jsonl"
    built = build_platform(provider="local", erp_transport=transport, audit_path=audit_path)
    yield built
    erp_app_module.reset()
    McpConnector.stub_outbox.clear()


def test_schedule_appointment_completes_with_resolved_output(platform):
    result = platform.orchestrator.handle({
        "plan": "schedule-appointment",
        "payload": {"customer_id": "customer-1", "slot": "2026-08-05T09:00"},
        "principal": Principal(id="user-1", type="user", scopes=("scheduling:write", "whatsapp:send")),
    })
    assert result.status == "completed"
    appointment_id = result.output.get("appointment_id")
    assert appointment_id is not None
    assert appointment_id.startswith("appointment-")

    summary = platform.console_obs.summary(result.execution_id)
    assert summary["cost_usd"] == 0.0
    assert summary["input_tokens"] + summary["output_tokens"] > 0


def test_inbound_email_to_erp_completes_with_resolved_output(platform):
    result = platform.orchestrator.handle({
        "plan": "inbound-email-to-erp",
        "payload": {"email_id": "email-1"},
        # no "principal": the Orchestrator issues a SystemPrincipal with the plan's entry_scopes.
    })
    assert result.status == "completed"
    record_id = result.output.get("record_id")
    reply_id = result.output.get("reply_id")
    assert record_id is not None and record_id.startswith("record-")
    assert reply_id is not None and reply_id.startswith("reply-")

    summary = platform.console_obs.summary(result.execution_id)
    assert summary["cost_usd"] == 0.0
    assert summary["input_tokens"] + summary["output_tokens"] > 0


def test_inbound_email_compensates_when_erp_rejects_invariant(platform, monkeypatch):
    """Forces a violation of the ERP invariant (no request_type is valid in this execution) to exercise
    compensation: the record registration fails, the Orchestrator compensates the 'acknowledge' step
    (correction email), and cancelling the record is never called because the record never got created."""
    monkeypatch.setattr(erp_app_module, "_VALID_REQUEST_TYPES", frozenset())

    result = platform.orchestrator.handle({
        "plan": "inbound-email-to-erp",
        "payload": {"email_id": "email-1"},
    })

    assert result.status == "compensated"
    assert result.rejection is not None
    assert result.rejection.code == "invariant_violated"

    # correction email present in the McpConnector stub outbox.
    correction_sent = any(
        entry["server"] == "gmail" and entry["operation"] == "send_reply"
        and "identified a failure" in entry["payload"].get("body", "")
        for entry in McpConnector.stub_outbox
    )
    assert correction_sent, f"no correction email found in {McpConnector.stub_outbox!r}"

    # cancel_record was never called: no record ever got created in the ERP mock.
    assert erp_app_module._records == {}


def test_audit_contains_proposal_guardrail_and_system_of_record_kinds(platform):
    result = platform.orchestrator.handle({
        "plan": "schedule-appointment",
        "payload": {"customer_id": "customer-2", "slot": "2026-08-06T09:00"},
        "principal": Principal(id="user-2", type="user", scopes=("scheduling:write", "whatsapp:send")),
    })
    assert result.status == "completed"

    events = platform.audit.read_all()
    kinds = {e["kind"] for e in events}
    assert "proposal" in kinds
    assert "guardrail" in kinds
    assert "system_of_record" in kinds
