"""Execution policy contracts: what a tool/inference may do, and who decides."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from core.capabilities import ToolSpec


class PolicyScope(Enum):
    """Level at which a GuardrailPolicy is declared; the effective policy is the intersection of the three."""
    PLATFORM = "platform"
    AGENT = "agent"
    PLAN = "plan"


@dataclass(frozen=True)
class GuardrailPolicy:
    """Rules allowed in a scope: tool types, targets and call limits per step."""
    id: str
    scope: PolicyScope
    allow_tool_types: frozenset[str]
    allow_targets: frozenset[str]
    max_model_calls_per_step: int
    max_tool_calls_per_step: int


@dataclass(frozen=True)
class GuardrailDecision:
    """Verdict of a guardrail evaluation, with the rule and scope that determined it."""
    allowed: bool
    rule: str
    scope: str


class IGuardrailEvaluator(ABC):
    """Evaluates whether a tool or an inference call is allowed by the effective policy."""

    @abstractmethod
    def evaluate_tool(self, spec: ToolSpec, ctx: object) -> GuardrailDecision: ...

    @abstractmethod
    def evaluate_inference(self, ctx: object) -> GuardrailDecision: ...


class GuardrailWideningError(Exception):
    """Restriction 3 at load time: a narrower-scope policy widens beyond the broader one."""


class GuardrailDenied(Exception):
    """Negative decision at runtime; carries the GuardrailDecision that originated it."""

    def __init__(self, decision: GuardrailDecision):
        super().__init__(decision.rule)
        self.decision = decision
