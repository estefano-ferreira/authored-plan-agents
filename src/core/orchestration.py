"""Execution plan contracts: declared sequence of agent steps and its result."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlanStep:
    """A plan step: which agent, which intent (never capability/tool) and input templates."""
    id: str
    agent: str
    intent: str
    input: dict
    depends_on: tuple[str, ...] = ()
    effect: bool = False
    compensate: dict | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    """Declarative plan loaded from YAML: steps, input scopes and selection triggers."""
    id: str
    version: str
    description: str
    triggers: tuple[str, ...]
    entry_scopes: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    output: dict


@dataclass(frozen=True)
class Rejection:
    """Typed reason for refusing an execution, without raising an exception to the external caller."""
    code: str
    reason: str
    scope: str = ""


@dataclass
class ExecutionResult:
    """Result of handle(), with aggregated metrics. `status` is one of:
    - "completed" — every step ran to completion.
    - "rejected" — no plan resolved for the request (restriction 7); nothing ran.
    - "failed_clean" — a step failed with zero completed effect steps: nothing irreversible ran,
      so there was nothing to compensate (not to be confused with "compensated" — no compensation
      protocol actually ran).
    - "compensated" — a step failed after one or more effect steps had completed, and every
      compensation for those steps succeeded.
    - "compensation_failed" — a step failed after one or more effect steps had completed, and at
      least one compensation attempt itself failed (the business system is left with orphaned
      state; see `AuditEvent` kind="compensation" for the trail)."""
    execution_id: str
    status: str
    output: dict = field(default_factory=dict)
    rejection: Rejection | None = None
    metrics: dict = field(default_factory=dict)


class IOrchestrator(ABC):
    """The platform's single entry point: resolves the plan, issues identity and executes steps."""

    @abstractmethod
    def handle(self, request: dict) -> ExecutionResult: ...


class PlanValidationError(Exception):
    """Restriction 6 at load time: a step with an effect and no compensation must be the last one with an effect."""


class PlanNotFoundError(Exception):
    """Restriction 7: no plan resolved for the request (the orchestrator converts this into a Rejection)."""
