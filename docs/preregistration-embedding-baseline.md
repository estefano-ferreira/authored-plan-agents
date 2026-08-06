# Pre-registration — embedding-similarity selection baseline

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-06, before any baseline
script exists. This document is committed before the experiment executes; any
deviation from it must be recorded as a dated amendment above the results,
never by editing the registered text.

## Question

On the selection sweep's exact intent set, does embedding-similarity
selection match LLM selection? This is the strong semantic comparator that
replaces the withdrawn keyword-counterfactual line (see README § Key
results, 2026-08-06 revision): the LLM selection layer's stated
justification is paraphrase robustness, and that justification has not yet
been tested against anything stronger than a trigger-word stub.

## Method (fixed before the run)

- **Dataset:** the identical 240-point set from
  `results/selection-sweep/selection_sweep.jsonl` — same 40 plans, same 15
  confusable clusters, same clear/ambiguous/out-of-catalog intents, same
  catalog sizes (N = 2, 5, 10, 20, 40). No new intents, no rewording.
- **Selector:** cosine similarity between the intent embedding and each
  catalog plan's embedded description + triggers; top-1 selection. The
  embedding model is named in this document by amendment **before** the run
  (candidate class: a current general-purpose text-embedding model; the
  choice is made on price/availability, never on eval-set performance).
- **Refusal (out-of-catalog):** similarity threshold τ. τ is selected by
  grid search on a **disjoint** synthetic set constructed by the same
  generator with a different seed — chosen and frozen before the eval set is
  touched. No post-hoc threshold adjustment.
- **Metrics:** identical to `selection_sweep_report.json` — accuracy per
  intent kind per N, out-of-catalog refusal accuracy, checkpointed JSONL,
  deterministic report. Evidence lands in `results/selection-sweep/` beside
  the LLM records.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional.** The result enters `results/`, NOTES and
README whatever it shows. Withholding an uncomfortable number would be the
exact failure mode this repository documents in agents.

- **Branch A — embeddings match the LLM** (clear-intent accuracy within 5
  points of the LLM at every N): the README claim that the selection layer
  "buys paraphrase robustness that keyword routing cannot provide" is
  **replaced**, not reworded — the measured statement becomes: embedding
  similarity provides equivalent paraphrase robustness at a fraction of the
  cost, and the LLM selection layer's remaining justification narrows to
  whatever the data still supports (typed out-of-catalog refusal behavior,
  zero-infrastructure operation), stated with its measured deltas. The cost
  paragraph is rewritten accordingly.
- **Branch B — embeddings fall materially short** (clear-intent accuracy
  more than 5 points below the LLM at any N, or refusal accuracy below the
  LLM's 100%): the counterfactual returns to the Key results table — now as
  a measured row with an evidence file, in the exact terms the data shows.
- **Branch C — mixed** (e.g., clear intents match but ambiguous intents or
  refusal diverge): each sub-result is reported in its own branch's terms;
  no composite headline number is constructed across kinds. Composite ranges
  built from different populations are how this repository acquired the
  citation error it just corrected.

The ambiguity floor (~75%) is compared separately in every branch: whether a
non-LLM selector hits the same floor is evidence about the *eval set* versus
the *model*, and is reported as such.

## Why pre-registered

Same discipline as the queued cross-model integrity-matrix run: the decision
of what gets published is made before the number is seen. Deciding after is
where it comes apart.
