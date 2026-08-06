# Pre-registration — cross-model integrity matrix, cells A+B

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-06, before any
cross-model script or number exists. This document is committed before the
experiment executes; any deviation from it must be recorded as a dated
amendment above the registered text, never by editing it.

## Amendments (dated; the registered text below is unmodified)

**2026-08-06 — model named: `claude-sonnet-5` (Anthropic), before any
run.** Chosen on availability and tier diversity, never on eval
performance:

- the runner already supports `--provider anthropic` natively, and the
  platform's Anthropic adapter and pricing-table entry ($3 / $15 per 1M
  tokens) exist — no provider code is written for this run;
- an API key is already provisioned in the environment;
- **tier diversity is the stated design reason**: every measurement in
  this repository ran on a flash-tier model, and the standing external
  critique is that the measured behavior could be a weak-model artifact.
  A frontier-tier model answers that directly; a second flash-tier model
  would not.

Estimated cost from the original cells' recorded token volumes
(A: 3893/1376, B: 4545/2636 per 10 reps): order of $0.10 at list price,
one sitting, no daily-cap accumulation.

Two mechanical notes fixed before the run: (1) the matrix resume keys
records by (cell, rep), so the cross-model run requires model-suffixed
evidence paths (per the registered Method) — that runner change happens
after this amendment and before any call; (2) cells E/F were measured
between registration and this amendment, and their result fixes the
interpretive baseline: the repair emulation absorbs rather than extracts
(NOTES § Schema+fault cells E and F), so a frontier model that emits raw
JSON without a schema may change the *shape* of cell A's persisted blobs
(single- rather than double-fenced) without changing the boundary-axis
reading the registered branches decide on.

**2026-08-06 — second cross-model arm added: `gpt-4o-mini` (OpenAI),
before any run.** A distinct role from the frontier arm, stated so the
two are never conflated: `claude-sonnet-5` carries **tier diversity**
(answers the weak-model-artifact critique); `gpt-4o-mini` carries
**family diversity only** — a third provider family at the same
capability class as the measured model, upgrading "spot-checked across
three families" (selection, N≤10) to "boundary cells measured across
three families". Chosen on availability and price, never on eval
performance: the OpenAI adapter, the pricing-table entry
($0.15 / $0.60 per 1M tokens) and an API key all already exist; the
model is the one already configured in the environment. Estimated cost
from the original cells' token volumes: order of $0.01. Both arms run
under the same registered branches; each model's evidence lands in its
own model-suffixed files, read separately — no composite number across
models (the registered Branch C discipline applies per family).

## Question

On the boundary-behavior axis of the integrity matrix (NOTES.md §
"Integrity matrix — controlled reproduction"), does a second model reproduce
the finding that, *given* a forced generation-format violation, the boundary
policy alone decides between silent corruption (cell A, tolerant repair) and
loud clean failure (cell B, `retry_once_then_fail`)? The original matrix
measured this on one model (`gemini-3.1-flash-lite`) only; README's
"Reprioritized ahead" note (2026-08-06) queued this cross-model check with
its success criterion — including the vacuity outcome — pre-registered
before the run, which is what this document fixes.

**Scope, stated precisely.** Only cells A and B transfer. Cells C and D
(real `responseSchema`) measured a natural violation rate of **zero** at
N=10 on the original model — the repair axis was invisible there because
neither cell had anything to repair. A second model that also violates
format at 0/10 without a schema would measure that model's own decoding
defaults, not the boundary policy, which is exactly the vacuity outcome
README named. A C/D-style natural-rate replication is **not** part of this
experiment's success criterion and is out of scope for this document.

## Method (fixed before the run)

- **Cells:** A and B only, from `scripts/run_plans.py --matrix-cell {A,B}`.
  Both cells run `GenerationFaultInjectionClient`, which re-fences **every**
  generation response by construction — the A/B violation rate is forced,
  not natural, on this model and is expected to remain forced on the second
  model for the same structural reason (the injector wraps the response
  regardless of what the underlying model produced). What is *not* forced,
  and is the actual subject of the transfer test, is what each boundary
  configuration does with that guaranteed violation, and whether the second
  model's calls reach the generation step at all (see Branch C below).
- **Reps:** 10 per cell, matching the original matrix — 20 executions.
- **Model:** named by dated amendment **before** the run, chosen on
  price/availability, never on eval performance. Known constraints as of
  2026-08-06 (NOTES.md § "Cross-model selection spot-check"): `gemini-2.5-*`
  is closed to new accounts; the viable current-generation candidates,
  `gemini-3.6-flash` (thinking regime) and `gemini-3-flash-preview`, both
  carry a **20-requests/day** free-tier cap
  (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`); the preview model
  additionally threw 503 capacity errors requiring a retry wrapper.
- **Call budget (estimated honestly, not measured):** inbound-email-to-erp
  costs ≈4 model calls per execution in cell A and ≈5 in cell B (the retry
  doubles the generation call, and — per the injector's construction — both
  attempts fault, so recovery is impossible by construction in this cell,
  as in the original run). 10 reps × 2 cells ⇒ roughly 90–100 calls total.
  Against a 20/day free-tier cap, that is multi-day accumulation via the
  idempotent resume mechanism (`scripts/resume_matrix.py`'s pattern: skip
  already-recorded `(cell, rep)` pairs in
  `results/integrity-matrix/integrity_matrix.jsonl` and continue) — that
  script currently hardcodes `gemini-3.1-flash-lite` and drives cells
  A–D+control, so a same-shaped invocation scoped to A/B on the amended
  model is what the run needs; this document does not write it. The
  alternative is one paid-tier run, of the same order as the original
  matrix's per-cell cost (cell A: $0.00304/10 reps; cell B: $0.00509/10
  reps, both exact figures from NOTES.md).
- **Evidence:** lands in `results/integrity-matrix/` beside the existing
  files, in the same formats — checkpointed JSONL
  (`integrity_matrix.jsonl`), per-cell SQLite snapshots as tracked evidence
  (`matrix-A.sqlite`, `matrix-B.sqlite`, model-suffixed to not overwrite the
  original), audit trails (`audit-matrix-*.jsonl`), consolidated report via
  `--matrix-report-only`.
- **What is read as the result:** reported status per cell (`completed` vs.
  `failed_clean`), contract violation/retry/fail counters, ERP row counts,
  valid-vs-garbage summary counts (checked directly in SQLite, not from the
  status field — the original finding was that cell A's counters read
  0/0/0 while the DB carried 10 garbage rows), token/cost deltas between
  cells.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.** The result enters
`results/`, NOTES and README whatever it shows.

- **Branch A — full transfer.** Second model's cell A reports `completed`
  10/10 with garbage persisted in the ERP `summary` column (verified in
  SQLite, not from status); cell B reports `failed_clean` 10/10 with zero
  rows. The boundary-axis claim upgrades from single-model to two-family:
  NOTES gains a second matrix block under this model's name, and README's
  wording changes from "one codebase, one model" (current phrasing in NOTES
  § Structured output correction, "Conclusion for the study") to a
  two-model statement — the axis, not the model, is what decides the
  outcome. No other cell's reading moves.
- **Branch B — divergence in the guarded cell.** Any statuses other than
  `failed_clean` 10/10 in cell B, or any row persisted in cell B's SQLite,
  is reported as a **falsification of the transfer claim**, in the data's
  exact terms (which statuses appeared, how many rows, with what content).
  The README/NOTES claim stays scoped to the single model family that
  demonstrated it, and gains the measured divergence as a named limitation
  — not folded into, or used to walk back, the original model's result.
- **Branch C — divergence upstream of generation.** If the second model
  fails plan or capability selection on the 2-plan catalog such that
  executions never reach the generation step (e.g., selection-format
  violations, wrong plan chosen, non-`completed` failures before
  `interpret_and_draft` runs), that is reported **separately, as a
  selection result** — using the same per-kind reporting discipline as the
  embedding-baseline pre-registration (`docs/preregistration-embedding-baseline.md`
  Branch C) — and is **not** composited into the boundary-axis number. This
  repository already recorded a citation error from composite ranges built
  across different populations (README § Honest limitations); a selection
  failure and a boundary-policy result are different populations and stay
  in different tables.
- Cost and token deltas (A vs. B, and both vs. the original model) are
  reported in every branch regardless of outcome — no cost figure is
  withheld for reading badly.

## Why pre-registered

Same discipline as the embedding-baseline pre-registration and the original
integrity matrix itself: the decision of what counts as transfer, what
counts as falsification, and what counts as a different question entirely
(Branch C) is fixed before the number exists. The original matrix's own
lesson — cells C/D showed the repair axis can be invisible at zero natural
violations — is the reason this document states the vacuity outcome as an
explicit non-goal rather than discovering it as an excuse after a
disappointing run.
