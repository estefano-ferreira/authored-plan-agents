# Pre-registration — extractor-natural cells K/L, cross-model arm (gpt-4o-mini)

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-07 (commit timestamp
authoritative), before any number for this arm exists. No script change is
required: cells K/L exist in the runner and the model-suffix mechanism
routes this arm's evidence to its own namespace. Deviations are recorded
as dated amendments above the registered text, never by editing it.

**Relation to the baseline arm.** The registered K/L arm on
`gemini-3.1-flash-lite` (`preregistration-extractor-natural-cells.md`) is
**unchanged and still owed**: its run was interrupted by the daily
free-tier quota at K = 6 recorded repetitions and resumes when the quota
resets. This document registers a *separate* cross-model arm — a
different model is a different experiment — run on OpenAI credits while
the baseline quota recovers.

## Question

Does the extractor-under-natural-violations reading depend on the model
family's natural failure shape? The baseline family self-fences 20/20
(cells I/J, tracked); the mini family's tracked evidence implies the
opposite regime.

## What the code already determines (written before any prediction)

1. **The mini family's natural self-fencing is low.** Direct SQL over the
   tracked `matrix-A-gpt-4o-mini.sqlite` (where the injector added
   exactly one fence to whatever the model produced): 5/6 persisted
   blobs are single-fenced (model gave bare content), 1/6 double-fenced
   (model self-fenced). Point estimate: ~1/6 natural fencing rate at the
   generation boundary, against the baseline's 20/20.
2. **Upstream divergence is the mini family's dominant failure locus.**
   The cross-model A/B arm measured 13/20 repetitions diverging on
   selection-response format before reaching the generation boundary.
   Per that arm's registered rule, upstream divergences are reported
   separately, never composited into boundary readings.
3. **Conditional mechanics are code**: a bare conforming response passes
   the extractor untouched (no fence match) and the full-schema check
   directly; a single-fenced response is stripped and revalidated; the
   guard refuses whatever still fails, typed.
4. **Cell L on this family is genuinely unmeasured**: gpt-4o-mini has
   never run with the schema active in this study (the A/B arm was
   no-schema only); its native structured-output conformity here is an
   open number.

## Registered predictions (only what survived the section above)

- **P1:** a material fraction of repetitions (≥ 6/20 across both cells)
  diverges upstream on selection format, reported separately (basis:
  13/20 in the A/B arm).
- **P2 (cell K, boundary-reaching reps):** the natural violation rate at
  the generation boundary is materially below the baseline family's
  10/10 (basis: determination fact 1); repairs occur only in the
  self-fenced minority and recover valid content with contract counters
  at zero.
- **P3 (cell L, boundary-reaching reps):** zero contract violations
  (native structured output conforms), extractor idle.

## Method (fixed before the run)

- **Cells:** K and L exactly as configured in the runner; 10 reps each.
- **Model/provider:** `--provider openai`, `OPENAI_MODEL=gpt-4o-mini`;
  list rates $0.15/$0.60 per million as recorded in the pricing table.
- **Evidence:** model-suffixed namespace, automatic:
  `integrity_matrix-gpt-4o-mini.jsonl` (appended),
  `matrix-{K,L}-gpt-4o-mini.sqlite`, audit trails, suffixed report.
- **What is read as the result:** status; failure locus per repetition
  (upstream vs boundary); contract counters; repairs; rows and per-row
  validity by direct SQL; tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — P1/P2/P3 hold.** The extractor's practical value in the
  natural regime is family-dependent: essential where the family fences
  (baseline), mostly idle where it does not (mini). Reported as the
  cross-model scoping of the §8 stack — the division of labor between
  extractor and guard shifts with the family's failure shape, which is
  itself an argument for measuring, not assuming, the repair layer.
- **Branch B — mini violates at a material rate at the boundary**
  (fencing or non-conforming bare JSON): reported in the data's exact
  terms; P2/P3 scored against it.
- **Branch C — upstream divergence dominates to the point of starving
  the boundary sample** (as in the A/B mini arm's strict cell): the
  per-locus report stands on its own and the boundary reading is
  declared under-sampled, never extrapolated.

## Why pre-registered

The uncomfortable branch is A itself: "the extractor barely matters in
this family" complicates the just-measured baseline story that the
extractor recovers everything. Registering that reading before the run
keeps the §8 division of labor empirical in both directions.
