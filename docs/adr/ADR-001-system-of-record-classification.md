# ADR-001 — System-of-record classification of tools

- **Status:** accepted
- **Date:** 2026-07-28 (name finalized same day after two collision searches)
- **Scope:** the `ToolType` enum in `src/core/capabilities.py` and every surface
  that consumes it (registration-time validation, guardrail `allow_tool_types`,
  audit vocabulary, docs)

## Context

The pattern classifies every tool a capability registers along one semantic
axis: what the tool's *target* is, from the business's point of view. The
original names were `AUTHORITATIVE` (target is the business system of record,
which owns invariants and may reject the write), `REFERENCE` (pure read), and
`EFFECTFUL` (outward effect that cannot be rolled back, e.g. sending an
e-mail). Registration-time validation enforces structural consequences:
`writes_business_state=True` requires the system-of-record classification, and
such a target may not be an in-memory or free-standing database owned by the
agent.

While preparing the study for publication, a related-work pass found that
"authoritative"/"authority" had, during 2026, consolidated in the agent
literature and market around a **different meaning**: *the permission or
mandate an agent has to act* (Ball, "Authority Is the AI Bottleneck",
jan/2026; AppZen's "authority calibration" usage, jul/2026). Keeping
`AUTHORITATIVE` would make the pattern's central term collide with a
vocabulary that answers "how much is the agent allowed to do?" — an
authorization question — when the axis here answers "who holds the business
truth this tool touches?" — a data-stewardship question.

## Decision

Rename the triad, aligning all three members (nothing outside this repository
consumes the enum names):

| new name | old name | meaning |
|---|---|---|
| `SYSTEM_OF_RECORD` | `AUTHORITATIVE` | target is the business system of record; it owns the invariants and may reject the operation with a typed business-rule violation |
| `READ_ONLY` | `REFERENCE` | pure read; no business state changes |
| `IRREVERSIBLE` | `EFFECTFUL` | outward effect that cannot be rolled back once emitted |

`READ_ONLY` and `IRREVERSIBLE` are deliberately *borrowed*, consolidated
vocabulary — they carry no novelty claim. The guarantee-locus axis is the one
term the related-work pass found no counterpart for, and the claim attached to
it is narrow: **one semantic axis** (who validates the business invariant, as
a first-class tool classification), not the triad as a whole and not the
registration-time enforcement mechanism (arXiv 2606.26924, "A Deterministic
Control Plane for LLM Coding Agents", is the closest neighbor on enforcement —
injected-violation conformance testing of declared invariants).

Historical records in `results/` are evidence and keep the old strings; any
reader script must accept both vocabularies.

## The guardrail-vs-locus test

The test used to judge candidate names: *does the name describe a property of
the target system, or a decision made at the boundary in front of it?* A
guardrail answers "may this principal use this tool in this run?" — a policy
decision that lives in `allow_tool_types` and can change per agent, per
deployment, per day. The classification answers "is this target the system
that owns the business truth?" — a property of the architecture that does not
change with policy. A candidate name fails the test if it names the
enforcement instead of the property being enforced.

## Rejected alternatives

- **`AUTHORITATIVE` (keep as-is).** Rejected for the market-semantics
  collision above: in 2026 usage, "authority" reads as *agency/permission
  granted to the agent*, inverting the intended direction (the term here is
  about the target's stewardship of business truth, not the agent's mandate).
- **`GUARDED`.** Rejected by the guardrail-vs-locus test: it names the
  enforcement mechanism (there is a guard in front of this tool), not the
  semantic property (this tool's target holds the business truth). Every tool
  type can be guarded; only one targets the system of record.
- **`CUSTODIAL` (adopted provisionally earlier the same day, then dropped).**
  A dedicated collision search — the one this ADR's first draft flagged as
  owed — found the collision **worse than AUTHORITATIVE's**:
  custodial/non-custodial is the standard binary of the 2026 AI-agent
  crypto-wallet space (MetaMask Agent Wallet, jun/2026; Coinbase AgentKit;
  Cobo), agent-specific products where "custodial" means *who holds the
  keys/assets on the agent's behalf* — a same-audience, shape-identical,
  direction-inverted reading. The CSA's agentic-IAM guidance adds a second
  in-domain sense ("custodian" = the human owner of an agent's permissions).
  Lesson recorded: no name gets fixed without a dedicated collision search
  first.

## Why `SYSTEM_OF_RECORD` survives its own collision check

The active 2026 usage is Workday's (and Airtable's) **"Agent System of
Record"** — a registry and governance layer *for* agents. That is a
composition ("a system of record **about** agents"), not a redefinition: the
base term keeps its decades-old enterprise meaning — the canonical holder of
business truth — which is literally what this label asserts about the tool's
target. No existing tool classification uses the term. The name is long for an
enum; clarity was weighed above brevity, deliberately. Residual risk: low.

## Consequences

- Enum members, their string values, exception messages, the audit
  `kind` vocabulary (now `"system_of_record"`), guardrail YAML
  `allow_tool_types`, tests, and the narrative docs are renamed with zero
  behavior difference. The suite (18 passed + 7 xfailed, name-only diffs) was
  verified once per rename **pass**: the first pass carried all three members
  (`AUTHORITATIVE`→`CUSTODIAL`, `REFERENCE`→`READ_ONLY`,
  `EFFECTFUL`→`IRREVERSIBLE`); the second only the collision-driven
  `CUSTODIAL`→`SYSTEM_OF_RECORD`.
- `results/` is untouched. Recorded evidence carries only the original
  `"authoritative"` strings (the intermediate `"custodial"` value existed in
  code for hours and never reached persisted evidence); readers tolerate old
  and new values.
- Prose that used "authoritative"/"effectful" as ordinary English in `src/`
  was reworded so the old terms grep clean outside intentional history.
