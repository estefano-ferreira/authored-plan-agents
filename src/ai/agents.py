"""Agent registry, execution runtime, tool registry and dynamic module loading, plus the
declarative agent specifications loaded from `ai/agents/` (a DATA folder alongside this module).

Module/folder coexistence: `ai/agents/` holds no `__init__.py` at any level (it is pure data --
per-agent YAML and capability `.py` files, not a Python package). Per PEP 420, a directory without
`__init__.py` is only ever considered as a *namespace package*, and namespace packages are always
tried last by the import system, after every regular module/package match. So `import ai.agents`
resolves to this file (`ai/agents.py`), never to the sibling directory; the directory's contents
are reached exclusively through explicit file paths (`load_module`, `AgentRegistry`), never via
`import ai.agents.<something>`. Deleting `ai/agents/__init__.py` is what makes this resolution
possible -- if it were still a regular package, it would shadow this module instead.

Capability id -> file name mapping: kebab-case -> snake_case (e.g. "read-and-reply" ->
"read_and_reply.py"), implemented in `_capability_file_name`. Each capability file under an
agent's data folder (`ai/agents/<agent>/<capability>.py`) now bundles together: the `Capability`
subclass, the tool functions it owns (`@tool`-decorated, when the capability defines any), and the
`ConnectorTarget`s those tools invoke, defined inline as local frozen value objects (there is no
shared `targets.py` anymore). A capability that only reuses tools physically defined in a sibling
file (e.g. `reschedule.py` referencing tools defined in `book.py`) declares no tool functions or
targets of its own -- only `Step.tool` references by name.
"""
import contextvars
import importlib.util
import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import yaml

from core.agents import (
    AgentCompositionError,
    AgentSpecification,
    ChannelViolationError,
    IAgentRegistry,
    IAgentRuntime,
)
from core.capabilities import Capability, StepKind, ToolClassificationError, ToolSpec, ToolType
from core.memory import MemoryPolicy
from core.ports import AuditEvent, ModelCallEvent, ModelRequest

from ai.context import ContextBuilder

_reentrancy = contextvars.ContextVar("ai_agent_runtime_active", default=False)

_module_cache: dict[str, ModuleType] = {}


def load_module(path: str | Path) -> ModuleType:
    """Loads (cached by absolute path) the module at `path`, executing it exactly once.

    Used to load an agent's declarative capability files (`ai/agents/<agent>/<capability>.py`),
    whose data folder does not form a conventional Python package.
    """
    resolved = Path(path).resolve()
    key = str(resolved)
    cached = _module_cache.get(key)
    if cached is not None:
        return cached
    mod_name = f"_ai_dyn_{abs(hash(key))}_{resolved.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load module at '{resolved}'")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _module_cache[key] = module
    return module


def _capability_file_name(capability_id: str) -> str:
    """Maps a kebab-case capability id (e.g. 'read-and-reply') to its snake_case file ('read_and_reply.py')."""
    return capability_id.replace("-", "_") + ".py"


class AgentRegistry(IAgentRegistry):
    """Catalog of AgentSpecification, loaded once by scanning `ai/agents/*/agent.yaml` (data folders)."""

    def __init__(self, specs_dir: str | Path):
        self._specs: dict[str, AgentSpecification] = {}
        for agent_dir in sorted(Path(specs_dir).iterdir()):
            agent_yaml = agent_dir / "agent.yaml"
            if not agent_dir.is_dir() or not agent_yaml.is_file():
                continue
            spec = self._load(agent_dir, agent_yaml)
            self._specs[spec.id] = spec

    @staticmethod
    def _load(agent_dir: Path, agent_yaml: Path) -> AgentSpecification:
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
        mp = data.get("memory_policy", {})
        session = mp.get("session", {})
        knowledge = mp.get("knowledge", {})
        memory_policy = MemoryPolicy(
            session_last_n_turns=session.get("last_n_turns", 10),
            session_ttl_seconds=session.get("ttl_seconds", 1800),
            knowledge_enabled=knowledge.get("enabled", False),
            knowledge_top_k=knowledge.get("top_k", 3),
        )
        return AgentSpecification(
            id=data["id"], owner=data["owner"], business_context=data["business_context"],
            goal=data["goal"], instructions=data["instructions"],
            capabilities=tuple(data.get("capabilities", ())),
            memory_policy=memory_policy, spec_dir=str(agent_dir),
        )

    def get(self, agent_id: str) -> AgentSpecification:
        return self._specs[agent_id]

    def all(self) -> tuple[AgentSpecification, ...]:
        return tuple(self._specs.values())


class ToolRegistry:
    """Catalog of ToolSpec indexed by name, with classification validation at registration time."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, fn_or_spec) -> ToolSpec:
        """Registers a tool (function decorated with @tool or a direct ToolSpec), validating restriction 1."""
        spec = fn_or_spec if isinstance(fn_or_spec, ToolSpec) else getattr(fn_or_spec, "__tool_spec__", None)
        if spec is None:
            raise ToolClassificationError(f"'{fn_or_spec!r}' is not a tool (no __tool_spec__)")
        if spec.writes_business_state and spec.tool_type is not ToolType.SYSTEM_OF_RECORD:
            raise ToolClassificationError(
                f"tool '{spec.name}': writes_business_state=True requires tool_type=SYSTEM_OF_RECORD"
            )
        if spec.tool_type is ToolType.SYSTEM_OF_RECORD and spec.target.kind in {"memory", "database"}:
            raise ToolClassificationError(
                f"tool '{spec.name}': SYSTEM_OF_RECORD target cannot have kind='{spec.target.kind}'"
            )
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        """Retrieves an already-registered ToolSpec by the function's name."""
        return self._tools[name]

    def load_capability_tools(self, spec_dir: str) -> None:
        """Imports every `*.py` in an agent's data folder and registers each function carrying `__tool_spec__`.

        Capability and tool code now live side by side per file (e.g. `book.py`), so scanning the
        agent's own folder directly (there is no more `capabilities/` subfolder) picks up every
        tool the agent owns, including tools reused by a sibling capability that defines none.
        """
        agent_dir = Path(spec_dir)
        if not agent_dir.is_dir():
            return
        for py_file in sorted(agent_dir.glob("*.py")):
            module = load_module(py_file)
            for value in vars(module).values():
                if getattr(value, "__tool_spec__", None) is not None:
                    self.register(value)


class CapabilitySelectionError(Exception):
    """Invalid capability selection (model response outside the agent's catalog) or malformed capability."""


def _find_capability_class(module) -> type[Capability]:
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, Capability) and value is not Capability:
            return value
    raise CapabilitySelectionError(f"no Capability class found in '{module.__file__}'")


def _select_kwargs(fn, payload: dict) -> dict:
    """kwargs = intersection of the tool's named parameters with `payload`'s keys (includes 'results')."""
    params = list(inspect.signature(fn).parameters.values())[1:]  # drops the first one (ctx)
    names = [p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    return {name: payload[name] for name in names if name in payload}


@dataclass
class ToolContext:
    """Local context passed to each tool/inference: identity, collaborators and observability coordinates."""
    principal: object
    connectors: dict
    model: object
    obs: object
    audit: object
    execution_id: str
    step_id: str
    plan_id: str
    agent_spec: object
    session_id: str
    context_builder: object
    session_store: object


class _MeasuringModel:
    """Wraps the real IModelClient, measuring latency around `complete` and emitting a ModelCallEvent."""

    def __init__(self, model, obs, execution_id: str, step_id: str):
        self._model = model
        self._obs = obs
        self._execution_id = execution_id
        self._step_id = step_id

    def complete(self, request: ModelRequest):
        started = time.monotonic()
        response = self._model.complete(request)
        latency_ms = (time.monotonic() - started) * 1000
        self._obs.model_call(ModelCallEvent(
            execution_id=self._execution_id, step_id=self._step_id, model=response.model,
            input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens, latency_ms=latency_ms, purpose=request.purpose,
        ))
        return response


class AgentRuntime(IAgentRuntime):
    """Executes an agent invocation: selects the capability and runs its steps in the order hardcoded in Python."""

    def __init__(self, registry, tools, guardrails, model, connectors, session_store, knowledge_store, obs, audit):
        self.registry = registry
        self.tools = tools
        self.guardrails = guardrails
        self.model = model
        self.connectors = connectors
        self.session_store = session_store
        self.knowledge_store = knowledge_store
        self.obs = obs
        self.audit = audit
        self.context_builder = ContextBuilder(session_store, knowledge_store)

    def execute(self, agent_id: str, intent: str, payload: dict, ctx) -> dict:
        if _reentrancy.get():
            raise AgentCompositionError(f"agent '{agent_id}' invoked from within another agent execution")
        if not getattr(ctx, "issued_by_orchestrator", False) or ctx.principal is None:
            raise ChannelViolationError(
                f"execution of '{agent_id}' outside the Orchestrator (or without a principal); only the Orchestrator issues ExecutionContext"
            )
        token = _reentrancy.set(True)
        try:
            return self._execute(agent_id, intent, payload, ctx)
        finally:
            _reentrancy.reset(token)

    def _execute(self, agent_id: str, intent: str, payload: dict, ctx) -> dict:
        spec = self.registry.get(agent_id)
        evaluator = self.guardrails.evaluator_for(agent_id, ctx.plan.id)
        capability_cls, module = self._select_capability(spec, intent, ctx)
        step_results: dict = {}
        completed_tool_steps = []
        try:
            for step in capability_cls.steps:
                tool_ctx = ToolContext(
                    principal=ctx.principal, connectors=self.connectors,
                    model=_MeasuringModel(self.model, self.obs, ctx.execution_id, step.id),
                    obs=self.obs, audit=self.audit, execution_id=ctx.execution_id, step_id=step.id,
                    plan_id=ctx.plan.id, agent_spec=spec, session_id=ctx.session_id,
                    context_builder=self.context_builder, session_store=self.session_store,
                )
                merged_payload = {**payload, "results": dict(step_results)}
                if step.kind is StepKind.TOOL:
                    tool_spec = self.tools.get(step.tool)
                    evaluator.evaluate_tool(tool_spec, tool_ctx)
                    kwargs = _select_kwargs(tool_spec.fn, merged_payload)
                    result = tool_spec.fn(tool_ctx, **kwargs)
                    if tool_spec.tool_type.name == "SYSTEM_OF_RECORD":
                        self.audit.record(AuditEvent(
                            execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id=step.id,
                            principal_id=ctx.principal.id, kind="system_of_record",
                            detail={"tool": tool_spec.name, "result": result},
                        ))
                    completed_tool_steps.append(step)
                else:
                    evaluator.evaluate_inference(tool_ctx)
                    fn = getattr(module, step.inference)
                    result = fn(tool_ctx, merged_payload)
                    self.audit.record(AuditEvent(
                        execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id=step.id,
                        principal_id=ctx.principal.id, kind="proposal",
                        detail={"inference": step.inference, "result": result},
                    ))
                step_results[step.id] = result
        except Exception:
            self._compensate(completed_tool_steps, step_results, ctx, spec)
            raise
        output = self._merge(step_results)
        self.session_store.append(ctx.session_id, {
            "intent": intent, "capability": capability_cls.id, "result_summary": output,
        })
        return output

    def _select_capability(self, spec, intent: str, ctx):
        """Confined non-determinism: the model chooses the capability among the agent's; never the step order."""
        loaded = {}
        for capability_id in spec.capabilities:
            path = Path(spec.spec_dir) / _capability_file_name(capability_id)
            module = load_module(path)
            loaded[capability_id] = (_find_capability_class(module), module)
        catalog = "\n".join(f"- {cid}: {cls.description}" for cid, (cls, _m) in loaded.items())
        prompt = (
            "Choose the id of the capability best suited to the intent below, or answer exactly 'none' "
            f"if none applies.\nIntent: {intent}\n\nCapabilities of '{spec.id}':\n{catalog}"
        )
        measuring_model = _MeasuringModel(self.model, self.obs, ctx.execution_id, "selection")
        response = measuring_model.complete(
            ModelRequest(messages=({"role": "user", "content": prompt},), purpose="selection")
        )
        chosen = response.content.strip()
        self.audit.record(AuditEvent(
            execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id="selection",
            principal_id=ctx.principal.id, kind="proposal",
            detail={"intent": intent, "chosen": chosen, "options": list(loaded.keys())},
        ))
        if chosen not in loaded:
            raise CapabilitySelectionError(
                f"invalid capability selection for agent '{spec.id}': response '{chosen}'"
            )
        return loaded[chosen]

    def _compensate(self, completed_tool_steps, step_results: dict, ctx, spec) -> None:
        """Intra-capability compensation: runs `Step.compensate` of the completed steps, in reverse order.

        Best-effort: a compensation failure here never masks the original exception (re-raised by
        the caller) -- but it is now audited (kind="compensation") instead of silently swallowed,
        mirroring `Orchestrator._compensate_and_fail`'s "start"/"result"/"failed" vocabulary and its
        `step_id=f"{step.id}:compensate"` convention.
        """
        for step in reversed(completed_tool_steps):
            if not step.compensate:
                continue
            try:
                tool_spec = self.tools.get(step.compensate)
            except KeyError:
                continue
            compensate_step_id = f"{step.id}:compensate"
            tool_ctx = ToolContext(
                principal=ctx.principal, connectors=self.connectors,
                model=_MeasuringModel(self.model, self.obs, ctx.execution_id, compensate_step_id),
                obs=self.obs, audit=self.audit, execution_id=ctx.execution_id, step_id=compensate_step_id,
                plan_id=ctx.plan.id, agent_spec=spec, session_id=ctx.session_id,
                context_builder=self.context_builder, session_store=self.session_store,
            )
            kwargs = _select_kwargs(tool_spec.fn, {**step_results, "results": dict(step_results)})
            self.audit.record(AuditEvent(
                execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id=compensate_step_id,
                principal_id=ctx.principal.id, kind="compensation",
                detail={"action": "start", "step": step.id, "tool": tool_spec.name},
            ))
            try:
                result = tool_spec.fn(tool_ctx, **kwargs)
                self.audit.record(AuditEvent(
                    execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id=compensate_step_id,
                    principal_id=ctx.principal.id, kind="compensation",
                    detail={"action": "result", "step": step.id, "tool": tool_spec.name, "result": result},
                ))
            except Exception as exc:
                self.audit.record(AuditEvent(
                    execution_id=ctx.execution_id, plan_id=ctx.plan.id, step_id=compensate_step_id,
                    principal_id=ctx.principal.id, kind="compensation",
                    detail={
                        "action": "failed", "step": step.id, "tool": tool_spec.name, "error": str(exc),
                        "orphaned_state": step_results.get(step.id),
                    },
                ))
                # best effort: does not mask the original exception, which is re-raised by the caller

    @staticmethod
    def _merge(step_results: dict) -> dict:
        """Capability result: merge of all steps' results (later steps take precedence)."""
        merged: dict = {}
        for result in step_results.values():
            if isinstance(result, dict):
                merged.update(result)
        return merged
