# Why this pattern exists

*Authored-Plan Agents — an architectural pattern for business process
automation with LLM agents.*

**The problem.** Ten times in a row, an agent reported success. Ten times, the
business database received garbage — a malformed blob written into a
customer-facing field, indistinguishable from a legitimate record to anything
downstream. The platform's own contract counters read **zero violations**: a
tolerant parser "repaired" every response before any check could see it. The
agent was polite, confident, fully authorized — and wrong ten out of ten
times, silently. This repository reproduces that incident under controlled
conditions — the fault injected where the original round hit it naturally
(the output schema stripped), on a real flash-tier model,
`gemini-3.1-flash-lite` — and measures what closes it.

**Why the obvious fixes don't close it.** Guardrails don't catch it — every
permission held; corrupted *content* is orthogonal to authorization. Schema
validation catches form, not meaning — the garbage entered through the
business API and satisfied it. Testing doesn't catch it — the model is
non-deterministic, so the failing output shape appears in production, not in
your fixtures.

## The three authorities

Each failure above is one party deciding something it cannot guarantee. The
pattern separates the three decisions and gives each to the party that can:

| Decision | Authority |
|---|---|
| **Sequence** — what runs, in what order | a human (authored, versioned plan) |
| **Authorization** — may this run, now | policy (guardrails, intersection-only) |
| **Validity** — is this business truth | the domain (the system of record) |

The model still has a job — it selects among authored plans and generates
content inside their steps. It never authors the steps, never grants itself
permission, and never judges its own output valid.

[![Authored-Plan Agents architecture poster](docs/authored-plan-agents-architecture.png)](docs/authored-plan-agents-architecture.png)

*The full picture: three model decisions, seven restrictions, three
enforcement points — click to zoom.*

## When to use it

When agents **write business state whose invariants matter** — registrations,
bookings, cancellations, refunds, anything where a wrong *record* costs more
than a wrong *sentence* — and the flows are known well enough to be written
down and reviewed.

## When NOT to use it

- **No business state to protect** — FAQ chatbots, personal assistants,
  summarization, simple copilots. There is no invariant for the pattern to
  guard; its cost buys nothing.
- **Validation already unavoidable** — if every write path runs through an
  enterprise API that enforces server-side validation and nothing in the
  agent's wiring can bypass it, the pattern names why your existing default
  is right, but adds no mechanism you don't have.
- **Prototypes and single-domain tools** — the per-agent ceremony (plans,
  specifications, guardrail scopes) doesn't amortize until flows and agents
  multiply.

A pattern that presents itself as universal is advertising. This one has a
scope, and these are its edges.

---

**Want to implement it in your stack? → [`SPECIFICATION.md`](SPECIFICATION.md).
Details, evidence and the reference implementation → [`README.md`](README.md).**
