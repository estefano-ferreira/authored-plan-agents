# NOTES — where the structure resisted, and where it had to bend

Reference implementation findings. Everything here was observed while building and
testing the prototype, not speculated. (Suite at the initial build: 14 tests
green; it grew to the current **18 passed + 7 xfailed** during the persistence
phase — see "Persistence and real boundary".)

## Where the structure resisted (build phase, ≤2026-07-27)

**1. Confining non-determinism to selection and generation held up cleanly.**
The model is consulted at exactly three points: plan selection, capability
selection, and content generation (`interpret_and_draft`). Step order between
agents lives in plan YAML; step order between tools lives in Python `steps`
tuples. At no point did the implementation *want* to ask the model "what next?" —
the seams were sufficient. Swapping `local_client` for Anthropic/OpenAI changes
cost and content, never the execution path. This is the pattern's central claim
and it survived contact with the code.

**2. Dependency direction was enforceable, not aspirational.**
`core` = stdlib only, `ai` = core + yaml, `infrastructure` = anything — verified
by an AST test, not convention. The one consolidation that helped: collapsing
`inference/connectors/diagnostics` into `core/ports.py` made "these are boundary
contracts implemented by Infrastructure" visible in the layout itself. Models
(`AgentSpecification`, `ExecutionPlan`) had to live in core alongside the
interfaces — if they were born in `ai`, Infrastructure would depend on `ai` and
the direction would invert.

**3. Tool classification mapped onto reality without residue.**
All 8 tools classified naturally (READ_ONLY / SYSTEM_OF_RECORD / IRREVERSIBLE),
and both halves of restriction 1 were mechanically checkable at registration
time: a writing tool that isn't SYSTEM_OF_RECORD and a SYSTEM_OF_RECORD tool
whose target is a
memory/database backend both fail before anything runs. The ERP mock validating
the invariant *on its side* (typed 422 → `ConnectorRejection`) kept the invariant
where the pattern says it belongs: in the business system, not in the agent.

**4. Load-time validation moved a production failure to design time.**
Restriction 6 (irreversible step without compensation must be last) rejected our
first, "natural" draft of the inbound-email plan before it could ever run — see
bend #1 below. That is exactly the value proposition of declaring sequence in
versioned YAML: the argument happened at load time, in review, not in an incident.

**5. Audit vs. telemetry separation fell out naturally.**
Three audit kinds — `proposal` (what the agent/model proposed), `guardrail`
(what the policy decided), `system_of_record` (what the business system
validated; recorded as `authoritative` in pre-rename trails — see "Terminology
correction") —
covered every event we needed to record, unsampled, in an append-only JSONL.
Telemetry (tokens, cache reads, latency per step) stayed entirely in
`IObservabilityWriter`, and **cost per completed task** is computable from
`ModelCallEvent`s alone: the two concerns never pulled on each other.
*(Update: "covered every event we needed" was falsified twice — the
persistence phase found compensation had no audit vocabulary, and the
structured-output correction added a contract vocabulary. The kind set grew
3 → 5: `compensation` and `output_contract` — see "Persistence and real
boundary" and "Structured output correction". The separation claim itself
held; the completeness claim did not.)*

**6. The two memory contracts never tempted unification.**
`ISessionStore` (append-only, TTL, rewrite attempt raises) and `IKnowledgeStore`
(cross-session, semantic search) were consumed by the same `ContextBuilder`
under one `MemoryPolicy` without either contract leaking into the other.

**7. Real connector, graceful stub.** The generic MCP client with per-server
deterministic fallback meant credential-less test runs cost nothing and broke
nothing, while the code path to a real Gmail/WhatsApp server is the same
`IConnector.invoke`. The `IConnector` seam carried all of that weight alone.

## Where it had to bend (build phase, ≤2026-07-27; bends #7 and #9 annotated later)

**1. Restriction 6 vs. capability granularity — the biggest collision.**
The natural business flow for `inbound-email-to-erp` is *read → register in ERP →
reply with the protocol number*. But `ReadAndReply` is one cohesive capability
containing both the read and the irreversible reply, and a plan may not reference
tools — so the plan cannot interleave the ERP step between a capability's tools.
Two exits existed: split `ReadAndReply` into two capabilities (destroying the
"cohesive business capability" premise), or reorder to *acknowledge → register*
and give the reply a real compensation. We chose the second: the acknowledgement
goes out first, and its compensation is a **forward correction email**, not a
rollback. Consequence accepted: the reply cannot contain the ERP protocol number.
The pattern did not solve this tension — it **forced it into the open** as an
explicit, reviewable business decision instead of letting it hide in a prompt.

**2. Compensation of an IRREVERSIBLE step is generation, not reversal.**
Because plans may only name agent + intent, the compensation of `acknowledge`
re-invokes the *same* capability with `mode: "correction"` in the payload. The
deterministic tool sequence is identical; only the generated content differs.
This is coherent with the pattern (IRREVERSIBLE = no guaranteed rollback), but it
means compensation semantics for irreversible actions lean on generation quality —
worth stating honestly in any production adoption.

**3. Intra-capability compensation exists but no in-scope agent exercises it.**
`Step.compensate` (tool-level) is part of the capability contract, yet the fixed
tool list gives `Book` no `cancel_appointment` to compensate `create_appointment`
with. Plan-level compensation (ERP `cancel_record`) is what the prototype
actually exercises end-to-end. This brushes against "don't build what the 3
agents don't exercise": the mechanism stayed because the contract requires it,
but a stricter reading would cut it until an agent needs it.

**4. "core imports nothing" required interpretation.**
Literally, core imports stdlib (`dataclasses`, `abc`, `enum`); and `ai` needed
`yaml` to load its own declarative artifacts. The dependency test encodes the
pragmatic reading (core = stdlib only; ai = core + stdlib + yaml). Related hole:
`IAgentRuntime.execute` receives an `ExecutionContext` that is *behavior* (lives
in `ai`), so the core contract types it as `object` — dependency direction was
preserved at the price of a typing gap at exactly the layer boundary.

**5. Restriction 4 is architectural verification, not a security boundary.**
`ExecutionContext._issue` + `issued_by_orchestrator` is trivially forgeable in
Python. The test proves the *architecture* rejects side entries; it does not
prove an attacker can't. One genuine defense emerged for free: tools receive a
`ToolContext` that deliberately does not carry the `ExecutionContext`, so a
hostile tool cannot replay it — in the restriction-2 test, such a tool dies on
the reentrancy guard before anything else. Real enforcement needs process or
authn boundaries, out of scope here.

**6. The deterministic stub coupled infrastructure to declarative text.**
`LocalModelClient` selects plans/capabilities by keyword matching against the
prompt, which quietly coupled it to plan `triggers` and intent wording (exposed
when translating the codebase to English: the correction-mode detection and the
trigger keywords both had to move in lockstep). A real model has no such
coupling — this is the cost of a zero-cost test double, and it should be read as
measurement infrastructure, not as part of the pattern.

**7. Status vocabulary had one rough edge — later measured, then fixed.**
Originally, a step failure with zero completed irreversible steps returned
`"compensated"` (there was nothing to compensate). What began as a cosmetic
complaint was upgraded to a **misleading remediation signal** by the controlled
fault-injection run (see "Structured output correction") and fixed with the
3-way failure statuses: `failed_clean` (nothing to compensate), `compensated`
(compensations actually ran), `compensation_failed`. The integrity matrix's
cell B is the measured proof the split was needed.

**8. Plan-scope guardrails are supported but not exercised.**
The engine composes three scopes by intersection and rejects widening at load,
but no plan in the repo declares its own guardrail file — platform ∩ agent is
what actually runs; plan scope is covered only by synthetic policies in tests.
Either a plan-level policy earns its place with a real rule, or the third scope
is speculative for this scope of agents.

**9. `Rejection.code` flattens failure causes (found in the 2026-07-28 doc
audit).** Only two codes are ever produced: `plan_not_found` and
`invariant_violated`. A `GuardrailDenied` raised mid-execution propagates
through the generic step-failure path and surfaces as `invariant_violated` —
a policy denial is indistinguishable from a business-rule rejection in the
result's code (the *audit trail* still tells them apart: the guardrail denial
has its own `kind="guardrail"` event). A dedicated `guardrail_denied` code
would be a small behavior change, deliberately not made during a
documentation pass; queued as a candidate correction. Related dead code:
`PlanNotFoundError` exists in `core` but nothing raises it — the orchestrator
builds the `plan_not_found` rejection directly.

## Measured — local provider, single run of `scripts/run_plans.py` (build phase, ≤2026-07-27)

| plan | model calls | tokens in/out | status | cost |
|---|---|---|---|---|
| schedule-appointment | 1 (selection) | 91 / 1 | completed | $0 |
| inbound-email-to-erp | 3 (2 selection + 1 generation) | 295 / 62 | completed | $0 |

Cost per completed task with real providers uses the same code path (pricing
table in `console_writer.py`; cache reads billed at 10% of input). The metric to
watch when swapping in Anthropic/OpenAI: selection calls are pure overhead of
the pattern (2 of the 4 calls above) — small, fixed, and cacheable, which is the
argument for keeping selection prompts short and stable.

## Real-model findings — Gemini, 10 runs per plan (2026-07-27)

Measured 2026-07-27: 10 runs per plan (20 executions, 60 model calls), model
**gemini-3.1-flash-lite**, ERP persisted (`--erp inprocess`, SQLite file),
LangSmith tracing active. The model choice was forced by observed free-tier
quotas, themselves a finding for measurement design: `gemini-2.5-flash` is
closed to new accounts, `gemini-3.6-flash` is capped at **20 requests/day**
(the run needs 60) and 5 requests/min — the adapter gained a 429-retry that
honors the server's `retryDelay`, and 4 of the 20 executions carry ~52–60s
rate-limit stalls in their latency. A second adapter-level finding from probing
`gemini-3.6-flash`: it is a *thinking* model whose thought tokens are **billed
as output but excluded from `candidates_token_count`** (73 thought tokens even
for a one-word reply) — the adapter now maps `output_tokens = candidates +
thoughts`, otherwise cost would be silently undercounted.

**Adapter required zero changes outside `infrastructure/`.** `IModelClient` /
`ModelRequest` / `ModelResponse` were sufficient for Gemini with full fidelity:
role mapping (system → `system_instruction`, assistant → `model`), `max_tokens`,
and `usage_metadata` (`prompt_token_count` / `candidates_token_count` /
`cached_content_token_count` → input / output / cache_read). Finding: the model
port was abstract enough; provider swap is purely infrastructure, as the pattern
claims. Missing `GOOGLE_API_KEY` fails loudly at construction — never a silent
stub — so an unmeasured run cannot masquerade as a measured one.

**Output Contract: the prototype does not implement it** *(superseded — this
described the state this round measured; the mechanism was implemented and
measured right after, see "Structured output correction")*. `output_contract`
with `retry_once_then_fail` existed only in the legacy sketch
(`_legacy_sketch/`), and DESIGN had not carried it into scope. Therefore
*recovery* by retry could not be measured — there was no retry. What the runner measures instead (post-hoc, from
the raw model-call log captured by `RecordingModelClient` in infrastructure,
without touching `ai/`): selection violations (response is not an offered id nor
`none` → the execution fails typed) and generation violations (response is not a
JSON object with `request_type` + `summary` → the capability's deterministic
fallback absorbs it and the execution still completes). The measurable split is
"failed via selection violation" vs. "fallback absorbed generation violation".

**Machinery validated with the local provider** (N=3 and N=10 per plan, single
shared platform, intent-driven selection): 100% success, selection distribution
fully deterministic (expected from the stub — this validates the pipeline, not
selection variance), 0 violations, cost $0. Intent-driven mode adds one
plan-selection call per execution: inbound-email-to-erp goes from 3 model calls
(295/62 tokens) with explicit plan to 4 calls (417/67) with model-selected plan —
that delta *is* the pattern's selection overhead, now separable in the cost
split (`selection_fraction` in the report).

**Exercising the model-driven path found a real dead-path bug.** The local
stub's `_select` matched keywords against the *full prompt* (intent + catalog),
so a capability id could win a plan-selection prompt via another plan's
description text. Invisible for months because nothing had ever exercised
plan selection by natural language — the legacy runner always passed the plan id
explicitly. Fixed in `infrastructure` (match restricted to catalog-offered ids,
keywords against the intent line only). Finding for the study: measurement
infrastructure exercised a path the happy-path tests never did.

**xfail verification of the 7 restriction tests: none is vacuous.** All 7
deliberate violations were blocked (7 xfailed, 0 XPASS, `strict=True`):

| # | Violation attempted | Blocked by |
|---|---|---|
| 1 | SYSTEM_OF_RECORD tool targeting a memory store | `ToolClassificationError` at registration |
| 2 | Hostile tool invoking another agent mid-execution | reentrancy guard (`AgentCompositionError`), plan ends non-completed |
| 3 | Agent guardrail widening the platform policy | `GuardrailWideningError` at engine construction |
| 4 | `runtime.execute` with a non-orchestrator ctx | `ChannelViolationError` |
| 5 | Issued ctx but `principal=None` | `ChannelViolationError` |
| 6 | Uncompensated effect step followed by another effect | `PlanValidationError` at load |
| 7 | Intent matching no plan | `status="rejected"`, no improvisation |

Subtlety confirmed by the probe: restriction 2 is enforced by the *reentrancy
flag*, not by the channel/identity check — the hostile tool's `ToolContext` has
no `issued_by_orchestrator` at all, and it is caught purely because reentrancy
is already true. A legitimate, independent enforcement path, but worth knowing
which guard actually fires.

### Measured results (results/runs/run-20260727-212338.json)

| metric | schedule-appointment | inbound-email-to-erp |
|---|---|---|
| success rate | 10/10 | 10/10 |
| model calls / execution | 2 (both selection) | 4 (3 selection + 1 generation) |
| tokens in/out (mean) | 206 / 4 | 389 / 135 |
| cache read tokens | 0 | 0 |
| cost per completed task | **$0.000058** | **$0.000300** |
| selection fraction of cost | **100%** | **29.1%** |
| latency (typical / with 429 stalls) | ~1.3s / up to 59.6s | ~2.9s / up to 52.3s |

**Selection variance: zero.** 20/20 plan selections and 30/30 capability
selections correct, from natural-language intents, with 0 selection-format
violations (the model answered exactly the offered id every time). At this
catalog size (2 plans, 1–2 capabilities per agent), real-model selection was
perfectly stable — the non-determinism the pattern confines did not, in
practice, vary at all here. Variance may appear with larger catalogs; not
tested.

**Output-contract violation rate on generation: 10/10 (100%) — all absorbed
silently.** Every `interpret_and_draft` call returned valid JSON **wrapped in
markdown code fences** (` ```json ... ``` `); the capability's parser expects
raw JSON, fails, and its fallback stuffs the raw fenced blob into
`summary[:200]`. Persistence made the consequence undeniable: the ERP rows for
all 10 inbound runs contain truncated fenced-JSON garbage as the business
`summary`, while every execution reported `completed`. This is the strongest
real-model finding of the study: **without output-contract enforcement,
contract violations don't fail — they silently degrade persisted business
data**, and only the post-hoc model-log analysis (and the `output_contract_violation`
LangSmith tag) surfaced it. `retry_once_then_fail` (legacy sketch) was never
implemented; here is the scenario that would have exercised it — a single
retry demanding raw JSON would likely have recovered all 10.

**Cost structure.** Total spend for 20 completed tasks: ~$0.0036. For the plan
with no generation step, the pattern's cost is *pure selection overhead* (100%);
for the two-agent plan with one generation step, selection is 29.1% of spend.
Overall, 40.6% of model spend went to selection — small in absolute terms
(≈$0.00007/task at flash-lite prices) but structurally fixed per step, which is
the argument for explicit-plan invocation (`"plan": ...`) on known channels:
it removes the plan-selection call entirely. Cache reads were 0 across all 60
calls — prompts at this size (~200–400 tokens) got no implicit caching benefit.

**Real boundary latency (local provider, ERP path isolated):** in-process
ASGI + SQLite ≈ **20ms** per execution; HTTP loopback (uvicorn, same SQLite
file) ≈ **570ms** per execution (schedule ~841ms with its 4 boundary
round-trips, inbound ~300ms). The per-call gap is dominated by the connector's
event-loop-per-invoke bridge creating a fresh HTTP client per call (no
connection pooling) — an honest cost of the sync-facade decision documented in
bend #4/#6 territory. Against real model latency (~1.3–3s per execution), the
HTTP boundary adds a material but non-dominant fraction; cost per completed
task is unaffected (cost is model-only).

LangSmith: traces for all 20 executions were emitted with the run's tags
(plan, capabilities, provider, run_index, `output_contract_violation`); console
metrics remained the source of truth and never depended on the external
service.

## Structure simplification (build phase, ≤2026-07-27)

File counts (excluding `__pycache__`), before → after the flattening:

| Bucket | Before | After |
|---|---|---|
| Fixed cost (core + ai shell + infrastructure) | 42 | 38 |
| Per-agent: schedule | 9 | 4 |
| Per-agent: correspondence | 6 | 3 |
| Per-agent: erp | 7 | 4 |
| **Per-agent total** | **22** | **11** |
| **Total** | **64** | **49** (−23%) |

The per-agent cost halved — which is the number that matters, since it is the
one that scales with the domain. Fixed cost dropped by 4 (module mergers:
`registry.py` + `runtime.py` + `dynload.py` + `tool_registry.py` → `agents.py`;
classes and APIs unchanged). Verified unchanged behavior: 14 passed + 7 xfailed
with **import-only** diffs in tests; legacy runner output byte-for-byte
identical (schedule 1 call 91/1 tokens, inbound 3 calls 295/62); dependency
direction still green — in fact stricter: the 5 capability files now import
only `core` + stdlib (the old `targets.py` indirection via dynload is gone).

**What was lost — observed, not speculated:**
- The capability↔tool boundary left the file system. Before, `capabilities/Book/`
  vs `capabilities/Reschedule/` made "these tools belong to that capability"
  visible in navigation; now `reschedule.py` declares no `@tool` functions and
  nothing in it except the docstring points to `book.py`, where the tools it
  references by name actually live. The file listing no longer tells you which
  files carry tools. No *test* stopped covering the boundary (restriction-1
  registration checks and tool loading are unchanged) — the loss is purely
  navigational.
- `ConnectorTarget` definitions are now duplicated per capability file (the old
  per-agent `targets.py` is gone). Frozen value objects, structurally equal, no
  functional risk — but a third ERP capability would repeat the same line again.

**Where the flattening made the three levels read as two:** the directory tree
now encodes agent (folder) and capability (file); the tool level exists only in
code. For the study's question — *does the file structure comfortably hold only
two of the pattern's three declared levels?* — the observed answer after this
refactor is yes: the third level survived intact in the *contracts* (ToolSpec,
classification, registry validation, guardrail targets) and in the *tests*, but
its last structural representation (a folder) is gone. One new structural
subtlety must be preserved: `ai/agents.py` (module) and `ai/agents/` (data
folder) coexist only while the folder has no `__init__.py` — a regular module
wins over a PEP 420 namespace package; adding an `__init__.py` would shadow the
module and break every import. That constraint is invisible in the layout and
lives only in docstrings — exactly the kind of knowledge the deleted folder
hierarchy used to carry by construction.

## Persistence and real boundary (2026-07-27)

The ERP became a real service (`erp_service/`, SQLAlchemy + SQLite, own Docker
image); invariants moved into **database constraints** (partial unique indexes:
one `open` request per customer, one `booked` appointment per slot); the platform
stores gained Postgres + pgvector implementations behind `PLATFORM_DATABASE_URL`
(in-memory/JSONL fallbacks preserved). 18 tests + 7 xfail green throughout.

**Did the separation require changes outside `infrastructure/`?** No contract or
`ai/` change. What did move: `erp_service/` is new (outside `src/`),
`scripts/run_plans.py` gained `--erp {http,inprocess}` (wiring), and
`core/memory.py` gained the sanctioned *platform* records `SessionRecord` /
`MemoryRecord` (business entities stayed exclusively in the ERP — grep-verified;
nothing forced a business model onto the platform side). One spec-vs-repo
mismatch: the task named `core/diagnostics.py` / `AuditRecord`, but that module
was collapsed into `core/ports.py` earlier and its `AuditEvent` already plays
that role — no rename was done. Test changes: import lines, plus two tests in
the new persistence file that had to follow the gmail-stub semantic change
described below.

**Partial failure — what persistence revealed that the stub hid.** The
compensation-failure scenario (compensation's own capability selection fails)
leaves the service request **orphaned: still `status='open'` in SQLite** while
the platform reports `status="failed"`. And the audit trail cannot tell you:
beyond one `proposal` event for the failed selection (`chosen="none"`), there is
**no audit event of any kind that marks "this was a compensation, and it
failed"** — `AuditEvent.kind` only knew proposal/guardrail/authoritative
(pre-rename term; the gap this paragraph records is what later grew the
vocabulary — see "Structured output correction"), and
the orchestrator surfaces its `compensation_failed` flag only through
observability (`trace`), never through the audit writer. The pattern promises
audit answers "what the agent proposed, what the guardrail decided, what the
system-of-record target validated (then named "authoritative")" — it had no
vocabulary for "what compensation did." With the in-memory mock this scenario was literally unobservable (state
died with the process); the SQLite row is what makes the gap undeniable.

**Did the DB constraint catch anything the in-memory validation passed?** Yes,
two cases, both observed the moment real persistence arrived:
1. The old mock validated "slot not taken" against per-process dicts, so
   *running the script twice* was idempotent by accident. With SQLite, the
   second run of the legacy fixed payloads genuinely collides — both plans come
   back `compensated` (`slot '2026-08-03T10:00' is already booked`, `customer
   already has an open request`) with only the first run's rows persisted. The
   runner was deliberately **not** changed to dodge this; measurement rounds vary
   payloads per round instead.
2. The "one open request per customer" invariant did not exist in the in-memory
   mock at all — adding it as a constraint immediately exposed that the MCP
   gmail stub returned a **fixed sender** for every email, which would have made
   every inbound round after the first collide. The stub now derives the sender
   from the email id (distinct emails = distinct customers); the constraint
   test provokes the collision explicitly by reusing the same email id.

**Typed rejection at the boundary.** `IntegrityError` → 422
`{code, violation, detail}` → `ConnectorRejection` works end to end (tested at
connector and plan level). One observed limitation: the structured
`violation` field (`slot_taken` / `open_request_exists`) does not survive into
the plan-level `Rejection`, which carries only `code` + the human-readable
reason string — a consumer that needs to branch on the specific violation has
to parse prose.

**Does isolation resolve bend #5 (restriction 4 as verification, not a security
boundary)?** At the deployment boundary, yes — structurally: the `aici` image
does not copy `erp_service/`, receives no DSN/volume/credential for ERP storage
(only `ERP_BASE_URL`), and `tests/test_persistence_restrictions.py` verifies it
three ways (static scan of `src/**`, docker-compose parse, dynamic inspection
of the built Platform). Reaching business storage from the platform container
is now impossible by configuration, not discouraged by convention. What remains
unresolved is the *in-process* half of bend #5: inside one Python process a
forged `ExecutionContext` still passes; the container boundary is what turns
the restriction into a real wall. An honest gap surfaced in review: the runner
initially imported `erp_service` at module scope — precisely the coupling the
isolation forbids — and would have crashed the `aici` container; the import is
now lazy, confined to the explicitly local modes (`--serve-erp`,
`--erp inprocess`).

**Latency/cost impact vs. in-process:** measured — see the "Real boundary
latency" entry in the Real-model findings section above: ~20ms per execution
in-process (ASGI + SQLite) vs ~570ms over HTTP loopback with the local
provider (ERP path isolated); dominated by the connector's client-per-call
bridge, material but non-dominant next to real model latency, and invisible in
cost per completed task (cost is model-only).

## Structured output correction (2026-07-27/28)

Re-measurement after enforcing structured output at the decoding level and
implementing `retry_once_then_fail`. Same conditions as the original round:
`gemini-3.1-flash-lite`, 10 runs per plan, fresh ERP SQLite, `--erp inprocess`.
Baseline: run `20260727-212338`; corrected: `results/runs/run-20260727-215811.json`.

> **Data provenance note (honest loss):** the baseline's raw per-execution
> file (`run-20260727-212338.json`) was deleted by an intermediate cleanup
> step before the repository was under version control — `results/` was
> gitignored at the time. Its aggregates survive in the table below and in
> the "Real-model findings" section; the corrected run and the
> dialect-failure run (`run-20260727-215429.json`) are intact. Consequence
> applied: `results/` is now tracked evidence, never ignored.

| | before (prompt-only) | after (responseSchema + retry) |
|---|---|---|
| generation contract violations | **10/10** (all silently absorbed) | **0/10** |
| ERP rows with valid `summary` | **0/10** (truncated fenced-JSON blobs) | **10/10** (verified directly in SQLite: 0 rows containing fences or raw JSON) |
| executions `completed` | 20/20 (deceptively) | 20/20 (genuinely) |
| inbound tokens in/out (mean) | 389.3 / 134.8 | 389.3 / **99.9** |
| cost per completed task (inbound) | $0.000300 | **$0.000247** (−18%) |
| cost per completed task (schedule) | $0.000058 | $0.000058 (unchanged — no generation step) |
| retries triggered | n/a (didn't exist) | **0** (and 0 second failures) |

**`responseSchema` eliminated the violation completely at N=10 — no residual
rate.** Every generation call returned raw schema-conforming JSON; both
detectors (post-hoc model-log analysis and the new audit counters) read zero.
The output also got *cheaper*: constrained decoding emits no fences and no
discardable prose, cutting inbound output tokens by ~26% and cost per completed
task by 18%.

**Retry cost: not measurable, because no retry fired.** The mechanism's idle
overhead is zero extra model calls. Its failure path was exercised only in
tests (simulated violation → 1 audited retry → recovery; forced double
violation → typed `OutputContractViolation`, plan non-completed, nothing
persisted).

**The mechanism DID fire in anger once — by accident, and it worked.** The
first corrected run (`run-20260727-215429.json`) passed the schema through
Gemini's `response_schema` config field, which accepts an **OpenAPI-subset
dialect** and rejected the standard-JSON-Schema key `additionalProperties`
with a live 400. Result: 10/10 inbound executions failed **visibly and typed**
with the ERP at **zero rows** (confirmed in SQLite) instead of completing with
garbage. (An earlier version of this paragraph also claimed "correction
e-mails sent" — the controlled reproduction below **falsified that detail**:
the fault precedes the irreversible step, so no e-mail ever goes out and the
`compensated` status is vacuous. The unverified embellishment is exactly what
turning an accident into a controlled condition exists to catch.) That is
precisely the behavioral inversion this task was about: the same class of
provider-side surprise that previously produced 10/10 silent data corruption
now produced 10/10 loud, unpersisted failures. Fix: the adapter now uses
`response_json_schema`, which accepts real JSON Schema verbatim. Secondary
finding: **JSON Schema is not portable verbatim across providers** — each has
a dialect (Gemini: two fields with different grammars; Anthropic: requires
`additionalProperties: false` + `required`; OpenAI: `strict` wrapper) — and
dialect translation is adapter responsibility, invisible to `core` and `ai`.

**The guard-only cell, reproduced as a controlled condition (2026-07-28).**
The accident above was not an experiment; this is. A measurement-only
fault-injection decorator (`GenerationFaultInjectionClient`) wraps the
provider and re-fences every generation response — the exact violation class
of the original round — with the local provider (deterministic, $0), the real
pipeline and persisted SQLite, 10 runs (`--repeat 10 --inject-generation-fault`;
data: `results/runs/run-20260728-001648.json`). Observed:

- inbound: **0/10 completed** (all typed non-completions); schedule-appointment
  (no generation step) **10/10 completed** — a built-in control arm;
- ERP: **0 service_requests** persisted, 10 appointments (control);
- audit: `output_contract` violation/retry/failed = 10/10/10 per the trail
  (the post-hoc model-log detector sees all 20 corrupted responses; the
  capability audits the first attempt's violation and the second only as
  `failed` — a vocabulary detail now on record);
- **zero e-mails sent, zero `compensation` events — the `compensated` status
  is vacuous in this shape.** The generation fault fires *inside* the
  `acknowledge` step, before its irreversible tool; the step never completes, so
  the orchestrator's completed-effect list is empty and "compensation" runs
  over nothing. The previously-cosmetic rough edge ("compensated with zero
  compensations") is hereby upgraded to a **misleading remediation signal**,
  measured: the status reads as if remediation happened when nothing was sent,
  rolled back, or corrected. Pattern amendment candidate: distinguish
  `failed_clean` (nothing to compensate) from `compensated` (compensations
  actually ran).

The cell's core promise held under controlled conditions — nothing unvalidated
persisted, every failure typed and visible, clean control arm — and the
reproduction paid for itself twice: it falsified an embellishment in the
accident narrative and converted a cosmetic status quirk into a measured
defect with a concrete fix.

**Was the `ModelRequest` change the only contract change needed?** Yes. One
optional field (`response_schema: dict | None = None`, backward compatible)
was sufficient; providers, the capability, the runner and the audit trail all
used existing surfaces (`AuditEvent.kind` was already a free string — the new
`output_contract` and `compensation` vocabularies required no core change).
The finding stands as recorded in the field's docstring: the original
`IModelClient` port could not express constrained output — a real expressive
limitation of the model port as it stood.

**The previous round measured a worst-case configuration — explicitly.** The
100% violation rate of the first real-model round was an artifact of
prompt-only output control on a flash-tier model against a parser demanding
raw JSON: without `responseMimeType` + schema, Gemini wraps JSON in markdown
fences *by default*, so "100%" measured that default, not the pattern. Against
the known enforcement-level gradient (prompt-only weakest; JSON mode
intermediate; native structured output strongest), our before/after (100% →
0/10) is a **replication of the enforcement-level gradient, not a discovery**.
*(Correction, 2026-08-06 citation audit: an earlier version of this sentence
attributed specific per-level failure rates — 8–15% / 2–5% / <0.1% — to
arXiv 2606.09863. That paper contains no format-enforcement measurements;
the figures circulate only in vendor engineering literature. The numbers are
withdrawn per this study's observed-not-speculated criterion; the qualitative
ordering stands on the measurement above.)*
What the study adds is the downstream evidence: with persistence in place, the
un-enforced configuration didn't just fail to parse — it silently wrote
corrupted business records while reporting success.

**Conclusion for the study.** Constrained decoding solves content integrity at
the token level — so does the pattern still need an architectural restriction
for it? The intermediate run answers: **both, in different roles**. Prescribing
native structured output (schema declared in the capability, enforced by the
provider) is what makes violations *rare*; the architectural contract check
with `retry_once_then_fail` is what makes the residue — provider dialect
surprises, model regressions, misconfigurations — *visible and non-persisting*
instead of silently corrupting business state. The decoding constraint is the
optimization; the architectural check is the boundary guard. The prototype's
evidence for this is no longer a patchwork of two accident stories and one
local experiment — it is a **single reproducible condition**: the 2×2
integrity matrix below, one codebase, one model, axes varied only by flags.

## Integrity matrix — controlled reproduction (2026-07-28)

One experiment replaces the earlier patchwork of evidence (the tolerant-parsing
accident of the original round, the dialect accident, and the local-provider
injection run): a 2×2 matrix over the two axes the study actually argues about,
run as a **single controlled condition** — same codebase, same model
(`gemini-3.1-flash-lite`), same prompts, everything varied only by flags
(`--matrix-cell A|B|C|D|control`). Axes: **decoding constraint** (real
`responseSchema` vs. schema stripped) × **boundary behavior** (tolerant repair
vs. strict `retry_once_then_fail` guard). Cells A and B additionally run the
fault injector (`GenerationFaultInjectionClient`, re-fences every generation
response); the control arm runs the D configuration on the schedule plan only
(no generation step). 10 reps per cell, 3 for control — 43 executions, each
with its own fresh SQLite. Data: `results/integrity-matrix/integrity_matrix.jsonl`,
`results/integrity-matrix/integrity_matrix_report.json`, per-cell DBs preserved as **tracked
evidence copies** in `results/integrity-matrix/matrix-{A,B,C,D,control}.sqlite` (immutable
snapshots taken after the run — the cell-A garbage rows are directly
inspectable from a fresh clone; the live runtime originals stay in gitignored
`var/erp/`),
audit trails `results/integrity-matrix/audit-matrix-*.jsonl`.

> **The A/B violation rate is forced, not natural.** The injector re-fences
> *every* generation response by construction, so 10/10 violations in A and B
> measures the injector, not the model. The natural no-schema rate on this
> model was measured in the original round (10/10 fenced, itself an artifact of
> flash-tier defaults — see "Structured output correction"). The matrix asks a
> different question: *given* a violation, what does each configuration do
> with it?

| | reported status | contract viol/retry/fail | repairs | ERP rows | valid summaries | garbage rows | tokens in/out | cost (10 reps) | latency mean |
|---|---|---|---|---|---|---|---|---|---|
| **A** no schema + fault + tolerant repair | `completed` 10/10 | 0 / 0 / 0 | **10** | 10 | **0** | **10** | 3893 / 1376 | $0.00304 | 14.1 s |
| **B** no schema + fault + strict guard | `failed_clean` 10/10 | 10 / 10 / 10 | 0 | **0** | — | 0 | 4545 / 2636 | $0.00509 | 11.5 s |
| **C** responseSchema + tolerant repair | `completed` 10/10 | 0 / 0 / 0 | 0 | 10 | 10 | 0 | 3893 / 995 | $0.00247 | 24.8 s¹ |
| **D** responseSchema + strict guard | `completed` 10/10 | 0 / 0 / 0 | 0 | 10 | 10 | 0 | 3893 / 993 | $0.00246 | 10.6 s |
| control (D config, schedule plan only) | `completed` 3/3 | 0 / 0 / 0 | 0 | 3 appts | n/a | 0 | 618 / 12 | $0.00017 | 16.7 s¹ |

¹ Latency means are contaminated by 429-retry backoff waits (see quota note
below), not model behavior: per-cell stall counts (>30 s) were A=2, B=2, C=3,
D=1, control=1, with a worst case of 111 s in C.

**What the reproduction confirmed.**

- **With real `responseSchema` (C/D), the repair axis is invisible.** Zero
  natural violations at N=10 in both cells; tolerant vs. strict produced
  byte-equivalent outcomes (10 clean rows, ~same tokens, ~same cost). This
  replicates the earlier 0/10 and sharpens it: at this violation rate the
  boundary guard has literally nothing to do, so its idle cost is zero.
- **Without the schema, the boundary axis alone decides the outcome.** Same
  fault, same model, same pipeline: A reports `completed` 10/10 and persists
  10 garbage rows; B reports `failed_clean` 10/10 and persists zero. The
  entire difference between silent corruption and clean visible failure is the
  guard.
- **The status and the audit counters are not evidence — the DB is.** Cell A's
  contract counters read 0/0/0, because the tolerant path absorbs the
  violation before the contract check ever sees it; only `repairs = 10` and
  direct DB inspection reveal what happened. Verified in SQLite: all 10 rows
  in `matrix-A.sqlite` carry a double-fenced ` ```json ` blob stuffed into the
  `summary` column, `status = 'open'`, indistinguishable from legitimate
  records to any downstream consumer. `matrix-B.sqlite`: zero rows.
  `matrix-C/D.sqlite`: 10 one-line valid summaries each.
- **The control arm isolates the generation step.** Schedule-appointment (no
  generation) completed 3/3 with 3 appointments and zero service requests —
  the axes touch nothing else in the pipeline.
- **Strict failure costs more per failure than tolerant "success".** B spent
  ~2× A's output tokens ($0.00509 vs. $0.00304): the retry doubles the
  generation call, and both attempts fault. The guard's price is one extra
  model call per violation — the price of *knowing*.

**What it falsified or corrected.**

- The earlier `compensated` reading is gone: `failed_clean` (introduced with
  the 3-way failure statuses) changed **only cell B's** reading. The fault
  fires inside the `acknowledge` step, before its irreversible tool, so the
  completed-effect list is empty, zero compensation *attempt* events fire —
  the trail carries only the always-emitted `outcome` record
  (`compensable_steps: 0`, 10/10 in `audit-matrix-B-*.jsonl`) — and the old
  status would have claimed remediation where none ran. B is the measured
  proof that the status split was needed — and no other cell's reading moved.
- Tolerant repair is not "repair". What the historical fallback (emulated here
  by an infra decorator, replicating the original round's behavior) actually
  does is *conceal*: it strips fences well enough to satisfy the parser and
  then persists whatever is left, reporting success. "Repaired" 10/10,
  valid 0/10.

**Quota reality (recorded as part of the evidence).** The 43 executions could
not be produced in one sitting: the model's daily free-tier quota was exhausted
mid-run (~460 calls that day across re-measurement, selection sweep, and cells
A/B), cell C stalled at 4/10 for roughly nine hours across the daily-quota
boundary, and the run required multiple idempotent resumes
(`scripts/resume_matrix.py`, which skips recorded (cell, rep) pairs). A 429
with 44 s backoff also fired inside the final control cell. Consequences:
per-cell wall-clock and latency aggregates mix model latency with rate-limit
backoff (flagged in the table), and timestamps inside `matrix-C.sqlite` span
the boundary. None of this affects row counts, statuses, or token/cost
figures, which are per-call.

## Large-catalog selection sweep (N = 2..40, 2026-07-28)

The experiment the related-work review called "the only measurement that
changes the answer". Setup: selection-only harness
(`scripts/selection_sweep.py`) — 40 synthetic plans in 15 deliberately
confusable clusters; clear intents are paraphrases verified programmatically
to leak no plan id or trigger word; ambiguous intents are plausible for ≥2
same-cluster plans; 3 out-of-catalog intents per size probe restriction 7 at
scale. The prompt is byte-identical to the Orchestrator's real
plan-selection prompt. 240 real calls (`gemini-3.1-flash-lite`, 2 reps),
run inside the free-tier quota via 4 chained resumable rounds
(~220k input tokens total, $0 on free tier, ≈$0.056 at list price),
LangSmith off. Data: `results/selection-sweep/selection_sweep.jsonl` / `_report.json`.

| N | overall | clear | ambiguous | none-detection | format violations | avg input tokens/call |
|---|---|---|---|---|---|---|
| 2 | 100% | 100% | — | 100% | 0 | 116 |
| 5 | 100% | 100% | 100% | 100% | 0 | 214 |
| 10 | 94.1% | 100% | 75.0% | 100% | 0 | 372 |
| 20 | 91.2% | 100% | 72.7% | 100% | 0 | 717 |
| 40 | 90.9% | **95.0%** | 75.0% | 100% | 0 | 1,396 |

**The headline result is the same-set counterfactual.** On the identical test
set, keyword routing (the local stub — and, by extension, any trigger-word
router) scores ≈0% on clear intents, because the paraphrases deliberately
share no vocabulary with the catalog; the model scores 95–100%. This is a
direct counterfactual with no cross-task comparison involved, and it is the
answer to the most likely objection ("you are paying an LLM to do
switch-case"): the LLM selection layer buys paraphrase robustness that
keyword routing cannot provide at any catalog size.
*(Reclassified, 2026-08-06 citation audit: the ≈0% is a construction
inference — the paraphrases share no catalog vocabulary by design — not a
recorded run; no `results/` file backs it. Withdrawn from README's
measured-results table; the queued replacement is a versioned
embedding-similarity baseline on the same set, which is the strong semantic
comparator a trigger-word stub is not.)*

**Floor, not slope.** Read per column, catalog size barely matters: clear
intents run 100 / 100 / 100 / 100 / 95 through N=40, and ambiguous intents
sit at a **constant ~73–75% floor from the moment confusable clusters exist
(N≥10), without worsening as N grows**. The aggregate decline (100% → 90.9%)
is a composition artifact of the eval set — larger catalogs contain
proportionally more ambiguous probes — not catalog-driven degradation. The
accurate statement is: within the measured range, selection accuracy is
nearly insensitive to catalog size; genuine ambiguity costs a fixed ~25%
misroute rate wherever it exists. Out-of-catalog detection is **100% at every
size** — restriction 7 (refuse, don't improvise) scales. Format violations:
**0/240** — the id-only selection protocol held without needing a response
schema. The honest *why*, connecting this to the structured-output round:
choosing one short id from an offered list is a trivially constrained output
task, which is what makes the selection contract naturally robust; generating
a structured object is not — that contract violated 100% until enforced at
the decoding level. The two results are one lesson about contract difficulty,
not a miracle of typed refusal. *(Correction, 2026-08-06 cross-model arm:
"naturally robust" was a family property, not a task property —
`gpt-4o-mini` violated the id-only selection format in 13/20 matrix
executions (verbose id responses), the study's first selection-format
violations; see § Cross-model family arm. The 0/240 stands as recorded
for its model; the generalization is withdrawn.)* Stability: 117/120 points identical across both reps; the 3 flips are
all at N=40 inside confusable clusters.

**On DACS (arXiv 2604.07911) — a design argument, not a measured comparison.**
Do **not** read our 90.9% against DACS's flat-context baseline (21.0–60.0%)
as evidence: the gap is dominated by task difficulty, not architecture — DACS
measures multi-turn steering under accumulated context pollution, ours is
stateless single-shot classification, and any single-shot classifier
(pattern-following or not) would land in a similar range. What survives is
the structural claim only: by confining selection to a fresh, single-purpose
call whose context contains nothing but the catalog, the pattern avoids *by
construction* the context-pollution mechanism DACS documents — a property of
the design, asserted, not demonstrated here. Claiming more would require
measuring this pattern on DACS's own task.

**Verdict on the standing self-critique ("the selection layer didn't pay its
cost").** Now answerable with data, in two halves. (a) The LLM earns its keep
on *paraphrase robustness* — the same-set counterfactual above (≈0% keyword
vs 95–100% model). (b) It does **not** earn silent authority over ambiguity: a
25% misroute rate on ambiguous intents at N≥10 argues that the pattern should
(i) keep explicit-plan invocation for known channels, and (ii) treat detected
ambiguity like it treats planlessness — clarify or refuse typed, rather than
choose silently. Selection cost also stops being negligible at scale: input
tokens grow linearly with the catalog (~35 tokens/plan entry; 12× from N=2 to
N=40), motivating catalog scoping/prefiltering before the selection call in
large deployments.

## Cross-model selection spot-check (2026-07-28, post-publication)

The README's honest-limitations list called the cross-model check "pending".
This is it — smaller than planned, for a reason that is itself a finding.

**The free-tier model landscape had shifted under the plan.** The TODO's
queued candidates aged out between planning and execution: `gemini-2.5-flash`
and `gemini-2.5-flash-lite` are closed to new accounts (404), and
`gemini-2.0-flash` has a free-tier request limit of **zero**. The available
current-generation alternatives — `gemini-3.6-flash` (thinking regime) and
`gemini-3-flash-preview` — both carry a **20-requests/day** free-tier cap
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`),
discovered by hitting it. The preview model additionally threw 503
capacity errors requiring a retry wrapper. Model availability is an
experimental variable, not an infrastructure constant — the same lesson the
integrity matrix taught about daily quotas, now on the axis of model access.

**What fit under the caps** (2 reps planned, rep 1 partially recorded before
each cap; data in `results/selection-sweep/selection_sweep.jsonl`, per-model
blocks in the report):

| model | n | correct | format violations | coverage |
|---|---|---|---|---|
| gemini-3.6-flash (thinking) | 24 | **24/24** | 0 | N=2, N=5 complete; N=10 clear 10/10 |
| gemini-3-flash-preview | 18 | **18/18** | 0 | N=2, N=5 complete; N=10 clear 4/4 |

Per-kind, both models: clear intents 100%, out-of-catalog refusal 100%
(6/6 each — restriction 7 transfers), ambiguous 1/1 each (barely sampled).

**Honest reading.** At N≤10, two additional model families — one of them a
thinking model — reproduce the measured model's selection profile exactly:
42/42 with zero format violations, on the byte-identical prompt. This
upgrades "single model family" to "spot-checked across three, unanimous in
the covered range". It does **not** extend the floor-not-slope ambiguity
claim or the N=20..40 range beyond the original model: those remain
single-family results. Full 240-point curves on a second model would need a
paid tier (~$0.06 at list price) or ~6 days of 20-per-day accumulation —
documented as the cost, not done.

The tool-type triad was renamed twice in one day — `AUTHORITATIVE` →
`CUSTODIAL` (morning) → `SYSTEM_OF_RECORD` (final), plus `REFERENCE` →
`READ_ONLY` and `EFFECTFUL` → `IRREVERSIBLE` — each pass with zero behavior
change (name-only diffs across the suite's 18 passed + 7 xfailed). Full
rationale, both collision searches, and rejected alternatives in
`docs/adr/ADR-001-system-of-record-classification.md`; the short version:

- **`AUTHORITATIVE` was dropped for a market-semantics collision.** During
  2026 the word consolidated in the agent literature around *the agent's
  mandate to act* (Ball, "Authority Is the AI Bottleneck", jan/2026; AppZen's
  "authority calibration", jul/2026) — an authorization reading that inverts
  what the axis means here (stewardship of business truth by the *target*,
  not agency granted to the *agent*).
- **`CUSTODIAL` lasted hours.** The dedicated collision search the ADR's first
  draft owed found a worse collision: custodial/non-custodial is the standard
  binary of the 2026 AI-agent crypto-wallet space (MetaMask Agent Wallet,
  Coinbase AgentKit) — same audience, inverted direction ("who holds the
  agent's keys", not "who holds the business truth"). Process lesson made
  explicit: **no name gets fixed without a collision search first.**
  `SYSTEM_OF_RECORD` passed its own check before adoption (Workday's "Agent
  System of Record" is a composition — a registry *of* agents — that leaves
  the base term's meaning intact; the full check is recorded in the ADR).
- **`READ_ONLY` and `IRREVERSIBLE` are borrowed, consolidated vocabulary** —
  deliberately so, with no novelty claim attached.
- **The guarantee-locus axis is the only one of the three with no found
  counterpart** — verified both by name and by behavior (vocabulary-agnostic
  sweep; the closest behavioral neighbor found is the operation-type
  read/write/commit `mode` field of arXiv 2605.10555 — a different axis than
  guarantee locus) — and the claim is kept narrow: **one semantic
  axis** (who validates the business invariant, as a first-class tool
  classification), not the triad, and not the registration-time enforcement
  mechanism (arXiv 2606.26924 — the injected-violation conformance neighbor;
  an earlier draft mis-cited 2606.04017, which is about epistemic integrity).
  *(Delimitation, 2026-08-06 review round: "no found counterpart" is bound to
  a keyword search at its date, over a literature that indexes by consequence
  (reversibility, blast radius), not by this axis. The method's
  false-negative rate is measured by example — arXiv 2606.22916, an adjacent
  authorization-axis paper with v1 of 2026-06-22, existed during this sweep
  and surfaced only in a later external round. Absence findings are
  search-bound, never absolute; full delimitation in RELATED_WORK § 3.)*

Historical records in `results/` keep the original strings (`authoritative` —
the intermediate `custodial` never reached persisted evidence) — they are
evidence, not documentation — and any reader script must accept both
vocabularies.

## Schema+fault cells E and F (2026-08-06)

The two cells completing the fault arm of the integrity matrix — real
`responseSchema` + `GenerationFaultInjectionClient` under each boundary
policy — pre-registered in `docs/preregistration-schema-fault-cells.md`
(committed before the runner change and before any number), run same-day on
the matrix's model, 10 reps each. Data appended to
`integrity_matrix.jsonl`; snapshots `matrix-E.sqlite` / `matrix-F.sqlite`
tracked; `verify_costs.py` reproduces all 63 recorded costs exactly.

| | status | viol/retry/fail | repairs | ERP rows | valid | garbage | cost/task |
|---|---|---|---|---|---|---|---|
| **E** schema + fault + tolerant | `completed` 10/10 | 0/0/0 | 10 | 10 | **0** | **10** | ≈$0.000246 |
| **F** schema + fault + strict | `failed_clean` 10/10 | 1/1/1 per rep | 0 | **0** | — | 0 | ≈$0.000364–397 |

**The pre-registered prediction was falsified — Branch B, not Branch A.**
Three independent predictions (the pre-registration's own "cell E is where
a discovery is possible" reasoning, this study's review round, and an
external reviewer) expected fence-stripping repair to recover the valid
JSON under the injected fence. Measured: it does not extract — it
**absorbs**. All 10 of cell E's persisted `summary` fields carry the raw
fenced blob (single-fenced this time; cell A's were double-fenced), with
the cell-A signature intact under an *active* schema: contract counters
0/0/0, ten "successful" repairs, ten corrupted business records. The
counterfactual "tolerance would have recovered it" is measured false for
this repair implementation, not conceded hypothetically.

**What each cell adds.** E is the second observed instance of the paper's
Proposition 1 under a different decoding condition — the blindness is
positional, indifferent to what sits upstream of the absorption. F is the
verification it was pre-declared to be (deterministic by construction:
the injector re-fences the retry), with one new visibility: its typed
errors *display* the refused content, which is schema-valid JSON — the
guard refused corruption that was recoverable in principle, and the
tolerant path did not recover it either. The clean sentence is about E,
not F: the alternative to refusal was not recovery; it was corruption.

**Still not a complete factorial.** The no-schema/no-fault arm (natural
violations under each boundary policy) has never run as a matrix cell —
the pre-matrix incident round covers only its tolerant half.

Quota note: one 429 (~48 s backoff) inside each cell's run; latency
aggregates carry those stalls, per-call tokens/costs unaffected.

**Name collision search — "telemetry blindness" (2026-08-06, run per the
no-unchecked-names rule after the term reached the paper's title).** No
named counterpart found. The nearest industry phrase is "telemetry blind
spots" — coverage gaps, areas the instrumentation does not observe — which
is semantically distinct: telemetry blindness as defined here is full
coverage reading *confidently wrong* (every observer reports success),
not partial coverage. The nearest academic neighbor is the
"fail-plausible" class of arXiv 2606.14589 — a *different* specialization
of gray failure's differential observability: there the failure fabricates
narrative that deceives the observer; here nothing is fabricated — the
violation is absorbed upstream and every observer honestly reads success.
Sibling specializations, not a collision; distinction recorded in the
paper's related work. Fresh mechanism sweep same date: the
repair-conceals warning remains prose-only in the practitioner literature
(json_repair caveats and 2026 posts); no measurement of counter-blinding
found. Read-queue additions from the sweep: arXiv 2607.14167 (structured
feedback vs.\ silent repair in agent loops), 2601.00481 (MAESTRO),
2606.01365 (failure-aware observability).

## Cross-model family arm — gpt-4o-mini, cells A and B (2026-08-06)

The family-diversity arm of the pre-registered cross-model run
(`docs/preregistration-crossmodel-integrity-AB.md`, amendment of
2026-08-06), on the model-suffixed evidence paths the runner gained the
same day. The tier arm (`claude-sonnet-5`) remains unrun — key not
provisioned by decision at run time. Evidence:
`integrity_matrix-gpt-4o-mini.jsonl`, its report, and tracked snapshots
`matrix-{A,B}-gpt-4o-mini.sqlite`; all 20 recorded costs recompute
exactly from tokens at the model's list rates ($0.15/$0.60 per 1M);
total spend for both cells ≈ $0.0015, zero stalls.

| | status | reached boundary | boundary outcome | ERP rows |
|---|---|---|---|---|
| **A** (tolerant) | `completed` 6 / `failed_clean` 4 | 6 | **6/6 garbage persisted, counters 0/0/0, repairs 6** | 6 (0 valid) |
| **B** (strict) | `failed_clean` 10/10 | 1 | 1/1 violation→retry→typed failure | **0** |

**Boundary axis: transfers where reached, with the sample stated.** Every
repetition that reached the generation boundary reproduced the original
reading in a third model family — tolerant absorbed and persisted fenced
garbage with the platform's counters at zero (telemetry blindness,
family #3), strict failed typed with nothing persisted. Honest bounds:
the strict cell's boundary sample is **one**; zero persistence held
10/10 in B regardless of failure locus.

**Branch C dominated — and falsified a task-robustness claim.** 13/20
repetitions failed *upstream*, on capability-selection response format:
`gpt-4o-mini` answers `id: read-and-reply` or a full sentence instead of
the bare id. These are the first selection-format violations in the
entire study (0/240 on `gemini-3.1-flash-lite`; 0/42 on two further
Gemini-family models), and they falsify, as a general claim, the sweep
section's "choosing one short id from an offered list is a trivially
constrained output task, which is what makes the selection contract
naturally robust": **the observed robustness was a property of the model
family, not of the task** (correction annotated in place). Per the
pre-registration, this selection result is reported separately from the
boundary axis, never composited. Restriction 7 held under the stress:
all 13 upstream failures were typed `failed_clean`, zero improvisation,
zero persistence.

## Open-weights arm — `gpt-oss-120b` via Groq, cells A and B (2026-08-06)

Third cross-model arm (amendment of the same date in
`docs/preregistration-crossmodel-integrity-AB.md`; rates $0.15/$0.60 per
1M recorded there). Evidence: `integrity_matrix-openai-gpt-oss-120b.jsonl`
+ report + tracked snapshots; all costs recompute exactly
(`verify_costs.py`: 123/123 across every evidence file).

| | status | boundary reached | outcome | ERP rows |
|---|---|---|---|---|
| **A** | `completed` 10/10 | 10/10 | garbage persisted, counters 0/0/0, repairs 10 | 10 (0 valid) |
| **B** | `failed_clean` 10/10 | **10/10** (contract 10/10/10) | typed failure per rep | **0** |

**Branch A, full transfer — and the mini arm's asymmetry resolved by
measurement, not words.** Unlike `gpt-4o-mini`, every repetition reached
the generation boundary in both cells: the id-only selection protocol
held in this family (0 selection-format violations), so the strict cell's
boundary sample is **10/10** — the full-sample boundary transfer the
family arm could not provide. Telemetry blindness reproduces with the
full cell-A signature (single-fenced blobs this time — garbage *shape*
varies by model, as the amendment's interpretive note anticipated; the
axis reading does not). Selection-format robustness is now measured
present in three configurations (Gemini family, `gpt-oss-120b`) and
absent in one (`gpt-4o-mini`) — reinforcing the family-property
correction, in both directions.

## Postgres system-of-record arm — cells A and B (2026-08-06)

Pre-registered in `docs/preregistration-postgres-sor.md` (including its
honesty note: the runner's generic Postgres support predates the
document; question and branches were fixed before any number). Baseline
model held fixed — the engine is the only variable. Evidence:
`integrity_matrix-pg.jsonl` + report + snapshot SQLite exports
(`matrix-{A,B}-pg.sqlite` — ground truth read live from Postgres, then
exported to the uniform tracked format).

| | status | outcome | ERP rows (PostgreSQL) |
|---|---|---|---|
| **A** | `completed` 10/10 | garbage persisted, counters 0/0/0, repairs 10 | 10 (0 valid) |
| **B** | `failed_clean` 10/10 | contract 10/10/10, typed failures | **0** |

**Branch A: the readings are engine-portable.** The same blindness
signature persisted into a PostgreSQL database with real declared
constraints validating, and the strict guard held zero persistence — a
SQLite artifact is ruled out as the explanation. The claim stays exactly
as registered: the *engine* varied, the *owner* did not; the self-built
system-of-record circularity remains open and declared. Forensic detail:
the Postgres cell-A blobs are double-fenced (the injector re-fenced an
already-fenced response, as in the original cell A), the `gpt-oss-120b`
ones single-fenced — model-dependent shape, engine-independent reading.

## Embedding-similarity selection baseline — measured (2026-08-06)

The pre-registered strong semantic comparator
(`docs/preregistration-embedding-baseline.md`: registered text + power
amendment + collapse-rule amendment, all dated before their numbers).
`gemini-embedding-001`, τ = 0.53 frozen on the disjoint calibration set
(100% there, 31 embedding calls) before the eval set was touched; 120
unique intents, 0 format violations, cost ≈ free tier. Evidence:
`embedding_baseline.jsonl` + report + the frozen calibration artifact;
paired analysis executable as `scripts/paired_selection_analysis.py`.

| kind | n | both | LLM-only | EMB-only | paired diff | p (deficit, one-sided) |
|---|---|---|---|---|---|---|
| clear | 77 | 70–71 | 4–5 | 0–1 | +3.9 to +6.5 pts | 0.1875 / **0.0312** (rule-dependent) |
| ambiguous | 28 | 20 | 1 | **7** | **−21.4 pts (embeddings ahead)** | 0.996 |
| out-of-catalog | 15 | 11 | 4 | 0 | +26.7 pts | 0.0625 |

**Outcome: Branch C, causes named.** The clear-intent decision flips with
the LLM rep-collapse rule (the three N=40 cross-rep flips sit exactly in
the discordant set) — undecidable at the margin by the amendment's own
definition; refusal stopped one discordant short of its catastrophic
criterion. **The descriptive result nobody predicted: embeddings beat the
LLM on ambiguous intents by 21.4 points (27/28).** The registered text
pre-committed this comparison as evidence about the eval set versus the
model — answered: **the ~75% ambiguity floor was substantially a property
of the measured model, not of the intent set** (third
correction-by-measurement of the study). The coherent narrowing, exactly
the shape the registered Branch A anticipated: the LLM selection layer's
measured justification narrows to **typed out-of-catalog refusal** (the
one kind where embeddings fell short, 4/15 missed); clear intents are a
near-tie at ~zero marginal cost; ambiguity favors the embedding
comparator.

## Open questions for the study

- Does plan-level compensation stay manageable at 5+ steps, or does reverse-order
  agent re-invocation start needing its own saga state?
- ~~At what agent count does keyword-free plan selection (real model) start
  misrouting, and is a typed output contract for selection enough?~~
  **Answered** (see "Large-catalog selection sweep"): misrouting appears at
  N=10 and concentrates almost entirely in deliberately ambiguous intents
  (~75% there, 95–100% on clear intents up to N=40); the id-only protocol
  held with 0/240 format violations. Remaining follow-up: ambiguity
  *detection* (clarify-or-refuse instead of silent choice) is unmeasured.
- The acknowledge-before-register ordering was forced by restriction 6 — is that
  ordering acceptable to the business in general, or does it push real systems
  toward splitting read/reply capabilities after all?
