"""Core boundary contracts — implemented by Infrastructure: connectors, inference and diagnostics."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.identity import Principal


@dataclass(frozen=True)
class ConnectorTarget:
    """Logical address of an external system (kind:name), used by tools and guardrails."""
    name: str
    kind: str

    def key(self) -> str:
        return f"{self.kind}:{self.name}"


class IConnector(ABC):
    """Synchronous bridge between a tool and a concrete external system."""

    @abstractmethod
    def invoke(self, target: ConnectorTarget, operation: str, payload: dict, principal: Principal) -> dict: ...


class ConnectorRejection(Exception):
    """Typed rejection raised by a SYSTEM_OF_RECORD target when a business invariant is violated."""

    def __init__(self, code: str, violation: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.violation = violation
        self.detail = detail


@dataclass(frozen=True)
class ModelRequest:
    """An inference call: messages, token budget and purpose (selection vs. generation).

    `response_schema` (JSON Schema, optional) constrains generation at the decoding
    level: when present, the provider MUST restrict the output to the schema (native
    structured output). Deliberate contract change (2026-07-27): the original port
    could not express constrained output — a real limitation of IModelClient as it
    stood, recorded in NOTES.md (Structured output correction).
    """
    messages: tuple[dict, ...]
    max_tokens: int = 1024
    purpose: str = "generation"
    metadata: dict = field(default_factory=dict)
    response_schema: dict | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Result of an inference call, with token usage for observability and cost."""
    content: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    model: str = ""


class IModelClient(ABC):
    """Synchronous boundary for any model provider (local, Anthropic, OpenAI)."""

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse: ...


@dataclass(frozen=True)
class ModelCallEvent:
    """A model call: token cost, cache and latency, for metrics and cost per task."""
    execution_id: str
    step_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    latency_ms: float
    purpose: str


@dataclass(frozen=True)
class StepEvent:
    """Execution of a plan step: agent, status and latency."""
    execution_id: str
    plan_id: str
    step_id: str
    agent: str
    status: str
    latency_ms: float


@dataclass(frozen=True)
class AuditEvent:
    """Audit record (never sampled). Kinds: "proposal" (what the agent/model proposed),
    "guardrail" (policy decision), "system_of_record" (business-system validation),
    "compensation" (per-step start/result/failure — incl. the orphaned state on failure —
    plus one "outcome" event per failing execution recording the final
    failed_clean/compensated/compensation_failed status, see `ExecutionResult.status`),
    "output_contract" (generation contract violation and retry_once_then_fail outcome)."""
    execution_id: str
    plan_id: str
    step_id: str
    principal_id: str
    kind: str
    detail: dict


class IObservabilityWriter(ABC):
    """Sink for metrics and tracing: model calls, steps and raw trace per execution."""

    @abstractmethod
    def model_call(self, e: ModelCallEvent) -> None: ...

    @abstractmethod
    def step(self, e: StepEvent) -> None: ...

    @abstractmethod
    def trace(self, execution_id: str, payload: dict) -> None: ...


class IAuditWriter(ABC):
    """Sink for business audit: complete, never sampled."""

    @abstractmethod
    def record(self, e: AuditEvent) -> None: ...
