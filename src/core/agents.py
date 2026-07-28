"""Agent contracts: declarative specification, registry and execution runtime."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.memory import MemoryPolicy


@dataclass(frozen=True)
class AgentSpecification:
    """Declaration of an agent: business context, available capabilities and memory policy."""
    id: str
    owner: str
    business_context: str
    goal: str
    instructions: str
    capabilities: tuple[str, ...]
    memory_policy: MemoryPolicy
    spec_dir: str


class IAgentRegistry(ABC):
    """Catalog of AgentSpecifications loaded from the declarative specifications."""

    @abstractmethod
    def get(self, agent_id: str) -> AgentSpecification: ...

    @abstractmethod
    def all(self) -> tuple[AgentSpecification, ...]: ...


class IAgentRuntime(ABC):
    """Executes an agent invocation. ExecutionContext is defined in ai; here it is only a
    forwarded type name (forward reference), without import, so as not to invert the dependency."""

    @abstractmethod
    def execute(self, agent_id: str, intent: str, payload: dict, ctx: "ExecutionContext") -> dict: ...


class AgentCompositionError(Exception):
    """Restriction 2: an agent cannot invoke another agent (execution within execution)."""


class ChannelViolationError(Exception):
    """Restriction 4: execution not initiated by an ExecutionContext issued by the Orchestrator."""
