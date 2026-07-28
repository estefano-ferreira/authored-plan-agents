# DESIGN — contracts and decisions (source of truth for the implementation)

Reference implementation of **Authored-Plan Agents**, an architectural pattern
for LLM agent platforms with separated execution, policy, and domain
authority: **sequence** is decided by a human (authored plan + capability
code), **authorization** by policy (three-scope guardrails, intersection),
**validity** by the domain (`SYSTEM_OF_RECORD` targets own their invariants;
the platform holds no business entity). The seven restrictions are this
separation made checkable.
This file pins down **names, signatures and rules**. Implementers do not invent
abstractions beyond it. Divergence = bug. The pattern's technology-independent
rules are extracted in [`SPECIFICATION.md`](SPECIFICATION.md) (draft): where
both state the same rule, the specification carries the pattern-level wording
and this file adds the binding to this implementation.

Python: 3.14 (venv in `.venv`). Style: **synchronous** code (async→sync bridges
live inside the connectors). No pydantic in `core` (pure stdlib: `dataclasses`,
`enum`, `abc`, `typing`).

## Motivation — the problem this pattern re-solves (recorded 2026-07-28)

The write-path discipline here is not a new idea — it is an old, solved one
("clients don't write the database; the system that owns the rules does",
n-tier, thin client / smart server) **being un-solved by agent-era wiring**:

- The easiest way to connect an agent in 2026 is an MCP server wrapping a
  database directly; the MCP security literature documents the consequence as
  standard risk — direct data pipelines, business-logic bypass, broad write
  scopes approved by default
  ([Supabase, defense-in-depth for MCP](https://supabase.com/blog/defense-in-depth-mcp);
  [WitnessAI](https://witness.ai/blog/mcp-server-security/);
  [Reco](https://www.reco.ai/learn/mcp-security)). Agent memory quietly
  becomes a shadow record; greenfield platforms hold orders and tickets in
  their own store "for now".
- The incident record shows what that wiring costs.
  [Cyera's analysis](https://www.cyera.com/research/agent-inflicted-damage-inside-the-real-world-failures-of-enterprise-ai-systems)
  of 7,246 public AI incidents (2023-09 → 2026-05) counts **188 in which an
  autonomous system harmed production with no attacker in the chain** —
  legitimate tokens, approved operations. Named cases: Replit (jul/2025,
  agent ignored a code freeze and deleted a live production DB) and
  [PocketOS](https://zenity.io/blog/current-events/ai-agent-database-deletion-pocketos)
  (abr/2026, production DB and its backups, seconds). The shared structural
  feature: **a write path to state that did not pass through a validating
  business API.** No ERP accepts "drop the table" as a business operation.
- The everyday, non-catastrophic version is measured false success — the
  agent's narrative diverging from the record — at 45–78% of failures in
  single-control settings ([arXiv 2606.09863](https://arxiv.org/abs/2606.09863)).

The pattern's answer is two mechanisms with **distinct jobs** (measured
distinct by the integrity matrix — see NOTES):

1. `SYSTEM_OF_RECORD` classification + the thin-platform rule (no business
   entity on the platform side): invalid *business operations* cannot happen
   and shadow state cannot accumulate, because the only writer of business
   truth is the system that owns the invariants — checked at registration.
2. The output-contract guard (`retry_once_then_fail`): corrupt *content*
   inside a valid operation fails clean instead of persisting — because the
   system of record validates its invariants, not content quality. Content
   integrity is orthogonal to authority enforcement.

Honest scope: where enterprise APIs already force server-side validation, the
classification is redundant-by-construction — it names why the existing
default is right and makes it checkable, so agent wiring doesn't undo it. It
is load-bearing where the default does not exist: greenfield platforms and
MCP-era connections.

## Architectural guarantees (given the seven restrictions + the output-contract guard; each with its evidence)

- **Deterministic execution order** — the model selects and generates, never
  sequences; model-call count per execution is **bounded** and known per plan
  (the strict guard adds at most one retry call per violation).
  Evidence: restriction tests 2/6; the bounded per-plan call shape in every
  recorded run (`results/runs/run-*.json`, `results/integrity-matrix/integrity_matrix.jsonl`).
- **Business validity decided outside the platform** — no business entity
  platform-side. Evidence: restriction 1 tests; persistence tests (no platform
  path to business storage); `erp_service/` holds the only business tables.
- **No policy composition widens permission.** Evidence: restriction 3 test
  (intersection-only; widening rejected at load).
- **Business state written only through the invariant-owning system**, whose
  rejection reaches the agent typed. Evidence: restriction 1 + persistence
  test (ERP 422 → `ConnectorRejection`).
- **Output-contract failure never persists unvalidated content.** Evidence:
  integrity-matrix cell B (`failed_clean` 10/10, zero rows); the dialect
  accident (10/10 typed, zero rows, `results/runs/run-20260727-215429.json`).
- **Every decision auditable, unsampled** — five kinds incl. compensation and
  its own failure with orphaned state. Evidence:
  `test_compensation_failure_is_audited_with_orphaned_state`; audit trails in
  `results/runs/` and `results/integrity-matrix/`.

Scope: architectural guarantees under the stated restrictions — not a security
boundary in-process (NOTES bend #5), not content quality inside a valid schema
(bounded by the output-contract guard; measured by the matrix).

## Layers and dependencies (verified by test)

- `core` — imports **stdlib only**. No import of `ai`, `infrastructure` or 3rd-party.
- `ai` — imports `core` + stdlib + `yaml` (the single 3rd-party exception, for declarative loading).
- `infrastructure` — imports `core`, `ai` and any 3rd-party.
- src layout: top-level packages `core`, `ai`, `infrastructure` under `src/` (pyproject, setuptools, src-layout).

## core/ — contracts

Layout: **7 files** — `identity.py`, `capabilities.py`, `agents.py`, `orchestration.py`,
`guardrails.py`, `memory.py`, `ports.py`. The former `inference.py`, `connectors.py`
and `diagnostics.py` **collapse into `ports.py`**: they are boundary contracts
implemented by Infrastructure, and grouping them makes that distinction visible in
the layout. Core holds contracts AND models (`AgentSpecification`, `ExecutionPlan`
are data that cross the layers — if they were born in `ai/`, Infrastructure would
depend on `ai/`, inverting the direction).

### core/identity.py
```python
@dataclass(frozen=True)
class Principal:
    id: str
    type: str            # "user" | "system"
    scopes: tuple[str, ...]

class SystemPrincipal(Principal):
    # factory: SystemPrincipal.with_scopes(("erp:write",)) -> Principal(id="system", type="system", scopes=...)
```

### core/capabilities.py
```python
class ToolType(Enum):
    SYSTEM_OF_RECORD = "system_of_record"  # business system that guarantees invariants; every write path
    READ_ONLY = "read_only"           # read without transactional state
    IRREVERSIBLE = "irreversible"     # action without guaranteed rollback

class StepKind(Enum):
    TOOL = "tool"
    INFERENCE = "inference"           # model call (interpret/draft)

@dataclass(frozen=True)
class Step:
    id: str
    kind: StepKind = StepKind.TOOL
    tool: str | None = None           # tool function name (kind=TOOL)
    inference: str | None = None      # inference function name in the capability module (kind=INFERENCE)
    depends_on: tuple[str, ...] = ()
    compensate: str | None = None     # compensating tool name (or None)

@dataclass(frozen=True)
class ToolSpec:
    name: str
    tool_type: ToolType
    target: "ConnectorTarget"         # from core.ports
    writes_business_state: bool
    fn: Callable                      # fn(ctx: ToolContext, **kwargs) -> dict

def tool(tool_type, target, writes_business_state=None):  # decorator
    # default writes_business_state: True if SYSTEM_OF_RECORD, else False.
    # attaches ToolSpec at fn.__tool_spec__

class Capability:                     # declarative base; subclass defines id, description, steps
    id: str
    description: str
    steps: tuple[Step, ...]

class ToolClassificationError(Exception): ...
```
Classification errors (restriction 1) are raised by `ai/agents.py`'s `ToolRegistry` at registration.

### core/ports.py — boundary contracts (implemented by Infrastructure)

Connectors:
```python
@dataclass(frozen=True)
class ConnectorTarget:
    name: str        # "erp", "gmail", "whatsapp", "scheduling"
    kind: str        # "rest" | "mcp" | "memory" | "database"
    def key(self) -> str: return f"{self.kind}:{self.name}"

class IConnector(ABC):
    def invoke(self, target: ConnectorTarget, operation: str, payload: dict, principal: Principal) -> dict: ...

class ConnectorRejection(Exception):
    # typed rejection from the SYSTEM_OF_RECORD target: .code, .violation, .detail
```

Inference:
```python
@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[dict, ...]        # {"role": ..., "content": ...}
    max_tokens: int = 1024
    purpose: str = "generation"       # "selection" | "generation"
    metadata: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ModelResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    model: str = ""

class IModelClient(ABC):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

Diagnostics:
```python
@dataclass(frozen=True)
class ModelCallEvent:  execution_id, step_id, model, input_tokens, output_tokens, cache_read_tokens, latency_ms, purpose
@dataclass(frozen=True)
class StepEvent:       execution_id, plan_id, step_id, agent, status, latency_ms
@dataclass(frozen=True)
class AuditEvent:      execution_id, plan_id, step_id, principal_id, kind, detail
# kind: "proposal" (what the agent proposed) | "guardrail" (decision) | "system_of_record" (target validation)
#       | "output_contract" (generation contract violation / retry_once_then_fail outcome)
#       | "compensation" (per-step start/result/failed — the failed event carries orphaned_state —
#         plus one "outcome" event per failing execution with the final 3-way failure status)

class IObservabilityWriter(ABC):
    def model_call(self, e: ModelCallEvent) -> None: ...
    def step(self, e: StepEvent) -> None: ...
    def trace(self, execution_id: str, payload: dict) -> None: ...

class IAuditWriter(ABC):              # complete, never sampled
    def record(self, e: AuditEvent) -> None: ...
```

### core/agents.py
```python
@dataclass(frozen=True)
class AgentSpecification:
    id: str
    owner: str
    business_context: str
    goal: str
    instructions: str
    capabilities: tuple[str, ...]     # capability ids
    memory_policy: "MemoryPolicy"
    spec_dir: str                     # specification folder path (to load capabilities/guardrail)

class IAgentRegistry(ABC):
    def get(self, agent_id: str) -> AgentSpecification: ...
    def all(self) -> tuple[AgentSpecification, ...]: ...

class IAgentRuntime(ABC):
    def execute(self, agent_id: str, intent: str, payload: dict, ctx: "ExecutionContext") -> dict: ...

class AgentCompositionError(Exception): ...   # restriction 2: agent called agent
class ChannelViolationError(Exception): ...   # restriction 4: entry without going through the Orchestrator
```

### core/orchestration.py
```python
@dataclass(frozen=True)
class PlanStep:
    id: str
    agent: str
    intent: str                       # never a capability or tool
    input: dict                       # templates "{{ request.x }}" / "{{ steps.<id>.result.y }}"
    depends_on: tuple[str, ...] = ()
    effect: bool = False
    compensate: dict | None = None    # {"agent":..., "intent":..., "input": {...}} — never capability/tool

@dataclass(frozen=True)
class ExecutionPlan:
    id: str
    version: str
    description: str
    triggers: tuple[str, ...]         # descriptions for plan selection
    entry_scopes: tuple[str, ...]     # SystemPrincipal scopes when there is no user
    steps: tuple[PlanStep, ...]
    output: dict

@dataclass(frozen=True)
class Rejection:
    code: str                         # "plan_not_found" | "invariant_violated" — the only two codes produced today.
                                      # A guardrail denial (GuardrailDenied) propagates through the generic step-failure
                                      # path and currently surfaces as "invariant_violated" (known vocabulary flattening,
                                      # recorded in NOTES; a dedicated "guardrail_denied" code would be a behavior change).
    reason: str
    scope: str = ""

@dataclass
class ExecutionResult:
    execution_id: str
    status: str                       # "completed" | "rejected" | "failed_clean" | "compensated" | "compensation_failed"
                                      # (3-way failure vocabulary: failed_clean = step failed with zero
                                      # completed effect steps, nothing to compensate; compensated = all
                                      # compensations ran and succeeded; compensation_failed = at least one
                                      # compensation itself failed, orphaned state left in the business system)
    output: dict = field(default_factory=dict)
    rejection: Rejection | None = None
    metrics: dict = field(default_factory=dict)

class IOrchestrator(ABC):
    def handle(self, request: dict) -> ExecutionResult: ...
    # request: {"plan": str?, "intent": str?, "payload": dict, "principal": Principal?}

class PlanValidationError(Exception): ...     # restriction 6 at load time
class PlanNotFoundError(Exception): ...       # restriction 7 — currently UNUSED: the orchestrator's _resolve_plan
                                              # returns None and handle() builds Rejection("plan_not_found", ...)
                                              # directly; the exception class exists in core but nothing raises it.
```
`ExecutionContext` lives in `ai` (it is behavior), but the type is referenced by
name via a bare forward-reference string annotation (no import, not even under
`TYPE_CHECKING`) in core to avoid inverting the dependency — at
runtime core does not import ai.

### core/guardrails.py
```python
class PolicyScope(Enum):
    PLATFORM = "platform"
    AGENT = "agent"
    PLAN = "plan"

@dataclass(frozen=True)
class GuardrailPolicy:
    id: str
    scope: PolicyScope
    allow_tool_types: frozenset[str]      # ToolType names
    allow_targets: frozenset[str]         # "kind:name" keys; "model:*" authorizes inference
    max_model_calls_per_step: int
    max_tool_calls_per_step: int

@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    rule: str
    scope: str

class IGuardrailEvaluator(ABC):
    def evaluate_tool(self, spec: "ToolSpec", ctx: object) -> GuardrailDecision: ...
    def evaluate_inference(self, ctx: object) -> GuardrailDecision: ...

class GuardrailWideningError(Exception): ...  # restriction 3 at load time
class GuardrailDenied(Exception): ...         # negative decision at runtime (carries the decision)
```

### core/memory.py
```python
@dataclass(frozen=True)
class MemoryPolicy:
    session_last_n_turns: int = 10
    session_ttl_seconds: int = 1800
    knowledge_enabled: bool = False
    knowledge_top_k: int = 3

class ISessionStore(ABC):             # short-term: append-only + TTL; never rewrites a turn
    def append(self, session_id: str, entry: dict) -> None: ...
    def history(self, session_id: str) -> tuple[dict, ...]: ...

class SessionRewriteError(Exception): ...

class IKnowledgeStore(ABC):           # long-term: cross-session, semantic search
    def upsert(self, doc_id: str, text: str, metadata: dict) -> None: ...
    def search(self, query: str, top_k: int) -> tuple[dict, ...]: ...
```

## ai/ — behavior

Layout (simplified 2026-07-27, structure-simplification phase): `orchestrator.py`,
`context.py`, `guardrails.py`, `guardrail.yaml`, **`agents.py`** (merger of the
former `agents/registry.py` + `agents/runtime.py` + `agents/dynload.py` +
`tool_registry.py` — the classes stay separate, only the files merged),
`plans/*.yaml`, and the flattened data tree `agents/<agent>/` (agent.yaml,
guardrail.yaml, one `.py` per capability with its tools inline). The `agents/`
data folder has **no `__init__.py`** at any level: `import ai.agents` must
resolve to the `agents.py` module (a regular module beats a PEP 420 namespace
package); the data folder is reached only via explicit file paths.

### ai/orchestrator.py — the platform's single entry point
- `Orchestrator(plans, registry, runtime, guardrails, model, session_store, obs, audit)`.
- `handle(request)`:
  1. Resolves the plan: `request["plan"]` directly, otherwise **model-based
     selection** (catalog of `id + description + triggers`; the model chooses or
     answers `none`). No plan → `ExecutionResult(status="rejected", rejection=Rejection("plan_not_found", ...))`. Never improvises.
  2. Principal: `request.get("principal")` or `SystemPrincipal.with_scopes(plan.entry_scopes)`.
  3. Creates the `ExecutionContext` via the **internal** factory `ExecutionContext._issue(...)` —
     only the Orchestrator issues it (restriction 4: the runtime validates `ctx.issued_by_orchestrator`).
  4. Executes steps in declared order (validates `depends_on`); resolves
     `{{ request.* }}` / `{{ steps.<id>.result.* }}` templates (regex + dotted path).
  5. Step failure: with zero completed effect steps → `status="failed_clean"`
     (nothing irreversible ran, nothing to compensate). Otherwise runs
     `compensate` of the **already-completed effectful steps** in reverse
     order → `status="compensated"`, or `"compensation_failed"` if any
     compensation itself fails (orphaned state recorded in the audit trail).
  6. Emits StepEvent/trace; audits proposal/guardrail/system_of_record on each
     step (output_contract and compensation kinds fire on their failure paths).
- Plan loading (`ai/plans/*.yaml`): validates **restriction 6** — a step with
  `effect: true` and `compensate: null` must be the last step with an effect,
  otherwise `PlanValidationError`.

### ai/context.py
- `ContextBuilder(session_store, knowledge_store)`.
- `build(agent_spec, session_id, payload, token_budget) -> tuple[dict, ...]` (messages):
  agent instructions + knowledge (if `memory_policy.knowledge_enabled`, top_k) +
  last N session turns + current payload.
- Compaction: token estimate `len(text)//4`; over budget → drop oldest turns
  first (deterministic), preserving the prefix (append-only).

### ai/guardrails.py — single engine, three scopes
- `load_policy(path, scope) -> GuardrailPolicy` (YAML below).
- `compose(wider, narrower) -> GuardrailPolicy` (2-arg; `GuardrailEngine.evaluator_for`
  calls it twice in sequence: platform∩agent, then ∩plan when present) — **intersection**:
  sets ∩, limits `min`. At load: if a narrower scope contains a tool_type/target
  absent from the wider one → `GuardrailWideningError` (restriction 3).
- `GuardrailEvaluator(effective_policy, audit)` implements `IGuardrailEvaluator`;
  every decision becomes an `AuditEvent(kind="guardrail")`. Denied → `GuardrailDenied`.

Guardrail YAML:
```yaml
id: platform-guardrail
scope: platform
allow_tool_types: [READ_ONLY, SYSTEM_OF_RECORD, IRREVERSIBLE]
allow_targets: ["rest:erp", "rest:scheduling", "mcp:gmail", "mcp:whatsapp", "model:*"]
limits:
  max_model_calls_per_step: 3
  max_tool_calls_per_step: 10
```

### ai/agents.py — ToolRegistry (formerly tool_registry.py)
- Imports the capabilities' tool modules and registers the `ToolSpec`s.
- **Restriction 1 at registration**: `writes_business_state=True` with type ≠ SYSTEM_OF_RECORD →
  `ToolClassificationError`; SYSTEM_OF_RECORD type with `target.kind in {"memory","database"}` →
  `ToolClassificationError` (a system-of-record target cannot be a memory store or database).
- `get(name) -> ToolSpec`.

### ai/agents.py — AgentRegistry (formerly agents/registry.py)
- Scans `ai/agents/*/agent.yaml` → `AgentSpecification`. Implements `IAgentRegistry`.

### ai/agents.py — AgentRuntime (formerly agents/runtime.py), implements IAgentRuntime
- Reentrancy guard (`contextvars`): `execute` inside `execute` →
  `AgentCompositionError` (restriction 2).
- `ctx.issued_by_orchestrator` false → `ChannelViolationError` (restriction 4).
- Flow per invocation:
  1. **Selection** (confined non-determinism): the model chooses a capability
     among the agent's, given the `intent` (`purpose="selection"`). Audited as `proposal`.
  2. Loads the Capability class (import of `ai/agents/<agent>/<capability>.py` —
     one file per capability, kebab-case id → snake_case filename) and
     executes `steps` **in the hardcoded order**; per step:
     - guardrail (`evaluate_tool`/`evaluate_inference`) — denial aborts;
     - TOOL: `spec.fn(tool_ctx, **kwargs)`; kwargs come from the payload/previous results;
     - INFERENCE: calls the capability module's function `fn(tool_ctx, full_payload) -> dict`
       (it uses `tool_ctx.model.complete` and `tool_ctx.context_builder`); content audited as `proposal`.
     - SYSTEM_OF_RECORD tool result audited as `system_of_record`; `ConnectorRejection`
       propagates as a typed failure.
  3. `ToolContext`: `principal`, `connectors` (kind→IConnector dict), `model`,
     `obs`, `audit`, `execution_id`, `step_id` — identity reaches the tool
     (restriction 5; `principal is None` → `ChannelViolationError`).
  4. Intra-capability compensation: `Step.compensate` runs when a later step fails.

### Specifications (data)

`ai/agents/<agent>/agent.yaml` (3 agents: `schedule/`, `correspondence/`, `erp/`).
Each agent folder holds `agent.yaml`, `guardrail.yaml` and one `.py` file per
capability containing the Capability class **and** its `@tool` functions (plus
inline `ConnectorTarget`s). Capability id → file: kebab-case → snake_case
(`read-and-reply` → `read_and_reply.py`). `reschedule.py` declares no tools of
its own — it references `book.py`'s tools by name (tools register once per agent).
```yaml
id: schedule-agent
version: 1.0.0
owner: scheduling
business_context: scheduling
goal: ...
instructions: |
  ...
capabilities: [book, reschedule]
memory_policy:
  session: {last_n_turns: 10, ttl_seconds: 1800}
  knowledge: {enabled: true, top_k: 3}
```
- **schedule-agent** (owner scheduling): capabilities `book`, `reschedule`.
  Tools: `check_availability` (READ_ONLY, rest:scheduling) · `retrieve_policy`
  (READ_ONLY, rest:scheduling) · `create_appointment` (SYSTEM_OF_RECORD, rest:scheduling) ·
  `send_confirmation` (IRREVERSIBLE, mcp:whatsapp).
  `book`: availability → policy → create → confirm. `reschedule`: availability →
  create (with `previous_appointment_id`) → confirm — reuses the same tools.
- **correspondence-agent** (owner customer-service): capability `read-and-reply`.
  Tools: `read_email` (READ_ONLY, mcp:gmail) · `send_reply` (IRREVERSIBLE, mcp:gmail).
  Steps: read → **interpret_and_draft (INFERENCE)** → send. The payload may carry
  `mode: "correction"` (used by the plan's compensation: drafts a correction/retraction).
- **erp-agent** (owner back-office): capabilities `register-request`, `cancel-request`.
  Tools: `create_record` (SYSTEM_OF_RECORD, rest:erp) · `cancel_record` (SYSTEM_OF_RECORD, rest:erp).

Each `<Agent>/guardrail.yaml` narrows `allow_targets` to the agent's own targets
(intersection with the platform; widening = error).

### ai/plans/ — 2 plans

Files `schedule_appointment.yaml` and `inbound_email_to_erp.yaml` (snake_case
filenames; the plan **ids** keep their kebab-case form below).

`schedule_appointment.yaml`:
```yaml
id: schedule-appointment
version: 1.0.0
description: Book an appointment and confirm it to the customer.
triggers: ["agendar", "marcar horário", "schedule", "book"]
entry_scopes: ["scheduling:write", "whatsapp:send"]
steps:
  - id: booking
    agent: schedule-agent
    intent: "book an appointment"
    input: {customer_id: "{{ request.customer_id }}", slot: "{{ request.slot }}"}
    effect: true
    compensate: null          # irreversible — must be the last effectful step (it is the only one)
output:
  appointment_id: "{{ steps.booking.result.appointment_id }}"
```

`inbound-email-to-erp.yaml`:
```yaml
id: inbound-email-to-erp
version: 1.0.0
description: Read a customer email, reply and register the request in the ERP.
triggers: ["email recebido", "received email", "inbound email", "solicitação por email", "email request"]
entry_scopes: ["gmail:read", "gmail:send", "erp:write"]
steps:
  - id: acknowledge
    agent: correspondence-agent
    intent: "read inbound email and send acknowledgement reply"
    input: {email_id: "{{ request.email_id }}"}
    effect: true
    compensate:
      agent: correspondence-agent
      intent: "send correction about failed processing"
      input: {email_id: "{{ request.email_id }}", mode: "correction"}
  - id: register
    agent: erp-agent
    intent: "register customer request record"
    depends_on: [acknowledge]
    input:
      customer_email: "{{ steps.acknowledge.result.sender }}"
      summary: "{{ steps.acknowledge.result.summary }}"
      request_type: "{{ steps.acknowledge.result.request_type }}"
    effect: true
    compensate:
      agent: erp-agent
      intent: "cancel request record"
      input: {record_id: "{{ steps.register.result.record_id }}"}
output:
  record_id: "{{ steps.register.result.record_id }}"
  reply_id: "{{ steps.acknowledge.result.reply_id }}"
```
Note: both steps have compensation → restriction 6 satisfied; an ERP failure
(invariant) triggers the acknowledge compensation (a correction email).

## infrastructure/

- `providers/local_client.py` — deterministic, zero cost. `purpose="selection"`:
  chooses by intent keyword (book/agendar→book or schedule-appointment;
  reschedule/remarcar→reschedule; read/reply/email→read-and-reply or
  inbound-email-to-erp; register→register-request; cancel→cancel-request; no
  match → "none"; plan selection likewise via triggers). `purpose="generation"`:
  canonical deterministic text (e.g. summary/email as a function of the input).
  Tokens: `len(prompt)//4` / `len(out)//4`. `cache_read_tokens`: simulates a
  repeated prefix per session. `model="local-deterministic"`.
- `providers/anthropic_client.py` — `anthropic` SDK, default model `claude-sonnet-5`
  (env `ANTHROPIC_MODEL`); reports real usage incl. cache. Lazy import.
- `providers/openai_client.py` — `openai` SDK, analogous (`OPENAI_MODEL`).
- `connectors/rest/rest_connector.py` — real `httpx` client; accepts `base_url` **or**
  `transport` (in-process ASGI for tests). Propagates identity in headers
  `X-Principal-Id`, `X-Principal-Type`, `X-Principal-Scopes`. HTTP 422 → raises a
  typed `ConnectorRejection` with the `{code, violation, detail}` body.
- `connectors/mcp/mcp_connector.py` — **real generic** MCP client (`mcp` SDK,
  stdio): reads `servers.yaml`; connects, `call_tool(operation, payload)`; internal
  asyncio→sync bridge. If the command/credential is missing or the connection
  fails → **deterministic stub** per server (gmail: canonical email, derived
  reply_id; whatsapp: derived message_id) without breaking the flow — logging the fallback.
- `connectors/mcp/servers.yaml` — declares `gmail` and `whatsapp` (command, args,
  env with credential names).
- `memory/session_store.py` — `InMemorySessionStore` (dict) with TTL and
  append-only semantics (attempting to rewrite a past index → `SessionRewriteError`);
  uses Redis when `REDIS_URL` is set (lazy import), same semantics.
- `memory/knowledge_store.py` — local vector store: deterministic bag-of-words
  hashing embedding (dim 256), cosine; `upsert` + `search`. (Swappable for real
  embeddings; measuring that is out of scope.)
- `observability/console_writer.py` — prints compact events; accumulates metrics
  per execution (`summary(execution_id) -> dict` with tokens, calls, cache,
  per-step latency, **cost per task** via a per-model pricing table; local = 0).
- `observability/langsmith_writer.py` — `langsmith` SDK (RunTree), active when
  `LANGSMITH_API_KEY` is set; otherwise no-op. No LangChain.
- `audit/audit_store.py` — append-only JSONL at `var/audit.jsonl`; never sampled.
- `configurations.py` — **composition root**:
  `build_platform(provider=None, erp_transport=None, audit_path=None, *, record_model_calls=False, inject_generation_fault=False, strip_response_schema=False, tolerant_repair=False) -> Platform`
  (`provider=None` resolves from `AI_PROVIDER` env, default local; the four
  keyword flags are measurement/matrix instrumentation — see the measurement
  sections below)
  (dataclass with the orchestrator + collaborators). Choice via env `AI_PROVIDER`.

## The business system: `erp_service/` (outside src/ — real boundary)

Persistence phase replaced the in-memory mock (`services/erp_mock`, deleted) with a
real service: **SQLAlchemy 2.0 + SQLite**, own Docker image, reachable by the
platform **only over HTTP**.

- `erp_service/models.py` — business entities live HERE and only here:
  `ServiceRequest` (status open|cancelled; **partial unique index**: at most one
  `open` request per `customer_email`) and `Appointment` (status booked|cancelled;
  **partial unique index** on `slot` for `booked`). Invariants are database
  constraints validated against persisted state inside a transaction — cancel is
  a status flip, never a delete (compensation leaves an audit-able trail).
- `erp_service/api.py` — `create_app(database_url=None)` factory; module-level
  `app` uses env `ERP_DATABASE_URL` (default: in-memory SQLite + StaticPool, for
  fast unit tests). Same endpoints/contracts as before (`POST /records`,
  `POST /records/{id}/cancel`, `POST /appointments`, availability/policies/debug).
  Two-layer validation, both → typed **422** `{code:"invariant_violated", violation, detail}`:
  pre-validation (shape) and `IntegrityError` translation (`open_request_exists`,
  `slot_taken`) — the decisive check is the constraint.
- `erp_service/Dockerfile` — uvicorn on 8123, `ERP_DATABASE_URL=sqlite:////data/erp.sqlite`
  (volume `./var/erp-data:/data`).
- **Isolation rule (restriction 4 made structural):** the platform image (`aici`)
  does not copy `erp_service/`, receives no ERP DSN/volume/credential — its sole
  channel to the business system is `ERP_BASE_URL` (HTTP). Verified statically,
  against docker-compose, and dynamically by `tests/test_persistence_restrictions.py`.

## Platform persistence (infrastructure)

Env `PLATFORM_DATABASE_URL` (Postgres) switches the platform stores from the
in-memory/JSONL fallbacks to real persistence (connection failure fails loudly —
never a silent fallback); absent → fallbacks, so everything runs without Docker.

- `core/memory.py` gained the platform records `SessionRecord` and `MemoryRecord`
  (frozen dataclasses, stdlib only). Existing store interfaces unchanged
  (`ai/context.py` consumes them; contract evolution would ripple into `ai/`).
- `infrastructure/memory/models.py` — SQLAlchemy platform tables: `session_entries`
  (composite PK session_id+turn_index, `expires_at` TTL), `knowledge_docs`
  (pgvector `Vector(256)`), `audit_records` (autoincrement, append-only).
- `PostgresSessionStore` — INSERT-only (no UPDATE/DELETE methods exist); PK
  collision → `SessionRewriteError`; TTL filtered on read.
- `PgVectorKnowledgeStore` — same deterministic hashing embedder as the local
  store (module-level `embed_text`), upsert via ON CONFLICT, cosine search.
- `PostgresAuditWriter` — INSERT-only, never sampled; `read_all()` shape-compatible
  with the JSONL writer. Explicit `audit_path` (tests) still wins over the env.
- docker-compose: `postgres-platform` (pgvector/pgvector:pg16) + `erp` + `aici`.

## Tests (pytest, local provider, ERP via ASGITransport, MCP on stub)

`tests/test_restrictions.py` — 1 test per restriction:
1. `test_write_requires_system_of_record_and_no_memory_target` — registering a tool
   with `writes_business_state=True` that is not SYSTEM_OF_RECORD → error; SYSTEM_OF_RECORD with a
   memory/database target kind → error.
2. `test_agent_cannot_call_agent` — a capability invoking the runtime during
   execution → `AgentCompositionError`.
3. `test_guardrail_intersection_rejects_widening` — an agent guardrail with a
   target outside the platform → `GuardrailWideningError` at load; and the
   effective intersection = ∩.
4. `test_entry_only_via_orchestrator` — `runtime.execute` with a ctx not issued
   by the orchestrator → `ChannelViolationError`.
5. `test_identity_propagated_to_tool` — runs a plan with a user Principal and
   verifies the ERP service (in-process via ASGITransport) received `X-Principal-Id`; without a principal →
   SystemPrincipal with the plan's `entry_scopes`.
6. `test_effectful_without_compensation_must_be_last` — loading an invalid plan
   (effect+null before another effect) → `PlanValidationError`; the repo's plans load.
7. `test_request_without_plan_is_refused` — intent with no plan → `status="rejected"`,
   `rejection.code == "plan_not_found"`, and no step executed.

`tests/test_dependency_direction.py` — AST over `src/`: core → stdlib only;
ai → internal imports only from core (+`yaml`); infrastructure unrestricted.

`tests/test_end_to_end.py` — both plans complete with the local provider (zero
cost); failure scenario: ERP rejects the invariant → `status="compensated"` and a
correction email is sent (stub) + `cancel` is not called; the audit trail
contains proposal/guardrail/system_of_record plus the compensation events.

## Scripts

`scripts/run_plans.py` — builds the platform (`AI_PROVIDER`, default local),
starts the ERP in-process (ASGI) or uses `--erp-url`, executes both plans, and
prints per execution: model calls, tokens in/out, cache hits, per-step latency,
and the **cost per completed task** (per-model pricing table; local = $0).
Readable table output.

## Measurement phase (real model)

Additions for the real-model measurement study (all outside `core`/`ai`):

- `infrastructure/providers/gemini_client.py` — `GeminiModelClient` (`google-genai`
  SDK, env `GOOGLE_API_KEY` **required** — clear `RuntimeError` when absent, never a
  silent stub; model via `GEMINI_MODEL`, default `gemini-3.1-flash-lite` — the
  measured model, so a fresh clone reproduces the study's condition; maps
  `usage_metadata` prompt/candidates/cached token counts to `ModelResponse`).
- `infrastructure/providers/recording_client.py` — `RecordingModelClient`, a
  measurement decorator over any `IModelClient`; logs
  `{purpose, prompt, content, model, input_tokens, output_tokens, cache_read_tokens}`
  per call. Enabled via `build_platform(record_model_calls=True)` →
  `Platform.model_log`.
- `observability/langsmith_writer.py` — root trace per execution, nested spans per
  plan step and per model call (prompt/response/tokens via index correlation with
  the recorder's log — valid because execution is synchronous single-thread);
  `finalize_execution(execution_id, tags)` patches tags `plan_id`,
  `selected_capability`, `provider`, `run_index`, `output_contract_violation`.
  No `LANGSMITH_API_KEY` → silent no-op; console writer always active.
- `scripts/run_plans.py --repeat N` — each plan N times on one shared platform,
  requests carry **intent only** (the model selects the plan — selection variance
  is the primary datum); per-execution selection tracking from the audit trail;
  post-hoc output-contract analysis from the model log — at this phase the
  prototype had **no** `output_contract`/`retry_once_then_fail` (that state is
  what the first round measured: 100% silent absorption); the guard was added
  in the correction phase below, and the post-hoc detector is **kept** as an
  independent second detector alongside the audit counters (the integrity
  matrix showed why: tolerant paths can zero the counters while the model log
  still sees every corrupted response); aggregates (mean/stdev, success rate,
  selection distribution,
  violation counts, selection-vs-generation cost split) to console and
  `results/runs/run-<timestamp>.json`. `--erp {http,inprocess}` picks the business
  boundary: `http` (default) targets `ERP_BASE_URL` (container); `inprocess`
  runs `erp_service` over ASGI with `var/erp/erp.sqlite` — persistent, so
  repeated runs against the same file can legitimately collide on business
  invariants (payloads vary per round within one run).
- `Dockerfile` + `docker-compose.yml` — runner reaching the **real `erp`
  container over HTTP** (`ERP_BASE_URL=http://erp:8123`; `--erp` default is
  `http` — the in-process ASGI mode is the local/test path, not compose's),
  `GOOGLE_API_KEY`/`LANGSMITH_*` passed via env, `./var` and `./results` volumes.
- `tests/test_restriction_violations.py` — anti-vacuity probe: 7 strict-xfail
  tests that commit each restriction's violation on purpose; XPASS = an
  unenforced restriction.
- `tests/test_persistence_restrictions.py` — persistence-phase restrictions:
  platform has no path to business storage (static + compose + dynamic);
  compensation reaches persisted state (SQLite row `cancelled`); compensation
  failure leaves an orphan row, now traceable end-to-end via `kind="compensation"`
  audit events (the audit gap found in the persistence phase is closed — see
  "Structured output correction" below);
  constraint violation reaches the agent as a typed `ConnectorRejection`.

## Structured output correction (post-measurement)

The first real-model round measured 100% generation-contract violations (fenced
JSON absorbed by a parser fallback, garbage persisted as business `summary`).
Research showed this is a worst-case-configuration artifact (prompt-only, flash
model), not a property of the pattern. Corrections:

- **`ModelRequest.response_schema: dict | None`** — deliberate `core` contract
  change: when present, the provider MUST constrain output at the decoding level.
  Finding recorded: the original model port could not express constrained output.
- Providers: Gemini → `response_mime_type: "application/json"` + `response_schema`;
  Anthropic → `output_config.format` (json_schema); OpenAI → `response_format`
  (json_schema, strict); local → always emits raw schema-conforming JSON.
- `ReadAndReply.interpret_and_draft` declares `_RESPONSE_SCHEMA`
  (request_type/summary/reply_body, `additionalProperties: false`). Parser is
  STRICT (raw JSON only — no fence-stripping, no tolerant parsing) and implements
  **`retry_once_then_fail`**: first violation → audit (`kind="output_contract"`,
  actions violation/retry) + one retry with the schema; second violation → typed
  `OutputContractViolation`, `status` non-completed, nothing unvalidated is ever
  persisted. The old silent fallback is removed.
- **Compensation audit vocabulary**: `kind="compensation"` events
  (action start/result/failed) emitted by the Orchestrator's plan-level
  compensation and the runtime's intra-capability compensation; the `failed`
  event carries the `orphaned_state` (the original step result left behind).
  Closes the audit gap found in the persistence phase.
- Runner reports `output_contract` counters (violations/retries/failures) from
  the audit trail alongside the model-log post-hoc analysis.

## Golden rules
- No agent frameworks. LangSmith only as a tracing SDK.
- No abstraction the 3 agents do not exercise; no folder with a single file.
- Sequence decisions never in the model: agent order in the plan YAML; tool
  order in the capability's Python.
