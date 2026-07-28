"""Orchestrator: the platform's single entry point. Resolves the plan, issues identity and executes steps."""
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.identity import Principal, SystemPrincipal
from core.orchestration import (
    ExecutionPlan,
    ExecutionResult,
    IOrchestrator,
    PlanStep,
    PlanValidationError,
    Rejection,
)
from core.ports import AuditEvent, ModelCallEvent, ModelRequest, ModelResponse, StepEvent

_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


@dataclass
class ExecutionContext:
    """Execution identity propagated throughout the plan. Only the Orchestrator issues it (restriction 4)."""
    execution_id: str
    plan: ExecutionPlan
    principal: Principal
    session_id: str
    steps: dict = field(default_factory=dict)
    issued_by_orchestrator: bool = False

    @classmethod
    def _issue(cls, execution_id: str, plan: ExecutionPlan, principal: Principal, session_id: str) -> "ExecutionContext":
        """Internal factory used exclusively by the Orchestrator; marks `issued_by_orchestrator=True`."""
        ctx = cls(execution_id=execution_id, plan=plan, principal=principal, session_id=session_id)
        ctx.issued_by_orchestrator = True
        return ctx


class Orchestrator(IOrchestrator):
    """The platform's single entry point: resolves the plan, issues identity and executes the steps."""

    def __init__(self, plans, registry, runtime, guardrails, model, session_store, obs, audit):
        self.plans = plans
        self.registry = registry
        self.runtime = runtime
        self.guardrails = guardrails
        self.model = model
        self.session_store = session_store
        self.obs = obs
        self.audit = audit

    def handle(self, request: dict) -> ExecutionResult:
        """Resolves the plan, executes the steps in declared order and compensates on failure."""
        execution_id = str(uuid.uuid4())
        plan = self._resolve_plan(request, execution_id)
        if plan is None:
            return ExecutionResult(
                execution_id=execution_id,
                status="rejected",
                rejection=Rejection("plan_not_found", "no plan matched the request"),
            )
        principal = request.get("principal") or SystemPrincipal.with_scopes(plan.entry_scopes)
        session_id = request.get("session_id", execution_id)
        ctx = ExecutionContext._issue(execution_id, plan, principal, session_id)
        completed_effect_steps: list[PlanStep] = []
        for step in plan.steps:
            self._check_depends(step, ctx)
            inputs = self._resolve_templates(step.input, request, ctx)
            started = time.monotonic()
            try:
                result = self.runtime.execute(step.agent, step.intent, inputs, ctx)
            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                self.obs.step(StepEvent(execution_id, plan.id, step.id, step.agent, "failed", latency_ms))
                return self._compensate_and_fail(execution_id, plan, completed_effect_steps, request, ctx, exc)
            latency_ms = (time.monotonic() - started) * 1000
            self.obs.step(StepEvent(execution_id, plan.id, step.id, step.agent, "completed", latency_ms))
            ctx.steps[step.id] = {"result": result}
            if step.effect:
                completed_effect_steps.append(step)
        output = self._resolve_templates(plan.output, request, ctx)
        self.obs.trace(execution_id, {"plan": plan.id, "status": "completed"})
        return ExecutionResult(
            execution_id=execution_id, status="completed", output=output,
            metrics={"steps_executed": len(plan.steps)},
        )

    def _resolve_plan(self, request: dict, execution_id: str) -> ExecutionPlan | None:
        """`request["plan"]` directly, otherwise model-based selection (catalog id+description+triggers, or 'none')."""
        plan_id = request.get("plan")
        if plan_id:
            return self.plans.get(plan_id)
        intent = request.get("intent", "")
        catalog = "\n".join(
            f"- {p.id}: {p.description} (triggers: {', '.join(p.triggers)})" for p in self.plans.values()
        )
        prompt = (
            "Choose the id of the plan best suited to the intent below, or answer exactly 'none' "
            f"if none applies.\nIntent: {intent}\n\nAvailable plans:\n{catalog}"
        )
        response = self._measured_complete(
            ModelRequest(messages=({"role": "user", "content": prompt},), purpose="selection"),
            execution_id, "plan-selection",
        )
        chosen = response.content.strip()
        self.audit.record(AuditEvent(
            execution_id=execution_id, plan_id=chosen if chosen in self.plans else "", step_id="plan-selection",
            principal_id="", kind="proposal", detail={"intent": intent, "chosen": chosen},
        ))
        if chosen == "none":
            return None
        return self.plans.get(chosen)

    def _measured_complete(self, request: ModelRequest, execution_id: str, step_id: str) -> ModelResponse:
        """Calls the model measuring latency and emitting a ModelCallEvent for observability/cost."""
        started = time.monotonic()
        response = self.model.complete(request)
        latency_ms = (time.monotonic() - started) * 1000
        self.obs.model_call(ModelCallEvent(
            execution_id=execution_id, step_id=step_id, model=response.model,
            input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens, latency_ms=latency_ms, purpose=request.purpose,
        ))
        return response

    @staticmethod
    def _check_depends(step: PlanStep, ctx: ExecutionContext) -> None:
        missing = [dep for dep in step.depends_on if dep not in ctx.steps]
        if missing:
            raise ValueError(f"step '{step.id}' depends on unexecuted step(s): {missing}")

    def _compensate_and_fail(
        self, execution_id: str, plan: ExecutionPlan, completed_effect_steps: list[PlanStep],
        request: dict, ctx: ExecutionContext, original_exc: Exception,
    ) -> ExecutionResult:
        """Compensates, in reverse order, the effect steps already completed.

        Status vocabulary (see `ExecutionResult.status` docstring for the canonical list):
        `failed_clean` when `completed_effect_steps` is empty (nothing irreversible ran, nothing to
        compensate -- no "compensation" events are emitted at all, only the "outcome" one below);
        `compensated` when there was something to compensate and every compensation succeeded;
        `compensation_failed` when there was something to compensate and at least one compensation
        attempt failed.

        Each compensation attempt is audited (kind="compensation") under
        `step_id=f"{step.id}:compensate"` -- the same suffix convention `AgentRuntime._compensate`
        uses for intra-capability compensation, so the two audit trails read consistently: "start"
        right before the compensating call, "result" on success, "failed" on failure. The "failed"
        event carries `orphaned_state`: the original step's result (e.g. a record_id), which remains
        unremediated in the business system because the compensating call itself did not go through.
        A final "outcome" event (`detail={"action": "outcome", "status": ..., "compensable_steps": ...}`)
        is always recorded, once per failing execution, so the audit trail alone answers which of the
        three status outcomes occurred without a reader having to recount the "start"/"result"/"failed"
        events themselves.
        """
        compensation_failed = False
        for step in reversed(completed_effect_steps):
            comp = step.compensate
            if comp is None:
                continue
            compensate_step_id = f"{step.id}:compensate"
            self.audit.record(AuditEvent(
                execution_id=execution_id, plan_id=plan.id, step_id=compensate_step_id,
                principal_id=ctx.principal.id, kind="compensation",
                detail={
                    "action": "start", "step": step.id,
                    "target_agent": comp["agent"], "target_intent": comp["intent"],
                },
            ))
            try:
                comp_input = self._resolve_templates(comp["input"], request, ctx)
                comp_result = self.runtime.execute(comp["agent"], comp["intent"], comp_input, ctx)
                ctx.steps[step.id] = {**ctx.steps.get(step.id, {}), "compensation": {"result": comp_result}}
                self.audit.record(AuditEvent(
                    execution_id=execution_id, plan_id=plan.id, step_id=compensate_step_id,
                    principal_id=ctx.principal.id, kind="compensation",
                    detail={"action": "result", "step": step.id, "result": comp_result},
                ))
            except Exception as exc:
                compensation_failed = True
                self.audit.record(AuditEvent(
                    execution_id=execution_id, plan_id=plan.id, step_id=compensate_step_id,
                    principal_id=ctx.principal.id, kind="compensation",
                    detail={
                        "action": "failed", "step": step.id, "error": str(exc),
                        "orphaned_state": ctx.steps.get(step.id, {}).get("result"),
                    },
                ))
        if not completed_effect_steps:
            status = "failed_clean"
        elif compensation_failed:
            status = "compensation_failed"
        else:
            status = "compensated"
        self.audit.record(AuditEvent(
            execution_id=execution_id, plan_id=plan.id, step_id="compensation:outcome",
            principal_id=ctx.principal.id, kind="compensation",
            detail={"action": "outcome", "status": status, "compensable_steps": len(completed_effect_steps)},
        ))
        self.obs.trace(execution_id, {"plan": plan.id, "status": status, "error": str(original_exc)})
        return ExecutionResult(
            execution_id=execution_id, status=status,
            rejection=Rejection("invariant_violated", str(original_exc)),
        )

    def _resolve_templates(self, value, request: dict, ctx: ExecutionContext):
        """Recursively resolves `{{ request.* }}` / `{{ steps.<id>.result.* }}` templates in dict/list/str."""
        if isinstance(value, dict):
            return {k: self._resolve_templates(v, request, ctx) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_templates(v, request, ctx) for v in value]
        if isinstance(value, str):
            stripped = value.strip()
            whole = _TEMPLATE_RE.fullmatch(stripped)
            if whole:
                return self._lookup(whole.group(1), request, ctx)
            return _TEMPLATE_RE.sub(lambda m: str(self._lookup(m.group(1), request, ctx)), value)
        return value

    @staticmethod
    def _lookup(path: str, request: dict, ctx: ExecutionContext):
        parts = path.split(".")
        root, rest = parts[0], parts[1:]
        if root == "request":
            node = request.get("payload") or {}
        elif root == "steps":
            if not rest:
                return None
            step_id, *rest = rest
            node = ctx.steps.get(step_id, {})
        else:
            return None
        for part in rest:
            node = node.get(part) if isinstance(node, dict) else getattr(node, part, None)
        return node


def load_plans(plans_dir: str | Path) -> dict[str, ExecutionPlan]:
    """Loads the declarative plans from `plans_dir`, validating restriction 6 (effect compensation)."""
    plans: dict[str, ExecutionPlan] = {}
    for path in sorted(Path(plans_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = tuple(
            PlanStep(
                id=s["id"], agent=s["agent"], intent=s["intent"], input=s.get("input", {}),
                depends_on=tuple(s.get("depends_on", ())), effect=s.get("effect", False),
                compensate=s.get("compensate"),
            )
            for s in data["steps"]
        )
        plan = ExecutionPlan(
            id=data["id"], version=data["version"], description=data["description"],
            triggers=tuple(data.get("triggers", ())), entry_scopes=tuple(data.get("entry_scopes", ())),
            steps=steps, output=data.get("output", {}),
        )
        _validate_effect_compensation(plan)
        plans[plan.id] = plan
    return plans


def _validate_effect_compensation(plan: ExecutionPlan) -> None:
    """Restriction 6: a step with effect=True and compensate=None must be the last step with an effect in the plan."""
    effect_steps = [s for s in plan.steps if s.effect]
    for index, step in enumerate(effect_steps):
        if step.compensate is None and index != len(effect_steps) - 1:
            raise PlanValidationError(
                f"plan '{plan.id}': step '{step.id}' has effect=True without compensate and is not the "
                "last step with an effect in the plan"
            )
