# Pre-registration — natural-violation cells I and J (no schema, no fault)

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-07, before the
runner cells exist and before any number. The runner change (cells I/J)
happens **only after this document is committed.** Deviations are recorded
as dated amendments above the registered text, never by editing it.

## Question

The design's longest-standing admitted gap (paper §3 and §6; review 4.1;
referee M2): the no-schema/no-fault arm — the model's **natural**
violation behavior under each boundary policy — has never run as a matrix
cell. The pre-matrix incident round covered only its tolerant half, and
that round's raw file is lost (aggregates survive in NOTES). Two cells
close it with tracked evidence:

- **Cell I** — schema stripped, **no injector**, tolerant (absorbing)
  repair;
- **Cell J** — schema stripped, **no injector**, strict guard.

## What the code already determines (written before any prediction)

1. **Nothing determines the violation rate.** With the schema stripped
   and no injector, whether a generation response parses as a bare JSON
   object depends entirely on the model's prompt-only behavior. These are
   the only cells of the whole matrix whose *headline number* no
   decorator fixes — the design's genuinely stochastic cells.
2. **Conditional on a violation, the policies' behavior is determined:**
   the absorber stuffs (garbage persisted, counters blind, repair
   counted); the guard counts the violation, retries the identical
   request once, and fails typed if the retry also violates. Whether the
   *retry* violates is again model behavior, not code.
3. **Conditional on no violation, both cells persist the natural content
   as-is** (subject to full-schema validation at the boundary).

## Registered predictions (only what survived the section above)

- **P1 (rate):** the natural violation rate is high — ≥ 8/10 repetitions
  in each cell hit at least one generation-contract violation. Basis: the
  incident round measured 10/10 fenced under prompt-only control on this
  model; that basis is an aggregate from a lost raw file, which is
  exactly why this arm must produce tracked evidence.
- **P2 (cell I):** every violating repetition completes with garbage
  persisted and contract counters at zero (absorber upstream of the
  check).
- **P3 (cell J):** violating repetitions end `failed_clean` with zero
  rows (retry violates again — the model has no reason to change
  envelope between identical calls), giving the strict half of the
  incident round its first tracked measurement.

## Method (fixed before the run)

- **Runner change (after this commit):** cells
  `I = {no schema, no fault, tolerant}` and
  `J = {no schema, no fault, strict}`, 10 default reps each — pure
  configuration addition; no new instrument.
- **Cells:** I and J, 10 reps each; model `gemini-3.1-flash-lite`; fresh
  SQLite per rep; boundary at full-schema validation (current `main`).
- **Evidence:** baseline namespace (new cells, not a re-run):
  `integrity_matrix.jsonl`, `matrix-{I,J}.sqlite`, audit trails,
  consolidated report.
- **What is read as the result:** status; contract counters; repair
  count; rows; per-row `summary` validity by direct SQL; tokens and
  cost; **and the per-repetition violation incidence**, the arm's
  headline number.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — high natural rate (P1 holds).** The lost-baseline caveat
  is retired by tracked evidence: the paper's F3 "before/after" no
  longer rests on surviving aggregates alone, and cell J finally
  measures the strict policy against *natural* violations. The
  limitations section is updated accordingly (the "never run as a cell"
  admission is replaced by the measurement).
- **Branch B — low or zero natural rate.** Reported as measured, with
  the consequence stated plainly: the incident round's 10/10 does not
  replicate at this date (model default drift is the obvious candidate
  and is named as hypothesis, not conclusion), and every claim resting
  on "10/10 natural violations" is re-scoped to the incident round's
  date. This branch weakens the study's motivating narrative and is
  published all the same.
- **Branch C — mixed or asymmetric outcomes** (e.g., violations in one
  cell's reps but not the other's): reported per repetition in the
  data's exact terms; no compositing.

## Why pre-registered

Branch B is the uncomfortable one: a low natural rate today would remove
the drama from the study's origin story. Fixing its publication before
the number exists is the point. The determination section records that
only the rate is open — policy behavior conditional on the rate is code,
and will be reported as verification either way.
