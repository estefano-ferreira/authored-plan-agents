"""Independent adversarial check that the 7 restrictions in DESIGN.md, plus SPECIFICATION.md's § 5.1
(output-contract enforcement on the write path), are actually enforced, not vacuous.

`tests/test_restrictions.py` was written by the same agent that wrote the code under test, so a test
there that merely asserts "the code behaves as the code was written" proves nothing about whether the
restriction is real. This module inverts the exercise: for each restriction it deliberately COMMITS the
violation the restriction is supposed to forbid, and asserts that the violation SUCCEEDED (the forbidden
outcome happened). Since the platform is supposed to block every one of these, each test is marked
`@pytest.mark.xfail(strict=True, ...)`: the assertion that the violation succeeded is expected to FAIL
(the platform actually raises/rejects before that point), which pytest reports as XFAIL — expected
failure, restriction intact.

Two of the nine probes below (§ 5.1) target `build_platform`'s `measurement_mode` gate rather than one
of DESIGN.md's 7 numbered restrictions: repair-before-check must be structurally impossible in
production composition, and unvalidated content must never persist on the write path. Each carries a
companion, non-xfail test proving the flag path itself still works (anti-vacuity of the anti-vacuity) —
without it, an xfail caused by an unrelated construction failure would look identical to enforcement.

With `strict=True`, if a violation is NOT blocked, the "assert it was permitted" line actually passes,
the test as a whole PASSES, and pytest reports it as XPASS — which strict xfail promotes to a hard
failure. An XPASS here is a genuine finding: the restriction it targets is not enforced by the code,
despite what `tests/test_restrictions.py` claims.

This file must never be "fixed" to make violations blocked more thoroughly or to pass normally — it is
a probe, not a feature test. Each test carries a one-line comment naming the error/rejection expected to
interrupt the violation before the "permitted" assertion is reached.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ai.guardrails import GuardrailEngine, load_policy
from ai.orchestrator import load_plans
from ai.agents import ToolRegistry
from core.capabilities import ToolType, tool
from core.guardrails import PolicyScope
from core.identity import Principal
from core.ports import ConnectorTarget
from infrastructure.audit.audit_store import JsonlAuditWriter
from infrastructure.configurations import build_platform
from erp_service import api as erp_app_module

_REAL_PLANS_DIR = Path(__file__).resolve().parents[1] / "src" / "ai" / "plans"


@pytest.fixture
def platform(tmp_path):
    """Full platform (local provider), ERP mocked in-process, audit trail isolated in tmp_path.

    Mirrors the fixture in tests/test_restrictions.py so both suites exercise the same real wiring.
    """
    erp_app_module.reset()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    audit_path = tmp_path / "audit.jsonl"
    built = build_platform(provider="local", erp_transport=transport, audit_path=audit_path)
    yield built
    erp_app_module.reset()


# 1. Restriction 1: a tool that writes business state must be SYSTEM_OF_RECORD, and a SYSTEM_OF_RECORD
#    target must never be a memory/database store. Try to register the two forbidden combinations and
#    assert they were accepted into the registry.
@pytest.mark.xfail(strict=True, reason="restriction 1 must block this")
def test_restriction_1_registry_accepts_misclassified_tools():
    # Expected blocker: ToolRegistry.register raises ToolClassificationError (SYSTEM_OF_RECORD + memory target).
    @tool(ToolType.SYSTEM_OF_RECORD, ConnectorTarget(name="session", kind="memory"))
    def hostile_system_of_record_memory(ctx, **_kwargs) -> dict:
        return {}

    registry = ToolRegistry()
    registered = registry.register(hostile_system_of_record_memory)
    # If reached, a SYSTEM_OF_RECORD tool backed by a memory store was accepted -- restriction 1 breached.
    assert registry.get("hostile_system_of_record_memory") is registered

    # Expected blocker (if the first assert were somehow skipped): ToolClassificationError for a
    # READ_ONLY tool declaring writes_business_state=True.
    @tool(ToolType.READ_ONLY, ConnectorTarget(name="scheduling", kind="rest"), writes_business_state=True)
    def hostile_read_only_writes(ctx, **_kwargs) -> dict:
        return {}

    registered_ref = registry.register(hostile_read_only_writes)
    # If reached, a non-SYSTEM_OF_RECORD tool was allowed to claim it writes business state -- also a breach.
    assert registry.get("hostile_read_only_writes") is registered_ref


# 2. Restriction 2: an agent must not be able to invoke another agent mid-execution. Swap in a hostile
#    tool that calls runtime.execute() on another agent from inside a running capability, run a real
#    plan through the real platform, and assert the plan completed (i.e. the agent-to-agent call worked).
@pytest.mark.xfail(strict=True, reason="restriction 2 must block this")
def test_restriction_2_agent_to_agent_call_completes_plan(platform):
    # Expected blocker: AgentCompositionError raised by the reentrancy guard in AgentRuntime.execute,
    # surfacing as ExecutionResult.status == "failed_clean" (no effect step completed) instead of "completed".
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
        # If reached, the nested agent-to-agent call was allowed to compose into a completed plan.
        assert result.status == "completed"
    finally:
        platform.tools._tools["check_availability"] = original


# 3. Restriction 3: a narrower-scope (agent) guardrail must never widen beyond the platform policy.
#    Build a GuardrailEngine with an agent policy that adds a target absent from the platform policy,
#    and assert the engine builds and evaluator_for() hands back a usable evaluator.
@pytest.mark.xfail(strict=True, reason="restriction 3 must block this")
def test_restriction_3_widening_agent_policy_is_accepted(tmp_path):
    # Expected blocker: GuardrailWideningError raised inside GuardrailEngine.__init__.
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
        # rest:scheduling is not in the platform policy above -> this is a widening, must be rejected at load time.
        "allow_targets: [\"rest:erp\", \"rest:scheduling\", \"model:*\"]\n"
        "limits:\n"
        "  max_model_calls_per_step: 2\n"
        "  max_tool_calls_per_step: 3\n",
        encoding="utf-8",
    )

    platform_policy = load_policy(platform_yaml, PolicyScope.PLATFORM)
    widening_policy = load_policy(widening_agent_yaml, PolicyScope.AGENT)
    audit = JsonlAuditWriter(tmp_path / "audit.jsonl")

    engine = GuardrailEngine(platform_policy, {"agent-widens": widening_policy}, {}, audit)
    evaluator = engine.evaluator_for("agent-widens")
    # If reached, an agent policy that widens beyond the platform policy was accepted and is now live.
    assert evaluator is not None


# 4. Restriction 4: an execution must only be valid if the ExecutionContext was issued by the
#    Orchestrator. Call runtime.execute() with a hand-built ctx claiming issued_by_orchestrator=False
#    and assert it still ran (returned a dict) instead of being rejected.
@pytest.mark.xfail(strict=True, reason="restriction 4 must block this")
def test_restriction_4_execute_without_orchestrator_issued_ctx_runs(platform):
    # Expected blocker: ChannelViolationError raised at the top of AgentRuntime.execute.
    principal = Principal(id="user-1", type="user", scopes=())
    fake_ctx = SimpleNamespace(issued_by_orchestrator=False, principal=principal)
    result = platform.runtime.execute("schedule-agent", "book an appointment", {}, fake_ctx)
    # If reached, an execution entering outside the Orchestrator was allowed to run and return a result.
    assert isinstance(result, dict)


# 5. Restriction 5: identity must reach the tool -- an execution without a principal must be refused,
#    not silently allowed to proceed anonymously. Call runtime.execute() with an orchestrator-issued ctx
#    that nonetheless carries principal=None and assert it still ran.
@pytest.mark.xfail(strict=True, reason="restriction 5 must block this")
def test_restriction_5_execute_without_principal_runs(platform):
    # Expected blocker: ChannelViolationError raised at the top of AgentRuntime.execute (principal is None).
    fake_ctx = SimpleNamespace(issued_by_orchestrator=True, principal=None)
    result = platform.runtime.execute("schedule-agent", "book an appointment", {}, fake_ctx)
    # If reached, an execution with no identity at all was allowed to run and return a result.
    assert isinstance(result, dict)


# 6. Restriction 6: a step with effect=True and compensate=None must be the last effectful step in the
#    plan (an irreversible step followed by more effects has no way to roll back). Load a plan that
#    violates this and assert it loaded successfully.
@pytest.mark.xfail(strict=True, reason="restriction 6 must block this")
def test_restriction_6_uncompensated_effect_followed_by_more_effects_loads(tmp_path):
    # Expected blocker: PlanValidationError raised inside ai.orchestrator.load_plans.
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
    plans = load_plans(bad_plans_dir)
    # If reached, a plan with an irreversible effect step stranded before another effect step was accepted.
    assert "bad-plan" in plans

    # Sanity check that the real plans still load under the same loader (not exercised by the xfail path
    # above, kept here only to document the loader is otherwise functional).
    real_plans = load_plans(_REAL_PLANS_DIR)
    assert "schedule-appointment" in real_plans


# 7. Restriction 7: a request that matches no plan must be refused in a typed way, never improvised.
#    Send an intent with no plausible plan match and assert the orchestrator completed it anyway.
@pytest.mark.xfail(strict=True, reason="restriction 7 must block this")
def test_restriction_7_unmatched_intent_is_improvised_into_completion(platform):
    # Expected blocker: no plan resolves -> ExecutionResult.status == "rejected" (rejection.code == "plan_not_found").
    result = platform.orchestrator.handle({"intent": "totally unrelated nonsense", "payload": {}})
    # If reached, the orchestrator ran some plan/step for an intent that matches nothing in the catalog.
    assert result.status == "completed"


# 8. SPECIFICATION 5.1 ("repair-before-check is structurally impossible on business writes"):
#    production composition (measurement_mode left at its default, False) must refuse outright to
#    wire a repair decorator onto the model client -- not merely warn about it in a docstring.
#    Attempt to build a platform with tolerant_repair=True and no measurement_mode, and assert the
#    platform was built (the violation succeeding).
@pytest.mark.xfail(
    strict=True,
    reason="SPECIFICATION 5.1: production composition must reject repair decorators on the write path",
)
def test_restriction_5_1_production_composition_accepts_repair_decorator(tmp_path):
    # Expected blocker: ValueError raised inside build_platform (tolerant_repair=True, measurement_mode
    # left False) -- checked before any other collaborator is assembled, so construction never reaches
    # the "platform was built" line below.
    erp_app_module.reset()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    built = build_platform(
        provider="local", erp_transport=transport, audit_path=tmp_path / "audit.jsonl", tolerant_repair=True,
    )
    # If reached, a repair decorator was wired into a production composition -- restriction breached.
    assert built is not None
    erp_app_module.reset()


# Companion to the probe above -- deliberately NOT xfail. Proves the xfail above is blocked by the
# measurement_mode gate specifically (the identical construction succeeds once measurement_mode=True
# is passed), not by some unrelated construction failure that would make the probe above vacuous in
# the other direction (an xfail for the wrong reason looks identical to an xfail for the right one).
def test_restriction_5_1_measurement_mode_allows_repair_decorator(tmp_path):
    erp_app_module.reset()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    built = build_platform(
        provider="local", erp_transport=transport, audit_path=tmp_path / "audit.jsonl",
        tolerant_repair=True, measurement_mode=True,
    )
    assert built is not None
    assert built.repair_client is not None
    erp_app_module.reset()


# 9. SPECIFICATION 5.1 ("nothing unvalidated is ever persisted"): commit the violation end to end.
#    A measurement-mode platform on the local provider with the schema stripped and the generation
#    response corrupted (fenced), no repair decorator wired -- exactly the strict guard alone
#    against the failure class it exists to catch (integrity-matrix cell "B", see
#    infrastructure/configurations.py). Run the inbound email-to-ERP plan once and assert at least
#    one service-request row was created (the violation succeeding).
@pytest.mark.xfail(strict=True, reason="SPECIFICATION 5.1: nothing unvalidated persists on the write path")
def test_restriction_5_1_unvalidated_content_persists_on_write_path(tmp_path):
    # Expected blocker: OutputContractViolation raised inside read_and_reply.py's
    # _complete_with_retry -- GenerationFaultInjectionClient corrupts BOTH the first attempt and the
    # identical retry the same way, so the "acknowledge" step never returns, "register" (the
    # SYSTEM_OF_RECORD write) never runs, and no service_requests row is ever created.
    erp_app_module.reset()
    transport = httpx.ASGITransport(app=erp_app_module.app)
    built = build_platform(
        provider="local", erp_transport=transport, audit_path=tmp_path / "audit.jsonl",
        inject_generation_fault=True, strip_response_schema=True, measurement_mode=True,
    )

    _result = built.orchestrator.handle({"plan": "inbound-email-to-erp", "payload": {"email_id": "email-5.1-probe"}})

    # If reached, unvalidated content survived the strict guard and was persisted into the ERP.
    assert len(erp_app_module._records) >= 1
    erp_app_module.reset()
