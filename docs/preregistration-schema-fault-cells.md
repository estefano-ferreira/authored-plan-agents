# Pre-registration — integrity matrix cells E and F (schema + injected fault)

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-06, before any script
change or number exists. This document is committed before the experiment
executes; any deviation from it must be recorded as a dated amendment above
the registered text, never by editing it.

## Erratum (2026-08-07, post-run)

(a) The registered sentence below — "Cell E is the first cell of this matrix
whose outcome the authors cannot predict from the code" — is wrong. Direct
reading of `TolerantRepairClient` (`src/infrastructure/providers/tolerant_repair_client.py`)
shows it never strips markdown fences: for any generation response that does
not parse as a bare JSON object, it replaces the content with
`{"request_type": "support", "summary": content[:200], "reply_body": content}`
— the original text, fences included. Because the fault injector re-fences
every generation response and this fallback absorbs every non-JSON response,
cell E's outcome (absorption; ten garbage rows; contract counters at zero)
was determined by roughly twenty lines of decorator code before the run.

(b) Structural fact 2 below ("fence-stripping repair plausibly recovers
*valid* content") therefore rested on a misdescription of the instrument:
the repair does not strip fences at all.

(c) Cell E is accordingly demoted from "falsified prediction" to
"verification that the historical absorbing fallback persists garbage
independently of the decoding condition." This does not touch the
pre-registration's other content: F's determinism-by-construction (structural
fact 1) and the overall design stand as registered.

(d) Process correction adopted going forward: every future pre-registration
gets a mandatory "what the code already determines" section, written before
any prediction — only what survives that section may be registered as a
prediction.

## Question

The integrity matrix (NOTES.md § "Integrity matrix — controlled
reproduction") ran its fault injector only in the no-schema column, so the
design is not a complete factorial — the fault arm co-varies with the
decoding axis, and no cell shows the boundary policies operating *with*
decoding enforcement active (the paper's § 3 admission and § 6 "Not a
complete factorial" limitation; this document is the experiment that
limitation queues). Two new cells complete the fault arm:

- **Cell E** — real `responseSchema` + `GenerationFaultInjectionClient` +
  tolerant repair (`TolerantRepairClient`);
- **Cell F** — real `responseSchema` + injector + strict
  `retry_once_then_fail` output-contract guard.

**Scope, stated precisely.** The injector corrupts *downstream* of the
provider (it re-fences every generation response after decoding has
already produced it). Under an enforced schema the injected markdown fence
therefore wraps schema-valid JSON. These cells do **not** test whether the
schema "holds under attack" — that question cannot be asked with this
injector — they test what each boundary policy does with **post-decoding
corruption** (the transport/middleware class). Two structural facts are
known before any run and are part of the design, not findings:

1. **Cell F is deterministic by construction**, like cell B: the injector
   re-fences the retry attempt too, so recovery is impossible and the
   guard's coded behavior (typed failure, nothing persisted) is what the
   cell verifies under real-model conditions.
2. **Cell E is where a discovery is possible**: unlike cell A — where the
   content under the fence was itself unconstrained — the fence in cell E
   wraps valid JSON, so fence-stripping repair plausibly recovers *valid*
   content. Whether it does, and what then persists, is the open question.

## Method (fixed before the run)

- **Cells:** E and F as defined above, added to `scripts/run_plans.py`'s
  `_MATRIX_CELLS` (currently hardcoded to A/B/C/D/control). **That script
  change happens only after this document is committed.** The resume
  mechanism's skip-recorded-(cell, rep) behavior applies unchanged.
- **Reps:** 10 per cell — 20 executions of the two-agent inbound plan
  (the only plan with a generation step), each against a fresh SQLite.
- **Model:** `gemini-3.1-flash-lite` — the original matrix's model, for
  internal validity. A different model is a different experiment (see the
  cross-model A+B pre-registration).
- **Call budget (estimate, same arithmetic as the A/B pre-registration):**
  ≈4 model calls per execution in E, ≈5 in F (the retry doubles the
  generation call and both attempts fault by construction) — roughly
  90–100 calls total, run inside free-tier quota with idempotent resumes
  if a daily cap interrupts.
- **Evidence:** `results/integrity-matrix/` in the existing formats —
  records appended to `integrity_matrix.jsonl`, per-cell tracked SQLite
  snapshots (`matrix-E.sqlite`, `matrix-F.sqlite`), audit trails,
  consolidated report via `--matrix-report-only`.
- **What is read as the result:** reported status; contract
  violation/retry/failure counters; repair count; rows created in the
  ERP; per-row validity of the persisted `summary` by direct SQL
  inspection (never via platform state); tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch**, and the paper's "Not a
complete factorial" limitation is updated with the measured result
whatever it shows. The uncomfortable branch is listed first because a
peer-review question asked whether its publication is fixed in advance —
it is.

- **Branch A — E persists valid rows while F refuses.** Repair recovers
  the recoverable corruption; the strict guard refuses it and persists
  nothing. **The insurance price rises**: the strict policy pays
  availability for certainty, refusing corruption that a tolerant path
  would have recovered. Committed consequence: the paper's § 5/§ 6 and
  the README state the moral as *position decides what the counters see;
  policy decides availability versus certainty* — never "strict always
  wins". F's zero-rows outcome is reported as the verification it is,
  not as a discovery.
- **Branch B — E persists garbage** (repair mangles the payload, or the
  fence survives into the business field): telemetry blindness
  reproduces even with the schema upstream, and the guard's case
  strengthens. Reported as such, with the persisted rows inspectable in
  the tracked snapshot.
- **Branch C — anything else** (E fails typed; F persists any row; mixed
  reps within a cell): reported in the data's exact terms. Any row
  persisted in cell F falsifies the guard's coded contract and is
  reported as a bug finding against the implementation, not smoothed
  into the experiment's narrative.

## Why pre-registered

Same discipline as the cross-model A+B and embedding-baseline documents:
the decision of what gets published — including the branch that
complicates this study's own headline — is made before the number is
seen. Cell E is the first cell of this matrix whose outcome the authors
cannot predict from the code; that is exactly the cell where deciding
afterwards would be easiest to rationalize.
