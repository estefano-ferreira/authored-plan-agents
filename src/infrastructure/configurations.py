"""Composition root: assembles the Platform (all collaborators) from declarative configuration and env.

The only place that knows the full assembly. Paths are resolved relative to this
file itself (not the cwd), so that `build_platform()` works from any directory
the process is started from (tests, scripts).
"""
import os
from dataclasses import dataclass
from pathlib import Path

from ai.agents import AgentRegistry, AgentRuntime, ToolRegistry
from ai.guardrails import GuardrailEngine, load_policy
from ai.orchestrator import Orchestrator, load_plans
from core.guardrails import PolicyScope
from core.orchestration import ExecutionPlan
from core.ports import IAuditWriter, IConnector, IModelClient
from infrastructure.audit.audit_store import JsonlAuditWriter, PostgresAuditWriter
from infrastructure.connectors.mcp.mcp_connector import McpConnector
from infrastructure.connectors.rest.rest_connector import RestConnector
from infrastructure.memory.knowledge_store import LocalVectorKnowledgeStore, PgVectorKnowledgeStore
from infrastructure.memory.session_store import PostgresSessionStore, build_session_store
from infrastructure.observability.console_writer import (
    CompositeObservabilityWriter,
    ConsoleObservabilityWriter,
)
from infrastructure.observability.langsmith_writer import LangSmithObservabilityWriter
from infrastructure.providers.extractor_repair_client import ExtractorRepairClient
from infrastructure.providers.fault_injection_client import GenerationFaultInjectionClient
from infrastructure.providers.local_client import LocalModelClient
from infrastructure.providers.recording_client import RecordingModelClient
from infrastructure.providers.schema_stripping_client import SchemaStrippingClient
from infrastructure.providers.tolerant_repair_client import TolerantRepairClient

# .../src/infrastructure/configurations.py -> parents[1] == .../src
_SRC_DIR = Path(__file__).resolve().parents[1]
_AI_DIR = _SRC_DIR / "ai"
_SPECS_DIR = _AI_DIR / "agents"
_PLANS_DIR = _AI_DIR / "plans"
_PLATFORM_GUARDRAIL_PATH = _AI_DIR / "guardrail.yaml"

_DEFAULT_ERP_BASE_URL = "http://127.0.0.1:8123"


@dataclass
class Platform:
    """All platform collaborators, assembled once by the composition root."""
    orchestrator: Orchestrator
    registry: AgentRegistry
    runtime: AgentRuntime
    guardrails: GuardrailEngine
    tools: ToolRegistry
    model: IModelClient
    connectors: dict[str, IConnector]
    session_store: object
    knowledge_store: object
    obs: CompositeObservabilityWriter
    console_obs: ConsoleObservabilityWriter
    langsmith_obs: LangSmithObservabilityWriter
    audit: IAuditWriter
    plans: dict[str, ExecutionPlan]
    model_log: list
    # Whichever repair instrument was wired in (see `build_platform`'s `tolerant_repair` /
    # `extractor_repair` params -- mutually exclusive, so at most one applies): a
    # `TolerantRepairClient`, an `ExtractorRepairClient`, or None when neither flag is True --
    # exposed so callers (the integrity-matrix runner) can read `.repairs` for post-hoc counting
    # without reaching into the `model` decorator chain themselves.
    repair_client: TolerantRepairClient | ExtractorRepairClient | None


def _build_model(provider: str) -> IModelClient:
    """Instantiates the IModelClient for the chosen provider; real SDK imports stay lazy (inside the client)."""
    if provider == "local":
        return LocalModelClient()
    if provider == "anthropic":
        from infrastructure.providers.anthropic_client import AnthropicModelClient

        return AnthropicModelClient()
    if provider == "openai":
        from infrastructure.providers.openai_client import OpenAIModelClient

        return OpenAIModelClient()
    if provider == "gemini":
        from infrastructure.providers.gemini_client import GeminiModelClient

        return GeminiModelClient()
    if provider == "meta":
        from infrastructure.providers.meta_client import MetaModelClient

        return MetaModelClient()
    if provider == "openrouter":
        from infrastructure.providers.openrouter_client import OpenRouterModelClient

        return OpenRouterModelClient()
    if provider == "groq":
        from infrastructure.providers.groq_client import GroqModelClient

        return GroqModelClient()
    raise ValueError(
        f"unknown provider: {provider!r} (expected: local|anthropic|openai|gemini|meta|openrouter|groq)"
    )


def build_platform(
    provider: str | None = None,
    erp_transport=None,
    audit_path: str | Path | None = None,
    *,
    record_model_calls: bool = False,
    inject_generation_fault: bool = False,
    strip_response_schema: bool = False,
    tolerant_repair: bool = False,
    extractor_repair: bool = False,
    measurement_mode: bool = False,
) -> Platform:
    """Composition root: assembles orchestrator + registries + runtime + guardrails + connectors + stores.

    `provider`: "local" | "anthropic" | "openai" | "gemini" | "meta" | "openrouter" | "groq"; if omitted, uses env `AI_PROVIDER` (default "local").
    `erp_transport`: in-process ASGI transport (e.g. `httpx.ASGITransport`) for the ERP/scheduling
    RestConnector; if omitted, uses a real `base_url` via env `ERP_BASE_URL` (default `http://127.0.0.1:8123`).
    `audit_path`: path to the audit JSONL; if omitted, uses `JsonlAuditWriter`'s default
    (lets tests isolate the file under `tmp_path`). Takes precedence over env `PLATFORM_DATABASE_URL`:
    passing it explicitly (as tests do) always selects the JSONL writer, even when Postgres is configured.
    Env `PLATFORM_DATABASE_URL`: when set (and `audit_path` was not passed), session/knowledge/audit
    persistence moves to Postgres (`PostgresSessionStore` + `PgVectorKnowledgeStore` +
    `PostgresAuditWriter`); the schema is created on first connection (`create_all`, checkfirst). A
    connection failure at that point is not caught here — persistence was explicitly requested and
    being unavailable must fail loudly, not fall back to the in-memory/JSONL stores.
    `record_model_calls`: when True, wraps the model client in a `RecordingModelClient` before it is
    injected into the runtime/orchestrator, so every `complete()` call (selection and generation) is
    logged for post-hoc analysis; the log is exposed as `Platform.model_log` (empty list otherwise).
    `inject_generation_fault`: measurement instrumentation only (see
    `infrastructure/providers/fault_injection_client.py`; rejected unless `measurement_mode=True`)
    -- when True, wraps the model client in a `GenerationFaultInjectionClient` that re-fences every
    `purpose=="generation"` response in markdown JSON fences, reproducing on purpose the "output
    contract guard catches a provider-side envelope surprise" cell of the integrity matrix (NOTES.md,
    "Structured output correction").
    `strip_response_schema`: measurement instrumentation only (see
    `infrastructure/providers/schema_stripping_client.py`; rejected unless `measurement_mode=True`)
    -- when True, wraps the model client in a `SchemaStrippingClient` that intercepts every REQUEST
    and forces `response_schema=None`, emulating an "enforcement=none" provider configuration
    without touching the capability that declared the schema.
    `tolerant_repair`: measurement instrumentation only (see
    `infrastructure/providers/tolerant_repair_client.py`; rejected unless `measurement_mode=True`)
    -- when True, wraps the model client in a `TolerantRepairClient` that intercepts non-conforming
    `purpose=="generation"` RESPONSES and replaces their content with a byte-faithful replica of
    the historical tolerant fallback removed from `read_and_reply.py`, so the capability's strict
    guard then accepts it. The wired instance (or None) is exposed as `Platform.repair_client` so
    callers can read `.repairs` for post-hoc counting.
    `extractor_repair`: measurement instrumentation only (see
    `infrastructure/providers/extractor_repair_client.py`; rejected unless `measurement_mode=True`)
    -- when True, wraps the model client in an `ExtractorRepairClient` that intercepts non-conforming
    `purpose=="generation"` RESPONSES and, if `content` matches a single markdown fence envelope,
    replaces it with the fenced-out inner text (one pass, no recursion) -- the practitioner
    "code fence removal" repair strategy, deliberately narrower than `tolerant_repair`'s absorbing
    fallback. Mutually exclusive with `tolerant_repair` (`ValueError` if both are True: only one
    repair instrument can be wired at a time). The wired instance (or None) is exposed as
    `Platform.repair_client`, same field `tolerant_repair` uses.
    `measurement_mode`: the composition-time gate for the four instrumentation flags above
    (SPECIFICATION.md § 5.1: production composition must not be able to wire a repair or fault
    decorator onto the write path). Default False. If any of `inject_generation_fault`,
    `strip_response_schema`, `tolerant_repair`, `extractor_repair` is True while `measurement_mode`
    is False, `build_platform` raises `ValueError` naming the offending flag -- before any other
    collaborator is assembled. Callers that need instrumentation (the integrity-matrix runner,
    measurement-mode `scripts/run_plans.py`) must pass `measurement_mode=True` explicitly; nothing
    else changes when it is True -- it is a pure permission gate, not a behavior switch.

    Wiring order (all four instrumentation flags together, integrity-matrix cell "A"):
    `Recorder(TolerantRepair(FaultInjection(SchemaStrip(base))))` -- i.e. a REQUEST flows outside
    in (Recorder -> TolerantRepair -> FaultInjection -> SchemaStrip -> base) and a RESPONSE flows
    inside out (base -> SchemaStrip [request-only, response passes through] -> FaultInjection
    [corrupts] -> TolerantRepair [repairs] -> Recorder). This ordering is deliberate: the recorder
    is always the outermost layer, so `Platform.model_log`/the audit trail record exactly what the
    capability's own parser saw -- post-injection, post-repair -- never the underlying provider's
    original, unmodified output.
    """
    if tolerant_repair and extractor_repair:
        raise ValueError("tolerant_repair and extractor_repair are mutually exclusive repair instruments")

    # SPECIFICATION 5.1 (output-contract enforcement on the write path): repair-before-check must be
    # structurally impossible in production composition, not merely discouraged by docstring warning.
    # Checked before any other collaborator is assembled -- nothing beyond arguments has been touched
    # yet, so a rejection here has zero side effects.
    if not measurement_mode:
        instrumentation_flags = {
            "inject_generation_fault": inject_generation_fault,
            "strip_response_schema": strip_response_schema,
            "tolerant_repair": tolerant_repair,
            "extractor_repair": extractor_repair,
        }
        for flag_name, flag_value in instrumentation_flags.items():
            if flag_value:
                raise ValueError(
                    f"measurement instrumentation ({flag_name}) requires measurement_mode=True; "
                    "production composition rejects repair or fault decorators on the write path "
                    "-- SPECIFICATION 5.1"
                )

    provider = provider or os.environ.get("AI_PROVIDER", "local")
    model = _build_model(provider)
    # Instrumentation wiring order -- see the docstring above ("Wiring order"): request flows
    # outside in, response flows inside out, so each decorator is layered on top of the previous
    # one in exactly this sequence (closest to the base model first).
    if strip_response_schema:
        model = SchemaStrippingClient(model)
    if inject_generation_fault:
        model = GenerationFaultInjectionClient(model)
    repair_client: TolerantRepairClient | ExtractorRepairClient | None = None
    if tolerant_repair:
        repair_client = TolerantRepairClient(model)
        model = repair_client
    elif extractor_repair:
        repair_client = ExtractorRepairClient(model)
        model = repair_client
    model_log: list = []
    if record_model_calls:
        recorder = RecordingModelClient(model)
        model = recorder
        model_log = recorder.log

    registry = AgentRegistry(_SPECS_DIR)
    tools = ToolRegistry()
    for spec in registry.all():
        tools.load_capability_tools(spec.spec_dir)

    platform_database_url = os.environ.get("PLATFORM_DATABASE_URL")

    if audit_path is not None:
        audit: IAuditWriter = JsonlAuditWriter(audit_path)
    elif platform_database_url:
        audit = PostgresAuditWriter(platform_database_url)
    else:
        audit = JsonlAuditWriter()

    platform_policy = load_policy(_PLATFORM_GUARDRAIL_PATH, PolicyScope.PLATFORM)
    agent_policies = {
        spec.id: load_policy(Path(spec.spec_dir) / "guardrail.yaml", PolicyScope.AGENT)
        for spec in registry.all()
    }
    plans = load_plans(_PLANS_DIR)
    plan_policies: dict = {}  # no plan in the repository declares its own guardrail today
    guardrails = GuardrailEngine(platform_policy, agent_policies, plan_policies, audit)

    if erp_transport is not None:
        rest_connector = RestConnector(transport=erp_transport)
    else:
        erp_base_url = os.environ.get("ERP_BASE_URL", _DEFAULT_ERP_BASE_URL)
        rest_connector = RestConnector(base_url=erp_base_url)
    mcp_connector = McpConnector()
    connectors: dict[str, IConnector] = {"rest": rest_connector, "mcp": mcp_connector}

    if platform_database_url:
        session_store = PostgresSessionStore(platform_database_url)
        knowledge_store = PgVectorKnowledgeStore(platform_database_url)
    else:
        session_store = build_session_store(os.environ.get("REDIS_URL"))
        knowledge_store = LocalVectorKnowledgeStore()

    console_obs = ConsoleObservabilityWriter()
    # Built after the recorder above (`model_log` is the recorder's live list, or `[]`/unused
    # when `record_model_calls` is False) so prompt/response correlation can start right away.
    langsmith_obs = LangSmithObservabilityWriter(model_log=model_log if record_model_calls else None)
    obs = CompositeObservabilityWriter(console_obs, langsmith_obs)

    runtime = AgentRuntime(
        registry, tools, guardrails, model, connectors, session_store, knowledge_store, obs, audit,
    )
    orchestrator = Orchestrator(plans, registry, runtime, guardrails, model, session_store, obs, audit)

    return Platform(
        orchestrator=orchestrator,
        registry=registry,
        runtime=runtime,
        guardrails=guardrails,
        tools=tools,
        model=model,
        connectors=connectors,
        session_store=session_store,
        knowledge_store=knowledge_store,
        obs=obs,
        console_obs=console_obs,
        langsmith_obs=langsmith_obs,
        audit=audit,
        plans=plans,
        model_log=model_log,
        repair_client=repair_client,
    )
