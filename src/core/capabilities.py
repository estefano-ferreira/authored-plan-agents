"""Capability contracts: tool classification, specification and step sequence."""
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from core.ports import ConnectorTarget


class ToolType(Enum):
    """Classifies the guarantee a tool offers about business state."""
    SYSTEM_OF_RECORD = "system_of_record"
    READ_ONLY = "read_only"
    IRREVERSIBLE = "irreversible"


class StepKind(Enum):
    """Distinguishes a step that calls a tool from a step that only invokes the model."""
    TOOL = "tool"
    INFERENCE = "inference"


@dataclass(frozen=True)
class Step:
    """A hardcoded step (never decided by the model) within a capability."""
    id: str
    kind: StepKind = StepKind.TOOL
    tool: str | None = None
    inference: str | None = None
    depends_on: tuple[str, ...] = ()
    compensate: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """Classification and execution metadata of a tool, attached by the @tool decorator."""
    name: str
    tool_type: ToolType
    target: ConnectorTarget
    writes_business_state: bool
    fn: Callable


def tool(tool_type: ToolType, target: ConnectorTarget, writes_business_state: bool | None = None):
    """Attaches a ToolSpec to fn.__tool_spec__; classification validation happens in the registry (ai)."""
    def decorator(fn: Callable) -> Callable:
        writes = writes_business_state
        if writes is None:
            writes = tool_type is ToolType.SYSTEM_OF_RECORD
        fn.__tool_spec__ = ToolSpec(
            name=fn.__name__,
            tool_type=tool_type,
            target=target,
            writes_business_state=writes,
            fn=fn,
        )
        return fn
    return decorator


class Capability:
    """Declarative base: subclass fixes id, description and the sequence of executed steps."""
    id: str
    description: str
    steps: tuple[Step, ...]


class ToolClassificationError(Exception):
    """Raised by ai.agents.ToolRegistry when a tool's classification violates restriction 1."""
