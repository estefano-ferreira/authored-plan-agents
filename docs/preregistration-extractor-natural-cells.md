# Pre-registration — extractor under natural violations, cells K and L

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-08, before the runner
cells exist and before any number. The runner change (cells K/L) happens
**only after this document is committed.** Deviations are recorded as dated
amendments above the registered text, never by editing it.

## Question

The v0.6.1 external review identified the last unmeasured column of the
matrix's factorial grid: the genuine extractor
(`ExtractorRepairClient`, single-pass fence removal) has run only against
*injected* faults (cells G/H). Under **natural** violations — the regime a
production deployment actually faces — the extractor is unmeasured, and
the paper's practical recommendation ("decoding + repair composed before
the check + typed-fail backstop + audit reads the system of record")
currently quantifies the backstop's share only in the injected regime. Two
cells close the grid:

- **Cell K** — schema stripped, no injector, extractor repair;
- **Cell L** — real `responseSchema`, no injector, extractor repair.

## What the code already determines (written before any prediction)

1. **Cell L is determined conditional on decoding conformity.** With the
   schema active, the natural violation rate measured 0/10 twice (C/D at
   both boundary strengths). Conforming bare-JSON responses do not match
   the extractor's fence pattern, so the extractor is idle by
   construction on conforming content. Determined outcome, conditional on
   that conformity: `completed` 10/10, 10 valid rows, 0 repairs,
   counters 0/0/0 — byte-equivalent to C/D. Any deviation exercises the
   extractor naturally and is reported as measured.
2. **Cell K's mechanics are determined; its rates are not.** The
   tracked natural evidence (cells I/J, 2026-08-07) shows this model
   self-fencing 20/20 first attempts, with cell I's persisted blobs
   **single-fenced**. On single-fenced content the single-pass extractor
   strips the one layer by construction; whether the recovered inner
   text then passes full-schema validation is model behavior (the inner
   JSON's key set and types were never measured — the absorber stuffed
   blobs whole). Double fencing, absent the injector, has no natural
   source observed so far. Open numbers: the per-repetition recovery
   rate and the full-schema validity of naturally fenced inner content.
3. **Conditional on recovery, counters are blind by position** (the
   repair precedes the check); conditional on non-recovery, the guard
   refuses typed. Both conditionals are code.

## Registered predictions (only what survived the section above)

- **P1 (cell K):** ≥ 8/10 repetitions complete with a valid persisted
  row — the extractor recovers naturally single-fenced, schema-valid
  JSON. Basis: I/J's 20/20 single-fenced rate and the prompt's explicit
  key instructions; the inner-validity component is the genuinely open
  part.
- **P2 (cell K):** in every recovered repetition the contract counters
  read zero — natural-regime confirmation that recovery and counter
  blindness co-occur.
- **P3 (cell L):** 0 natural violations at N=10 (third such
  observation), extractor idle, outcomes byte-equivalent to C/D.

## Method (fixed before the run)

- **Runner change (after this commit):** cells
  `K = {no schema, no fault, extractor}` and
  `L = {schema, no fault, extractor}`, 10 default reps each — pure
  configuration addition; the instrument exists and is unchanged.
- **Model:** `gemini-3.1-flash-lite`; fresh SQLite per rep; boundary at
  full-schema validation (current `main`).
- **Evidence:** baseline namespace: `integrity_matrix.jsonl`,
  `matrix-{K,L}.sqlite`, audit trails, consolidated report.
- **What is read as the result:** status; contract counters; repair
  count; rows; per-row `summary` validity by direct SQL; per-repetition
  recovery incidence; tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — K recovers (P1/P2 hold), L clean (P3 holds).** Committed
  consequence for the paper's §8: the practical stack's division of
  labor is fully quantified in the natural regime — the extractor
  carries availability, the guard's backstop share is K's failure count,
  and counter blindness persists through recovery (audit must still
  read the system of record). Reported with K's open numbers as the
  measured ones and L as the verification it is.
- **Branch B — K refuses at a material rate** (extraction reaches
  content that fails full validation, or unexpected fencing shapes):
  the guard's backstop share in the natural regime is that rate;
  reported in the data's exact terms.
- **Branch C — L deviates** (any natural violation under the schema):
  the third 0/10 observation fails to replicate; reported as measured,
  with the schema-conformity conditional of every determined cell
  re-examined in that light.
- **Branch D — anything else** (upstream divergences): reported
  separately, never composited.

## Why pre-registered

Branch B is the uncomfortable one for the review's own framing: a
material natural refusal rate would *strengthen* the strict guard's case
right after the review argued its demotion. Fixing publication before
the number exists keeps that argument empirical in both directions.
