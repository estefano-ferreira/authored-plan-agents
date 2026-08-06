# Related work — does this pattern already exist?

Deep-research survey (2026-07-27) comparing the proposed pattern against existing
frameworks, products, standards and academic literature. Method: three parallel
research sweeps (orchestration frameworks; governance/security mechanisms;
naming and academic priors), synthesized here. Every claim carries a source.

**Headline verdict: the composite does not exist under a single name; every
individual element has a strong, citable precedent.** The defensible
contribution is a legitimate synthesis carrying two sharp mechanisms (the
guarantee triad with system-of-record enforcement; load-time compensation
ordering) plus measurement in a space where the comparable architectures do
not measure. This supports an honestly-framed adoptable pattern — not a claim
of a new architecture. See the weighted contribution hierarchy in the Verdict.

---

## 1. The central rule (non-determinism confined to selection + generation)

| Neighbor | How close | Key difference |
|---|---|---|
| Anthropic, ["Building Effective Agents"](https://www.anthropic.com/research/building-effective-agents) | Conceptual origin of the workflow/agent dichotomy this pattern lives on | In orchestrator-workers, the LLM *invents* the subtask set; here the plan is human-authored — the model never creates steps |
| [Salesforce Agent Graph — "guided determinism"](https://engineering.salesforce.com/agentforces-agent-graph-toward-guided-determinism-with-hybrid-reasoning/) | Closest enterprise articulation: "orchestration as design-time configuration, not runtime improvisation"; LLM decides intent/topic, Agent Script authors transitions | Covers only the plan/execution slice — no saga, no scoped guardrails, no tool classification, no audit vocabulary |
| [Microsoft Conductor](https://opensource.microsoft.com/blog/2026/05/14/conductor-deterministic-orchestration-for-multi-agent-ai-workflows/) (May 2026) | Closest to the plan artifact: agents + routing in a single **version-controlled, diffable YAML**, "no LLM in the orchestration loop" | No tool classification, no compensation, no identity propagation, no agent-call-agent prohibition stated |
| [Declarative Language for LLM Agent Workflows (PayPal, arXiv 2512.19769)](https://arxiv.org/abs/2512.19769) | Closest academic prior in spirit: human-authored declarative DSL, non-determinism confined, production-validated | No compensation/saga, no typed refusal, no layered contracts |
| [Camunda agentic process orchestration](https://docs.camunda.io/docs/components/agentic-orchestration/ao-design/) | Deterministic BPMN backbone + confined agent zones; **native compensation boundary events** (reverse-order cascade) | Inside ad-hoc subprocesses the agent decides task *order* — exactly what this pattern forbids |
| LangGraph / [OpenAI Agents SDK](https://github.com/openai/swarm) / Flowise | Popular, mature — and **antithetical**: model-decided routing and agent→agent handoffs are their central feature | This pattern prohibits what they sell |
| Plan-and-Execute / ReWOO / [LLMCompiler](https://arxiv.org/pdf/2312.04511) | Share "plan before execute" | The plan is **LLM-generated per run**; here it is human-authored and versioned — opposite philosophies of plan origin |
| [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) | Philosophical kin ("own your control flow", "tools are structured outputs") | A checklist of engineering heuristics, not a composed reference architecture |
| ["Design Patterns for Securing LLM Agents against Prompt Injections" (arXiv 2506.08837)](https://arxiv.org/abs/2506.08837) — ETH Zürich / Google DeepMind / IBM / Microsoft, 14 authors | **The closest articulation of the central rule, arrived at from a third motivation**: Action-Selector (the LLM cannot decide control flow) and Plan-Then-Execute (the plan is fixed before execution) as *provable* prompt-injection defenses | Their plan is **LLM-generated per run** and the frame is security; here the plan is human-authored/versioned and the frame is integrity + determinism. Three independent roads — determinism (Conductor), cost, security (this paper) — converge on the same rule, which makes the rule plausible and dilutes any single-owner claim to it; convergence is consilience, not evidence — the integrity matrix, not the number of parties agreeing, is what carries the rule here |
| [Invariant Engineering (Eledath, abr/2026)](https://www.bassimeledath.com/blog/invariant-engineering) | The crispest practitioner articulation: "a prompt that says ask the five questions in order is a suggestion; a harness that only surfaces question 3 after 2 is an invariant" — the harness owns sequencing, not the LLM | Blog-level guidance, no platform composition, no measurement |

## 2. Restriction 2 (agent never calls agent) — already named

The [Akka "golden rule"](https://doc.akka.io/concepts/ai-orchestration-patterns.html)
states it almost verbatim: direct agent-to-agent calls bypass platform
mediation and "give up durability, retries, and audit"; all coordination goes
through a supervisor/workflow. This restriction is prior art, not a contribution.

## 3. Tool classification by guarantee (SYSTEM_OF_RECORD / READ_ONLY / IRREVERSIBLE)

- Closest mechanism: [MCP tool annotations](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`) — but the MCP maintainers
  are explicit that annotations are **untrusted hints**, and that real guarantees
  "belong to the authorization, transport or runtime layer". That is precisely
  the gap the SYSTEM_OF_RECORD mechanism fills (guarantee enforced by a database
  constraint in the system of record, not asserted by the tool). The
  [MCP 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
  keeps the hints-only stance (its changes are protocol-mechanical), and the
  live SEP discussions extend the *sensitivity* vocabulary (`sensitiveHint`,
  `egressHint`), not guarantee locus.
- [OWASP Agentic Top 10 — Excessive Agency](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
  covers autonomy/permission, not transactional guarantee.
- Dedicated follow-up search (2026-07-28): the nearest named neighbor is the
  **reversible/irreversible write split** in the capability vocabulary of
  ["Skills as Verifiable Artifacts" (arXiv 2605.00424)](https://arxiv.org/pdf/2605.00424)
  (`fs.write.rev` / `fs.write.irrev` as the load-bearing distinction) — a
  *reversibility* axis, close to READ_ONLY/IRREVERSIBLE. What remains unfound is
  the **guarantee-locus** axis: classifying a write path by *who validates the
  invariant* (an external system of record, by constraint, vs. the platform),
  with "no business entity on the platform side" as a rule. The narrowed
  claim: the triad's reversibility half has named neighbors; its
  guarantee-locus half does not. **Delimitation (2026-08-06): that absence
  claim is time- and method-bound** — keyword search at its date, over a
  literature that does not index by this axis (every taxonomy found
  classifies by consequence: reversibility, blast radius, privilege). The
  method's false-negative rate is measured by example: an adjacent
  authorization-axis result ([arXiv 2606.22916](https://arxiv.org/abs/2606.22916),
  v1 of 2026-06-22 — server-side, payload-bound enforcement over declared
  manifests; the guarantee-belongs-to-the-validator argument on the
  authorization axis) existed during the July sweep and surfaced only in a
  later external review round. Absence findings in this document mean "not
  found by this search as of its date", never "does not exist". DDD's aggregate-as-invariant-guardian is
  decades old, and its application to LLM agents is emerging in 2026
  ([Thoughtworks](https://www.thoughtworks.com/insights/blog/generative-ai/your-agent-skill-not-anti-corruption-layer),
  [Value Iteration](https://www.valueiteration.com/insights/data-modeling-ai-agents-systems-of-record))
  but is not yet a formal prescription. **Candidate contribution.**
- Behavior-first verification sweep (2026-07-28, vocabulary-agnostic — searched
  the *mechanism*, never the name). Strongest neighbors found, each organized
  around a different axis than guarantee locus:
  - ["Agent-First Tool APIs" (arXiv 2605.10555)](https://arxiv.org/html/2605.10555v1) —
    the only first-class, checkable tool-classification *field* found anywhere:
    a per-tool `mode` of read / write / commit, inside a six-layer validation
    pipeline. Axis is **operation type**, not who validates the invariant; no
    thin-platform rule. Closest behavioral near-miss on record.
  - [Workday Agent System of Record](https://joshbersin.com/2026/04/the-reinvention-of-workday-from-system-of-record-to-platform-of-agents/) —
    "agents read from Workday, process externally, write back through validated
    APIs, every mutation audited": the write-back-through-the-SoR *behavior* as
    product governance, with no tool typing and no prohibition on platform-side
    state.
  - ["Five-Plane Reference Architecture" (arXiv 2606.12320)](https://arxiv.org/pdf/2606.12320)
    and the ["System of Intelligence" pattern (jan/2026)](https://theagentichive.com/the-system-of-intelligence-pattern-architecting-enterprise-ai-that-respects-systems-of-record-037430da3b34) —
    the near-twins on the *thin-platform clause*. SoI Invariant 1, verbatim:
    *"Everything the system of intelligence stores must be reconstructible from
    systems of record and raw logs"* (its other invariants — typed interfaces,
    runtime policy, saga-bounded writes — echo in 2606.12320's "derived/
    rebuildable state" safety invariants). Both prescribe **reconstructibility**
    of platform state; this pattern's restriction is stricter and different in
    kind — the platform may not *own* a business entity at all (absence, not
    rebuildability), and the rule is enforced per-tool at registration.
  - Honest framing note: "no source composes all N elements" is a weak claim
    shape — enough conjuncts make anything unique. The load-bearing statement
    is behavioral: the integrity matrix measures what the boundary does when
    corrupted content reaches it (silent persistence vs. clean typed failure),
    and no neighbor above measures its own mechanism at all.

## 4. Guardrails composed by intersection across three scopes

- Formally near-identical semantics exist — in cloud IAM, not agent platforms:
  [AWS permission boundaries + SCPs](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html)
  ("effective permissions are the intersection").
- LLM guardrail products (NeMo, Bedrock Guardrails, [LiteLLM policies](https://docs.litellm.ai/docs/proxy/guardrails/guardrail_policies))
  layer by *pipeline stage* (input/exec/output), not by *organizational scope
  with intersection-only composition and load-time widening rejection*.
  **Candidate contribution: importing IAM's intersection semantics into agent
  platform policy, enforced fail-closed at load.**

## 5. Compensation (saga) declared per step, validated at load

- Saga is classic (Garcia-Molina 1987); BPMN compensation boundary events are
  mature industry practice ([Camunda](https://docs.camunda.io/docs/components/modeler/bpmn/compensation-events/));
  [SagaLLM (VLDB 2025)](https://www.vldb.org/pvldb/vol18/p4874-chang.pdf)
  transposes saga to LLM planning; AWS publishes
  [saga orchestration patterns for agentic AI](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/saga-orchestration-patterns.html).
- Not found anywhere: **the static rule "an effectful step without compensation
  must be the last effectful step", validated at plan load time.** SagaLLM
  validates at runtime/planning. **Candidate contribution (small but sharp).**

## 5b. Durable-execution runtimes — the most populated neighborhood (added 2026-08-06)

- The category this document previously did not map: Temporal, DBOS, Restate,
  and LangGraph's checkpointers treat **durable execution** as a first-class
  agent feature. Temporal's workflow/activity split is structurally this
  pattern's separation between deterministic sequence and non-deterministic
  model calls — workflows must be deterministic and cannot perform I/O; LLM
  calls live in activities. The agent ecosystem has adopted it explicitly:
  [Pydantic AI ships Temporal/DBOS/Prefect integrations](https://temporal.io/blog/build-durable-ai-agents-pydantic-ai-and-temporal),
  Temporal ships a [LangGraph plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)
  and an [OpenAI Agents SDK integration](https://docs.temporal.io/ai-cookbook/openai-agents-sdk-python).
- The first question a distributed-systems reviewer asks — *why not wrap a
  LangGraph agent in a Temporal workflow?* — has a structural answer: durable
  execution guarantees that the **execution** completes, replays, and
  survives crashes. It says nothing about whether the persisted **content**
  is business-true: a Temporal workflow persists garbage perfectly durably
  and perfectly auditably. Integrity-matrix cell A is exactly that artifact —
  a durable, fully-logged, wrong record. The validity authority is
  **orthogonal to durable execution, not redundant with it**.
- Consequence for the map: durable runtimes are candidate **hosts** for the
  sequence authority (an authored plan could compile to a Temporal workflow;
  the pattern's poster is format-agnostic on this), while the validity
  decision still has nowhere to live inside them — it belongs to the system
  of record. Their replay determinism is execution-level, not content-level.

## 6. Identity, audit, refusal, metric

- Identity to the tool: equivalent in spirit to OAuth Token Exchange
  ([RFC 8693](https://www.ietf.org/archive/id/draft-oauth-ai-agents-on-behalf-of-user-01.html) /
  MCP authorization spec); `SystemPrincipal` with plan-declared scopes is a
  local variation, not a contribution.
- Unsampled audit separate from telemetry: established compliance practice and
  soon regulation ([EU AI Act Art. 12](https://artificialintelligenceact.eu/article/12/));
  the five-kind vocabulary (proposal/guardrail/system_of_record/output_contract/
  compensation) is this study's naming over an established principle.
- Typed refusal for planless requests: industry treats this as "intent
  fallback", not a first-class architectural contract — **modest candidate
  contribution.**
- Cost-per-completed-task: known KPI ("cost-per-resolved-task"); elevating it
  to the architecture's primary metric is uncommon but not novel.

## 7. Academic catalogues

The [CSIRO Agent Design Pattern Catalogue (arXiv 2405.10467)](https://arxiv.org/abs/2405.10467)
covers registries, guardrails (single-scope), role-based cooperation and
adapters — none of its 18 patterns covers human-authored versioned plans,
load-time saga validation, guarantee-classified tools, or unsampled audit.
["Compound AI systems" (BAIR)](https://www.databricks.com/blog/what-are-compound-ai-systems)
is an umbrella term, not an architecture. Searched candidate names
("workflow-constrained agents", "deterministic agent orchestration", "bounded
autonomy", "structured autonomy", "policy-governed agents", "agentic process
orchestration") — none denotes this composite.

## 8. The literature this study's measurements answer to

*(All of this study's own measurements referenced in this section ran on
`gemini-3.1-flash-lite`; single-family scope in README § Honest limitations.)*

**False success / corrupt success — the empirical claim must be narrowed.**
The phenomenon our structured-output experiment exhibits is already named and
measured:

- [Advani, "From Confident Closing to Silent Failure: Characterizing False
  Success in LLM Agents" (arXiv 2606.09863)](https://arxiv.org/abs/2606.09863) —
  false success (agent claims completion, no state change in the system of
  record; canonical example: "refund processed" with no database record) is
  **45–48% of failures in single-control tau2-bench domains and 75.8% among
  AppWorld self-assessing trajectories** — but only **3% in the dual-control
  telecom domain**, where a second system holds ground-truth state. LLM judges
  fail to detect it (≤0.65 AUROC).
- [Cao et al., "Beyond Task Completion: Revealing Corrupt Success in LLM Agents
  through Procedure-Aware Evaluation" (arXiv 2603.03116)](https://arxiv.org/abs/2603.03116) —
  **27–78% of benchmark-reported successes are procedurally corrupt** (policy
  checks bypassed, communications fabricated); gating collapses Pass^4 to
  2–24% and reverses model rankings.
- [SABER, "Small Actions, Big Errors — Safeguarding Mutating Steps in LLM
  Agents" (arXiv 2512.07850)](https://arxiv.org/abs/2512.07850) (under review,
  ICLR 2026) — decomposing τ-bench and SWE-bench Verified trajectories into
  mutating vs. non-mutating steps: each additional deviation in a **mutating**
  action reduces the odds of task success by up to 92% (Airline) and 96%
  (Retail); deviations in non-mutating actions have little effect. The write
  path is where failures become decisive — the step class this pattern's
  `SYSTEM_OF_RECORD` boundary isolates. (Name collision, recorded per this
  repo's convention: a distinct 2026 "SABER" benchmarks operational safety of
  coding agents — [arXiv 2606.01317](https://arxiv.org/abs/2606.01317).)

Consequence for this study: the false-success experiment is a **replication in
a persistence-grounded setting, not a discovery**. What remains specifically
ours is narrower and still defensible: *every authority restriction held —
identity propagated, guardrails passed, the SYSTEM_OF_RECORD target validated its
invariants — and corrupted content persisted anyway*, i.e. content integrity
is orthogonal to authority enforcement; and the before/after showing that
decoding-level enforcement plus an architectural contract guard closes that
channel. Advani's dual-control figure is a **structural analogy, not
corroboration** of the SYSTEM_OF_RECORD mechanism: there, the second controller
is a *user simulator that independently verifies state* — a different
mechanism from invariant-validating systems of record — and the authors
themselves flag the 3% as observational (one domain, 15 cases, "insufficient
to isolate environment structure from other domain differences"). The shared
principle — separating the actor from an independent verifier of state
suppresses false success — supports the design direction; it does not confirm
this specific mechanism.

**Selection degradation — the numbers our zero-variance result will be compared
against.** [DACS (arXiv 2604.07911)](https://arxiv.org/abs/2604.07911) reports
flat-context orchestrator steering accuracy of **21.0–60.0% across scenarios
(vs. 90.0–98.4% with context scoping), with the degradation growing with agent
count N and diversity D**. Our measured 30/30 correct selections live at the
easy extreme of that curve (2 plans, ≤2 capabilities per agent) and say nothing
about where this pattern's selection layer sits on it. **The large-catalog
measurement is the single experiment that changes the study's answer** — it
either shows the confinement architecture holding selection accuracy where
flat orchestrators degrade, or shows the selection layer needs the same
mitigations (scoping, deterministic routing) as everyone else.

---

## 9. Post-matrix verification sweep (2026-07-28)

Three parallel sweeps run after the integrity matrix completed, to re-test the
verdict's load-bearing claims against the current landscape. Epistemic status:
these are **self-run searches with honest effort to refute, evaluated by the
same parties who hold the claims** — not independent adversarial review. They
killed two names (AUTHORITATIVE, CUSTODIAL) and no mechanism, which is
encouraging but not validation in the strong sense; that test only happens
when disinterested readers get the published claims. Findings that touch
section 3 are folded in above; the rest:

**9a. The naming decision, twice.** The collision search ADR-001 owed was run.
"Custodial" (that morning's rename target) **fails**: custodial/non-custodial
is the standard binary of the 2026 AI-agent crypto-wallet space —
[MetaMask Agent Wallet](https://metamask.io/agent-wallet) (jun/2026),
[Coinbase AgentKit](https://www.cobo.com/post/the-definitive-comparison-of-top-agentic-wallets-for-active-crypto-traders) —
agent-specific products where "custodial" means *who holds the keys/assets*, an
inverted reading in the same audience; the
[CSA IAM guidance](https://cloudsecurityalliance.org/artifacts/agentic-ai-identity-and-access-management-a-new-approach)
adds a second in-domain sense ("custodian" = the human owner of an agent's
permissions). Judged a worse collision than AUTHORITATIVE's. Replacement
`SYSTEM_OF_RECORD` was itself collision-checked before adoption: the hot 2026
usage is ["Agent System of Record"](https://www.workday.com/en-us/artificial-intelligence/agent-system-of-record.html)
(Workday; also [Airtable](https://www.airtable.com/articles/agent-system-of-record)) =
a registry *of* agents — a composition ("SoR **for** agents") that leaves the
base term's meaning (canonical holder of business truth) intact and aligned
with what the label asserts. No tool classification using the term exists.
Accepted; risk noted as low.

**9b. The matrix's experimental design has no published counterpart.** Search
across structured-output ablations, fault-injection frameworks, and
state-grounded evaluation: the ingredients exist separately —
enforcement-level ablation ([arXiv 2606.09395](https://arxiv.org/abs/2606.09395)),
fault injection ([MAS-FIRE, arXiv 2602.19843](https://arxiv.org/html/2602.19843v1);
[AgentCheck, arXiv 2607.11098](https://arxiv.org/html/2607.11098v1)),
DB-grounded benchmarks ([AppWorld](https://arxiv.org/pdf/2407.18901), tau2-bench,
[GroundEval, arXiv 2606.22737](https://arxiv.org/html/2606.22737v2)) — but no
work crosses decoding enforcement × boundary repair policy as a factorial with
ground truth read from a persisted business DB. The specific "tolerant repair
conceals violations" warning exists only in prose
([json_repair maintainer caveats](https://github.com/mangiucugna/json_repair/):
"fixes syntax, not semantics"; fail-closed guidance), never quantified — and
part of the practitioner literature celebrates high repair-success rates as a
virtue, the inverted reading the matrix falsifies. The matrix's cell-A result
(audit counters at zero while the DB holds 10 corrupted records) extends the
Advani/Cao thesis one level: not just the agent's report — the platform's own
telemetry is not evidence either.

**9c. Three-way failure vocabulary: open territory.** No framework names
"failed clean / nothing to compensate" as distinct from `compensated` and
`compensation_failed`. Temporal/AWS/Camunda leave it application-level;
the closest practitioner state machines
([payments saga](https://dev.to/gabrielanhaia/saga-compensation-for-a-payments-flow-that-actually-unwinds-2d09))
fold "nothing to compensate" into success. One unverified candidate remains:
[RAC (arXiv 2605.03409)](https://arxiv.org/abs/2605.03409) — full text not
retrieved; read it before claiming the vocabulary as novel.

**9d. Window update.** Keep the claim scoped: plenty of 2026 work measures
agents (the benchmark/evaluation literature of § 8, DACS, the routing studies
below) — the defensible statement is that the works proposing **platform
architectures comparable to this one** do not measure their own mechanisms. As
of 2026-07-28 none of Conductor, Salesforce Agent Graph, Camunda, AWS, Google,
or LangGraph has published agent-quality measurements (PayPal's DSL paper
reports engineering-productivity metrics only). And the gap is narrowing from
the research side: [ACE-Router (ACL 2026)](https://aclanthology.org/2026.acl-long.281/),
[GitHub Copilot's 40→13 toolset cut](https://github.blog/ai-and-ml/github-copilot/how-were-making-github-copilot-smarter-with-fewer-tools/)
(2–5 p.p. resolution-rate effect), and
[Layer-Isolated Evaluation (arXiv 2606.11686)](https://arxiv.org/abs/2606.11686)
all measure adjacent phenomena. No replication of the clear/ambiguous/
out-of-catalog decomposition. The composite pattern remains unnamed by others;
[AgentSPEX (arXiv 2604.13346)](https://arxiv.org/abs/2604.13346) is the
closest structural relative (versioned declarative YAML, checkpoint/replay) —
no confined-model-role rule, no guardrail intersection, no tool typing, no
measurements. Citation correction recorded: the registration-enforcement
neighbor is [arXiv 2606.26924](https://arxiv.org/abs/2606.26924) ("A
Deterministic Control Plane for LLM Coding Agents", injected-violation
conformance), not 2606.04017 (epistemic integrity — a mis-transposed ID that
had propagated into NOTES and the ADR).

**9e. Pattern-name collision search (run per the no-unchecked-names rule).**
Candidates: "Plan-Governed Agents" (the README's title at the time) vs.
"Authored-Plan Agents" (recorded recommendation; **adopted** after this
search). Neither string is taken verbatim anywhere
(arXiv, GitHub, npm/PyPI, vendor blogs). The difference is at fragment level:

- **"Plan-Governed Agents" — moderate-to-severe fragment collision.**
  "-Governed Agents" is the single most crowded, vendor-contested phrase in
  the 2026 agent lexicon, and it is *compliance-coded*, not
  architecture-coded: LangChain ["Building Governed Agents"](https://www.langchain.com/blog/building-governed-agents-a-framework-for-cost-control-and-compliance),
  [OpenAI's governed-agents cookbook](https://developers.openai.com/cookbook/examples/partners/agentic_governance_guide/agentic_governance_cookbook),
  [Google's "Govern your agents"](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern),
  Microsoft's Agent Governance Toolkit, [Port.io's "governed AI agent" glossary entry](https://www.port.io/glossary/governed-ai-agent),
  plus NIST/OWASP governance discourse. A cold reader parses it as "agents
  wrapped in a governance/compliance layer", not "the plan bounds the agent".
  Secondary: SE readers may route "Plan-" through Boehm's plan-driven/agile
  axis. Salesforce already pairs ["Guided Determinism and Governance Controls"](https://www.salesforce.com/news/stories/agent-fabric-control-plane-announcement/)
  in one title — direct mental substitution with a live competitor.
- **"Authored-Plan Agents" — low-to-moderate.** The only real risk is skim
  contamination from "agent authoring" (Copilot Studio / Agentforce /
  [ServiceNow's authoring-vs-execution split](https://agentmarketcap.ai/blog/2026/04/18/servicenow-build-agent-skills-sdk-author-outside-deploy-inside)),
  where "authoring" means *building the agent in a studio*. Containable: the
  compound binds "Authored" to "Plan", not to "Agents", and "authoring" is a
  feature verb, not a colonized market category the way "governance" is.
  One-line definition neutralizes it.
- Fallback if wanted: "Human-Authored Plan Agents" (no collisions found;
  longer). Verdict: **"Authored-Plan Agents" survives better**, confirming the
  earlier recorded recommendation — now with the dedicated search the naming
  rule requires.

## Verdict

0. **Calibration on the verification frame.** "Restrictions enforced and
   adversarially probed" is this study's organizing property, but the
   *technique* has stronger precedents: [ACP (arXiv 2603.18829)](https://arxiv.org/abs/2603.18829)
   model-checks its protocol in TLA+ across 4.29 **billion** states (11
   invariants, 4 temporal properties, 0 violations), and
   [arXiv 2606.26924](https://arxiv.org/abs/2606.26924) publishes
   injected-violation conformance testing (registry tampering, disallowed
   tool declarations) — the same method as our strict-xfail probes. What is
   ours is not the technique but its application to the **complete
   restriction set of a platform pattern**, spanning code, database
   constraints, and deployment boundary in one suite.
1. **Does the pattern already exist under another name? Partially.** Every
   slice has a named precedent (Akka golden rule; Salesforce guided
   determinism; Conductor's versioned YAML; BPMN/SagaLLM compensation; IAM
   intersection; RFC 8693; EU AI Act audit). **No source composes them**, and
   no existing name denotes the composite.
2. **Contributions, in explicit order of weight** (listing them as equals
   would be inflation):
   - **Strong**: the guarantee triad (SYSTEM_OF_RECORD/READ_ONLY/IRREVERSIBLE) with
     writes enforced by the system of record and no business entity on the
     platform side — positioned exactly in the gap the MCP maintainers
     themselves declare open ("annotations are hints; real guarantees belong
     to another layer"). Its novelty half is a negative claim and carries
     the § 3 delimitation (search- and time-bound as of 2026-08-06); its
     mechanism half — the registration-time check itself — does not depend
     on that claim. Advani's dual-control observation (false success at
     3% when an independent verifier holds state) is a structural analogy in
     the same direction — separate the actor from the verifier — not
     corroboration of this specific mechanism (see § 8).
   - **Strong, but as evidence rather than architecture**: the measurement
     discipline itself — cost-per-completed-task with the selection/generation
     split, and the persistence-grounded false-success replication with its
     before/after — in a space where none of the comparable architectures
     (Conductor, Agent Graph, Camunda, PayPal DSL) publish measurements.
   - **Small but sharp**: load-time validation of compensation ordering
     (SagaLLM validates at runtime; no load-time equivalent found).
   - **Modest transpositions**: intersection-only scoped guardrails (IAM
     semantics moved into agent-platform policy) and typed refusal as a
     first-class contract (industry: ad-hoc "intent fallback").
3. **Consolidated practice renamed** (cite, don't claim): single-orchestrator
   entry, declarative versioned plans, saga compensation, layered guardrails,
   identity propagation, unsampled audit, role-based cooperation.
4. **On the window**: Conductor (May 2026), Salesforce Agent Graph, Camunda and
   the PayPal DSL paper (Dec 2025) are converging on the same backbone — and
   Conductor and Salesforce have distribution, so the *vocabulary* will likely
   be theirs regardless of publication order. Racing does not change that.
   What the convergence players do not have is **measurement** — that, not
   speed, is this study's durable differentiator, and it decays only if left
   unpublished while others start measuring.
5. **Open self-critique the comparison sharpens**: at this catalog size the
   model-driven selection layer did not pay for itself — zero observed
   variance, structurally fixed cost (100% of model spend on the plan with no
   generation step). The pattern should admit deterministic selection as a
   degenerate case, and the DACS-style large-catalog measurement (section 8)
   is the one experiment that decides whether the selection layer is a
   load-bearing element or overhead.
