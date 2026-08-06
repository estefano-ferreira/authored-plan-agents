# Authored-Plan Agents — Specification

**Status: draft.** No version number is assigned yet.

> This specification was extracted from a single reference implementation
> written before it. It therefore describes what that implementation does,
> which is not yet known to be the same as what the pattern requires — that
> distinction only emerges when a second, independent implementation is
> written against it. A version number will be assigned then.

Nothing in this document names a language, file, class or signature. The test
applied to every paragraph: *would this still be true of an implementation in
.NET, Java or TypeScript?* Implementation-specific decisions live in the
reference implementation's [`DESIGN.md`](DESIGN.md).

---

## 1. Scope

This pattern targets systems where **LLM agents write business state whose
invariants matter** — registrations, bookings, cancellations, refunds:
anything where a wrong *record* costs more than a wrong *sentence* — and
where the flows are known well enough to be written down and reviewed.

It is **not** for:

- systems with **no business state to protect** (FAQ bots, personal
  assistants, summarization, simple copilots) — there is no invariant for the
  pattern to guard;
- environments where **every write path already runs through server-side
  validation that nothing in the agent's wiring can bypass** — there the
  pattern names why the existing default is right, but adds no mechanism;
- **prototypes and single-domain tools** — the per-agent ceremony does not
  amortize until flows and agents multiply.

## 2. Motivation

The write-path discipline this pattern enforces is an old, solved one —
"clients don't write the database; the system that owns the rules does" —
being un-solved by agent-era wiring. The fastest way to connect an agent
today is a connector that wraps a database directly; the security literature
for agent connectivity documents the consequence as standard risk: direct
data pipelines, business-logic bypass, broad write scopes approved by default
([Supabase](https://supabase.com/blog/defense-in-depth-mcp),
[WitnessAI](https://witness.ai/blog/mcp-server-security/),
[Reco](https://www.reco.ai/learn/mcp-security)). The incident record shows
the cost: in [one analysis of 7,246 public AI incidents](https://www.cyera.com/research/agent-inflicted-damage-inside-the-real-world-failures-of-enterprise-ai-systems),
188 involve an autonomous system harming production with **no attacker in the
chain**. The everyday version is measured false success — the agent's
narrative diverging from the record — at
[45–48% of failures in single-control tau2-bench domains](https://arxiv.org/abs/2606.09863). The shared
structural feature: a write path to state that did not pass through a
validating business API.

## 3. Terminology

- **Plan** — a human-authored, versioned, reviewable declaration of a
  business flow: an ordered sequence of agent invocations with entry scopes,
  per-step compensation, and output mapping. Plans name agents and intents —
  never capabilities or tools. The declarative format (YAML, BPMN, JSON, …)
  is not specified; authorship and versioning are.
- **Agent** — a declaratively specified unit of business responsibility with
  a named owner, a business context, instructions, and a list of
  capabilities.
- **Capability** — a cohesive business ability implemented as an ordered tool
  sequence with dependencies, optional inference steps, and per-step
  compensation. Tool order inside a capability is written by a human, not
  chosen by the model.
- **Tool** — the atomic unit of effect, classified by the guarantee its
  target offers (see Tool types) and bound to a declared target.
- **System of Record** — the external business system that owns the business
  entities and their invariants, validates writes on its own side, and
  rejects violations with a typed error. It is outside the agent platform.
- **Orchestrator** — the platform's single entry point: resolves the plan,
  issues the execution context, executes plan steps in declared order, and
  runs the compensation protocol on failure.
- **Guardrail** — a policy scope (platform, agent, or plan) declaring what
  may run; scopes compose by intersection only.
- **Tool types** — `READ_ONLY` (reads, no business-state change);
  `IRREVERSIBLE` (outward effect with no guaranteed rollback);
  `SYSTEM_OF_RECORD` (a write to business state through the system that owns
  the invariant — the only permitted business-write classification).
- **The three authorities** — the separations the pattern exists to enforce:
  **sequence** is decided by a human (plan + capability order),
  **authorization** by policy (guardrails), **validity** by the domain (the
  system of record).

## 4. Architectural principles

1. **Non-determinism is confined to selection and generation — never to
   sequence.** The model may choose among authored plans, choose among an
   agent's capabilities, and generate content inside steps. It never decides
   what runs next.
2. **The platform holds no business entity.** Anything the platform stores
   is platform state (sessions, audit, telemetry); business truth lives
   exclusively in systems of record.
3. **Every decision has an owner and an audit voice.** What the agent
   proposed, what policy decided, what the system of record validated, what
   the output contract enforced, and what compensation did are recorded
   unsampled, distinguishably.

## 5. Mandatory constraints

Each constraint states its enforcement moment. An implementation conforms
only if enforcement happens **at that moment** — not later, not by review.

1. **System-of-record write path** *(registration time).* Every tool that
   writes business state must be classified `SYSTEM_OF_RECORD`, and every
   `SYSTEM_OF_RECORD` tool's target must be an external system that owns and
   validates its invariants. Registration must reject: a business-writing
   tool with any other classification, and a `SYSTEM_OF_RECORD` tool whose
   target is platform-side storage.
2. **No agent-to-agent calls** *(runtime).* Composition exists only in
   plans, executed by the orchestrator. The runtime must detect an agent
   execution initiated from within another agent execution and fail typed.
3. **Intersection-only policy composition** *(load time; evaluation at
   runtime).* Effective policy is the intersection of the applicable scopes.
   Loading must reject any narrower scope that introduces a permission
   absent from its wider scope. Every runtime allow/deny decision must be
   audited.
4. **Single entry** *(runtime, made structural at deployment).* Execution
   contexts are issued only by the orchestrator, and the runtime must refuse
   contexts it did not issue. In-process this is architectural verification,
   not a security boundary (see § 10); the deployment must additionally give
   the platform no path to business storage other than the systems of
   record's own APIs.
5. **Identity propagation** *(runtime).* The requesting principal must reach
   the tool invocation. Absent a user, a system principal carries the plan's
   declared scopes. A missing principal is a typed failure, not a default.
6. **Compensation ordering** *(plan load time).* A step with an irreversible
   effect and no compensation must be the last effectful step of its plan.
   Plans violating this must be rejected at load.
7. **Typed refusal** *(runtime).* A request that resolves to no plan is
   refused with a typed rejection — never improvised. Selection output is
   constrained to the offered identifiers or an explicit "none".

**5.1 Output-contract enforcement on the write path** *(runtime).* Generated
content that feeds a `SYSTEM_OF_RECORD` write must be validated against a
declared output contract at a boundary the generator cannot bypass; on
violation, at most a bounded retry, then a typed failure — nothing
unvalidated is ever persisted, and tolerant repair (accepting malformed
output by fixing its syntax) is non-conforming on this path. *Note:* this
requirement is not one of the seven numbered constraints; it was found
load-bearing by the reference study (its measured guarantee about
unvalidated content rests on it), which is why it is normative here.
Promoting it to a numbered eighth constraint is a recognized candidate for
the first versioned revision — conditional on giving it what the other seven
have: a portable violation probe that commits the violation on purpose and
requires the enforcement to catch it.

## 6. Optional elements

The pattern admits, but does not require:

- **Plan-scope guardrails.** Platform and agent scopes are mandatory
  composition inputs; a per-plan scope is admitted (same intersection rule).
- **Intra-capability compensation.** Per-tool-step compensation inside a
  capability is part of the capability contract but may be unused; plan-level
  compensation is the load-bearing mechanism.
- **A long-term knowledge store.** Session memory is part of the platform's
  context discipline; cross-session semantic memory is optional per agent.
- **Model-based plan selection.** A channel that knows its plan may invoke
  it by explicit key; this is equally conforming, cheaper, and removes the
  selection error class entirely. Model-based selection is the fallback for
  natural-language entry, not a requirement.

## 7. Architectural guarantees

An implementation satisfying § 5 provides:

1. **Deterministic execution order** — the model selects and generates,
   never sequences; model-call count per execution is bounded and known per
   plan.
2. **Business validity decided outside the platform** — no business entity
   exists platform-side.
3. **No policy composition ever widens permission.**
4. **Business state is written only through the system that owns the
   invariant**, and its rejection reaches the caller typed.
5. **A generation-contract failure never persists unvalidated content**
   (rests on § 5.1).
6. **Every decision is auditable, unsampled** — including compensation and
   its own failure, with the orphaned state recorded.

(The reference repository's README carries the measured evidence behind each
of these for one implementation; the guarantees above are what any
implementation must be able to claim.)

## 8. Conformance

**What conformance means today:** implementing the seven constraints of § 5
plus § 5.1, each enforced at its declared moment, with the audit vocabulary
of § 4, principle 3, distinguishable in the trail.

**What does not exist yet:** a language-independent conformance suite. The
only executable criteria today are the reference implementation's own tests —
including a strict anti-vacuity suite that commits each violation on purpose
and requires the enforcement to catch it — and they are coupled to that
implementation's language and structure. A mature specification needs
portable conformance cases (given-this-plan, expect-rejection-at-load;
given-this-tool-declaration, expect-rejection-at-registration; …). That work
is acknowledged and not done.

Until it exists, "conforming" is a claim an implementer argues, not one a
suite certifies.

## 9. Reference implementation

One reference implementation exists — the repository this specification
lives in: a minimal end-to-end platform, an external system of record, the
restriction-enforcing test suite, and three cycles of real-model measurement
with tracked evidence. It is *an* instance of the pattern, not the pattern:
its language, module layout, provider adapters and measurement instrumentation
are implementation decisions, documented separately in `DESIGN.md`.

## 10. Security considerations

What the pattern does **not** guarantee, stated before what it does:

- **Constraint 4 is architectural verification in-process.** Within one
  process, context checks are forgeable by construction; they prove the
  architecture rejects side entries, not that an attacker can't. The
  constraint becomes a real wall only at a deployment boundary (separate
  services, no platform credential or route to business storage).
- **No protection against a same-process attacker.** Real enforcement
  requires process or authentication boundaries, which are the deployment's
  job, not the pattern's.
- **Content integrity inside a valid schema is not covered by the authority
  constraints.** Identity, policy, and invariant validation can all hold
  while semantically wrong content persists; § 5.1 bounds the *form* of that
  channel, not the quality of meaning. Content-quality verification is out
  of scope.
- Identity propagation transports *who is asking*; it is not itself
  authentication. The system of record decides how much to trust it.

## 11. Known limitations

Observed in the reference study; expected to apply to any implementation:

- **Constraint 6 collides with capability granularity.** When a cohesive
  capability bundles a read and an irreversible effect, the natural business
  order may be unexpressible; the pattern forces the trade-off into an
  explicit, reviewable decision rather than resolving it.
- **Compensation of an irreversible step is forward correction, not
  rollback** — it re-invokes generation with a correction intent, so its
  quality leans on generation quality.
- **Genuine intent ambiguity has an error floor.** Where distinct plans are
  legitimately plausible for one utterance, model selection misroutes at a
  roughly constant rate regardless of catalog size; implementations should
  prefer clarify-or-refuse over silent choice, and explicit-key invocation
  for known channels.
- **Selection cost grows linearly with catalog size.** Large catalogs need
  scoping or prefiltering before the selection call.
