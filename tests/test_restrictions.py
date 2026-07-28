"""One test per architectural restriction (see DESIGN.md, Tests section). Local provider, ERP via ASGITransport."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ai import agents as agent_runtime_module
from ai.guardrails import GuardrailEngine, compose, load_policy
from ai.orchestrator import load_plans
from ai.agents import ToolRegistry
from core.agents import AgentCompositionError, ChannelViolationError
from core.capabilities import ToolClassificationError, ToolType, tool
from core.guardrails import GuardrailWideningError, PolicyScope
from core.identity import Principal
from core.orchestration import PlanValidationError
from core.ports import ConnectorTarget
from infrastructure.audit.audit_store import JsonlAuditWriter
from infrastructure.configurations import build_platform
from erp_service import api as erp_app_module

_REAL_PLANS_DIR = Path(__file__).resolve().parents[1] / "src" / "ai" / "plans"


@pytest.fixture
def platform(tmp_path):
    """Full platform (local provider), ERP mocked in-process, audit trail isolated in tmp_path."""
    erp_app_module.reset()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    audit_path = tmp_path / "audit.jsonl"
    built = build_platform(provider="local", erp_transport=transport, audit_path=audit_path)
    yield built
    erp_app_module.reset()


# 1. Restriction 1: a tool that writes business state must be SYSTEM_OF_RECORD; a SYSTEM_OF_RECORD
#    target cannot be a memory/database store.
def test_write_requires_system_of_record_and_no_memory_target():
    @tool(ToolType.READ_ONLY, ConnectorTarget(name="erp", kind="rest"), writes_business_state=True)
    def writes_without_system_of_record(ctx, **_kwargs) -> dict:
        return {}

    registry = ToolRegistry()
    with pytest.raises(ToolClassificationError):
        registry.register(writes_without_system_of_record)

    @tool(ToolType.SYSTEM_OF_RECORD, ConnectorTarget(name="cache", kind="memory"))
    def system_of_record_with_memory_target(ctx, **_kwargs) -> dict:
        return {}

    with pytest.raises(ToolClassificationError):
        registry.register(system_of_record_with_memory_target)

    @tool(ToolType.SYSTEM_OF_RECORD, ConnectorTarget(name="db", kind="database"))
    def system_of_record_with_database_target(ctx, **_kwargs) -> dict:
        return {}

    with pytest.raises(ToolClassificationError):
        registry.register(system_of_record_with_database_target)


# 2. Restriction 2: an agent cannot invoke another agent (execution within execution).
def test_agent_cannot_call_agent(platform):
    # Direct check: with the reentrancy flag set, any execute() is rejected.
    token = agent_runtime_module._reentrancy.set(True)
    try:
        with pytest.raises(AgentCompositionError):
            platform.runtime.execute("schedule-agent", "book an appointment", {}, object())
    finally:
        agent_runtime_module._reentrancy.reset(token)

    # Genuine scenario: a tool that tries to invoke another agent mid-execution.
    original = platform.tools.get("check_availability")

    def hostile_tool(ctx, **_kwargs) -> dict:
        return platform.runtime.execute("erp-agent", "register customer request record", {}, ctx)

    platform.tools._tools["check_availability"] = replace(original, fn=hostile_tool)
    try:
        result = platform.orchestrator.handle({
            "plan": "schedule-appointment",
            "payload": {"customer_id": "customer-1", "slot": "2026-09-01T10:00"},
            "principal": Principal(id="user-1", type="user", scopes=()),
        })
        assert result.status == "failed_clean"
        assert "within another agent execution" in result.rejection.reason
    finally:
        platform.tools._tools["check_availability"] = original


# 3. Restriction 3: a narrower-scope guardrail cannot widen beyond the broader one; the effective
#    policy is always the intersection (types/targets ∩, limits = min).
def test_guardrail_intersection_rejects_widening(tmp_path):
    platform_yaml = tmp_path / "platform-guardrail.yaml"
    platform_yaml.write_text(
        "id: platform-test\n"
        "scope: platform\n"
        "allow_tool_types: [READ_ONLY, SYSTEM_OF_RECORD]\n"
        "allow_targets: [\"rest:erp\", \"model:*\"]\n"
        "limits:\n"
        "  max_model_calls_per_step: 3\n"
        "  max_tool_calls_per_step: 5\n",
        encoding="utf-8",
    )
    widening_agent_yaml = tmp_path / "agent-guardrail-widens.yaml"
    widening_agent_yaml.write_text(
        "id: agent-widens\n"
        "scope: agent\n"
        "allow_tool_types: [READ_ONLY, SYSTEM_OF_RECORD]\n"
        # rest:scheduling is not in the platform policy above -> widens, must be rejected at load time.
        "allow_targets: [\"rest:erp\", \"rest:scheduling\", \"model:*\"]\n"
        "limits:\n"
        "  max_model_calls_per_step: 2\n"
        "  max_tool_calls_per_step: 3\n",
        encoding="utf-8",
    )

    platform_policy = load_policy(platform_yaml, PolicyScope.PLATFORM)
    widening_policy = load_policy(widening_agent_yaml, PolicyScope.AGENT)

    audit_path = tmp_path / "audit.jsonl"
    audit = JsonlAuditWriter(audit_path)

    with pytest.raises(GuardrailWideningError):
        GuardrailEngine(platform_policy, {"agent-widens": widening_policy}, {}, audit)

    # compose(): intersection of sets and minimum of the limits, between a valid narrower policy.
    narrower_yaml = tmp_path / "agent-guardrail-narrow.yaml"
    narrower_yaml.write_text(
        "id: agent-narrow\n"
        "scope: agent\n"
        "allow_tool_types: [READ_ONLY]\n"
        "allow_targets: [\"rest:erp\"]\n"
        "limits:\n"
        "  max_model_calls_per_step: 1\n"
        "  max_tool_calls_per_step: 2\n",
        encoding="utf-8",
    )
    narrower_policy = load_policy(narrower_yaml, PolicyScope.AGENT)
    effective = compose(platform_policy, narrower_policy)
    assert effective.allow_tool_types == frozenset({"READ_ONLY"})
    assert effective.allow_targets == frozenset({"rest:erp"})
    assert effective.max_model_calls_per_step == 1
    assert effective.max_tool_calls_per_step == 2


# 4. Restriction 4: an execution is only valid if the ExecutionContext was issued by the Orchestrator.
def test_entry_only_via_orchestrator(platform):
    principal = Principal(id="user-1", type="user", scopes=())
    fake_ctx = SimpleNamespace(issued_by_orchestrator=False, principal=principal)
    with pytest.raises(ChannelViolationError):
        platform.runtime.execute("schedule-agent", "book an appointment", {}, fake_ctx)


# 5. Restriction 5: the requester's identity reaches the system-of-record tool/target. With a user
#    Principal, the ERP receives X-Principal-Id; without a principal, the Orchestrator issues a
#    SystemPrincipal with the plan's entry_scopes.
def test_identity_propagated_to_tool(platform):
    user_principal = Principal(id="user-99", type="user", scopes=("scheduling:write", "whatsapp:send"))
    result = platform.orchestrator.handle({
        "plan": "schedule-appointment",
        "payload": {"customer_id": "customer-1", "slot": "2026-09-01T10:00"},
        "principal": user_principal,
    })
    assert result.status == "completed"
    assert erp_app_module.last_principal is not None
    assert erp_app_module.last_principal["id"] == "user-99"
    assert erp_app_module.last_principal["type"] == "user"

    erp_app_module.reset()
    result_system = platform.orchestrator.handle({
        "plan": "inbound-email-to-erp",
        "payload": {"email_id": "email-1"},
    })
    assert result_system.status == "completed"
    assert erp_app_module.last_principal is not None
    assert erp_app_module.last_principal["id"] == "system"
    assert erp_app_module.last_principal["type"] == "system"
    plan = platform.plans["inbound-email-to-erp"]
    assert set(erp_app_module.last_principal["scopes"]) == set(plan.entry_scopes)


# 6. Restriction 6: a step with effect=True and compensate=None must be the last step with an effect in the plan.
def test_effectful_without_compensation_must_be_last(tmp_path):
    bad_plans_dir = tmp_path / "plans"
    bad_plans_dir.mkdir()
    (bad_plans_dir / "bad-plan.yaml").write_text(
        "id: bad-plan\n"
        "version: 1.0.0\n"
        "description: invalid plan for testing restriction 6\n"
        "triggers: []\n"
        "entry_scopes: []\n"
        "steps:\n"
        "  - id: first\n"
        "    agent: schedule-agent\n"
        "    intent: \"book an appointment\"\n"
        "    input: {}\n"
        "    effect: true\n"
        "    compensate: null\n"
        "  - id: second\n"
        "    agent: schedule-agent\n"
        "    intent: \"book an appointment\"\n"
        "    input: {}\n"
        "    effect: true\n"
        "    compensate: null\n"
        "output: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(PlanValidationError):
        load_plans(bad_plans_dir)

    # the repository's real plans must load without error.
    real_plans = load_plans(_REAL_PLANS_DIR)
    assert "schedule-appointment" in real_plans
    assert "inbound-email-to-erp" in real_plans


# 7. Restriction 7: a request with no resolved plan is refused in a typed way, without executing any step.
def test_request_without_plan_is_refused(platform):
    result = platform.orchestrator.handle({"intent": "make breakfast", "payload": {}})
    assert result.status == "rejected"
    assert result.rejection is not None
    assert result.rejection.code == "plan_not_found"
    # no step was executed: the ERP mock never received any principal.
    assert erp_app_module.last_principal is None
