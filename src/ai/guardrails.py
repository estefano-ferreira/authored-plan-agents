"""Guardrail engine: declarative loading, composition by intersection and runtime evaluation (audited)."""
from pathlib import Path

import yaml

from core.capabilities import ToolSpec
from core.ports import AuditEvent
from core.guardrails import (
    GuardrailDecision,
    GuardrailDenied,
    GuardrailPolicy,
    GuardrailWideningError,
    IGuardrailEvaluator,
    PolicyScope,
)


def load_policy(path: str | Path, scope: PolicyScope) -> GuardrailPolicy:
    """Loads a GuardrailPolicy from a YAML (same format across all three scopes: platform/agent/plan)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    limits = data.get("limits", {})
    return GuardrailPolicy(
        id=data["id"],
        scope=scope,
        allow_tool_types=frozenset(data.get("allow_tool_types", ())),
        allow_targets=frozenset(data.get("allow_targets", ())),
        max_model_calls_per_step=int(limits.get("max_model_calls_per_step", 0)),
        max_tool_calls_per_step=int(limits.get("max_tool_calls_per_step", 0)),
    )


def compose(wider: GuardrailPolicy, narrower: GuardrailPolicy) -> GuardrailPolicy:
    """Effective policy between two scopes: intersection of allowed types/targets, limits by minimum."""
    return GuardrailPolicy(
        id=f"{wider.id}+{narrower.id}",
        scope=narrower.scope,
        allow_tool_types=wider.allow_tool_types & narrower.allow_tool_types,
        allow_targets=wider.allow_targets & narrower.allow_targets,
        max_model_calls_per_step=min(wider.max_model_calls_per_step, narrower.max_model_calls_per_step),
        max_tool_calls_per_step=min(wider.max_tool_calls_per_step, narrower.max_tool_calls_per_step),
    )


def _check_widening(wider: GuardrailPolicy, narrower: GuardrailPolicy) -> None:
    """Restriction 3: a narrower scope can never declare a tool_type/target absent from the broader one."""
    extra_types = narrower.allow_tool_types - wider.allow_tool_types
    extra_targets = narrower.allow_targets - wider.allow_targets
    if extra_types or extra_targets:
        raise GuardrailWideningError(
            f"policy '{narrower.id}' widens beyond '{wider.id}': "
            f"extra tool_types={sorted(extra_types)} extra targets={sorted(extra_targets)}"
        )


class GuardrailEngine:
    """Single guardrail engine: validates widening at load time and exposes the effective policy per agent/plan."""

    def __init__(
        self,
        platform_policy: GuardrailPolicy,
        agent_policies: dict[str, GuardrailPolicy],
        plan_policies: dict[str, GuardrailPolicy],
        audit,
    ):
        self.platform_policy = platform_policy
        self.agent_policies = agent_policies
        self.plan_policies = plan_policies
        self.audit = audit
        self._effective_agent: dict[str, GuardrailPolicy] = {}
        for agent_id, policy in agent_policies.items():
            _check_widening(platform_policy, policy)
            self._effective_agent[agent_id] = compose(platform_policy, policy)
        self._effective_plan: dict[str, GuardrailPolicy] = {}
        for plan_id, policy in plan_policies.items():
            _check_widening(platform_policy, policy)
            self._effective_plan[plan_id] = compose(platform_policy, policy)

    def evaluator_for(self, agent_id: str, plan_id: str | None = None) -> "GuardrailEvaluator":
        """Agent's effective policy, optionally refined by the plan's policy (always by intersection)."""
        effective = self._effective_agent.get(agent_id, self.platform_policy)
        if plan_id is not None and plan_id in self._effective_plan:
            effective = compose(effective, self._effective_plan[plan_id])
        return GuardrailEvaluator(effective, self.audit)


class GuardrailEvaluator(IGuardrailEvaluator):
    """Evaluates tools/inferences against the effective policy; audits every decision; denies by raising GuardrailDenied."""

    def __init__(self, effective_policy: GuardrailPolicy, audit):
        self.effective_policy = effective_policy
        self.audit = audit
        self._tool_calls: dict[str, int] = {}
        self._model_calls: dict[str, int] = {}

    def evaluate_tool(self, spec: ToolSpec, ctx: object) -> GuardrailDecision:
        step_id = getattr(ctx, "step_id", "")
        policy = self.effective_policy
        if spec.tool_type.name not in policy.allow_tool_types:
            decision = GuardrailDecision(False, "tool_type_not_allowed", policy.scope.value)
        elif spec.target.key() not in policy.allow_targets:
            decision = GuardrailDecision(False, "target_not_allowed", policy.scope.value)
        else:
            count = self._tool_calls.get(step_id, 0) + 1
            self._tool_calls[step_id] = count
            if count > policy.max_tool_calls_per_step:
                decision = GuardrailDecision(False, "max_tool_calls_per_step", policy.scope.value)
            else:
                decision = GuardrailDecision(True, "allowed", policy.scope.value)
        self._audit(ctx, "tool", spec.name, decision)
        if not decision.allowed:
            raise GuardrailDenied(decision)
        return decision

    def evaluate_inference(self, ctx: object) -> GuardrailDecision:
        step_id = getattr(ctx, "step_id", "")
        policy = self.effective_policy
        if "model:*" not in policy.allow_targets:
            decision = GuardrailDecision(False, "target_not_allowed", policy.scope.value)
        else:
            count = self._model_calls.get(step_id, 0) + 1
            self._model_calls[step_id] = count
            if count > policy.max_model_calls_per_step:
                decision = GuardrailDecision(False, "max_model_calls_per_step", policy.scope.value)
            else:
                decision = GuardrailDecision(True, "allowed", policy.scope.value)
        self._audit(ctx, "inference", "model", decision)
        if not decision.allowed:
            raise GuardrailDenied(decision)
        return decision

    def _audit(self, ctx: object, kind: str, target: str, decision: GuardrailDecision) -> None:
        principal = getattr(ctx, "principal", None)
        self.audit.record(AuditEvent(
            execution_id=getattr(ctx, "execution_id", ""),
            plan_id=getattr(ctx, "plan_id", ""),
            step_id=getattr(ctx, "step_id", ""),
            principal_id=getattr(principal, "id", ""),
            kind="guardrail",
            detail={"check": kind, "target": target, "allowed": decision.allowed, "rule": decision.rule},
        ))
