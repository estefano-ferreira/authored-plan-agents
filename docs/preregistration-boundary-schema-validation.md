# Pre-registration — full-schema boundary validation arm (cells C/D/E/F)

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-07, before any script
or implementation change and before any number exists. This document is
committed before the boundary code is strengthened and before the runner
gains the evidence-suffix mechanism described below; any deviation from it
must be recorded as a dated amendment above the registered text, never by
editing it.

This is the first pre-registration written under the process rule adopted in
the 2026-08-07 erratum to `preregistration-schema-fault-cells.md`: the
"What the code already determines" section below was written first, and only
what survives it is registered as a prediction.

## Question

The paper (v0.4.1) reports its boundary result with a deliberate
calibration: the boundary applies a *decode-shape check* (`json.loads`, a
bare-JSON-object test, presence of the contract's required keys), so the
measured claim is about the *position* of the check, not its validation
strength. The external review of v0.4.0 posed the strong version of the
question: **does the result survive when the boundary performs full
validation of the declared schema** — types, enum, and
`additionalProperties: false` — instead of the shape check?

This arm strengthens `_parse_strict`
(`src/ai/agents/correspondence/read_and_reply.py`) to validate the complete
`_RESPONSE_SCHEMA` (JSON object; exactly the three required keys; no
additional properties; all three values strings; `request_type` in
`{support, order, cancellation}`), as a permanent upgrade of the reference
implementation, and re-runs the four schema-bearing cells (C, D, E, F) of
the integrity matrix under it.

## What the code already determines (written before any prediction)

1. **Cell F (schema + fault + strict) is determined.** The injector
   re-fences every generation response, including the retry; fenced content
   fails `json.loads` before any schema logic is reached. Outcome:
   `failed_clean` 10/10, zero rows — identical to the recorded F. This cell
   is a regression check on the strengthened guard's failure path, nothing
   more.
2. **Cell E (schema + fault + tolerant) is determined — and that is the
   point.** The absorbing fallback (`TolerantRepairClient`, described as
   coded since the 2026-08-07 erratum) replaces any non-JSON response with
   `{"request_type": "support", "summary": content[:200], "reply_body":
   content}`. That payload is **schema-valid by construction**:
   `request_type` is in the enum, both other values are strings, exactly the
   three declared keys are present. No boundary-side validation of this
   schema, at any strength, can reject it. Outcome: `completed` 10/10, ten
   garbage rows, contract counters at zero. If the run confirms this, the
   paper's positional claim ("position, not strength") stops being a
   structural argument supported by a shape-check measurement and becomes a
   result **measured under the strongest boundary check the declared schema
   admits**. Reported as a verification, per the erratum's rule.
3. **Cells C and D are the only open cells.** Under an active decoding
   constraint the provider should emit schema-conforming content, but
   provider conformity is exactly what the naturally occurring dialect
   incident showed can silently break. The strengthened check can reject
   content the shape check would have passed (extra keys, non-string
   values, out-of-enum `request_type`). Whether any such
   *full-schema-only rejection* occurs in 10 natural repetitions per cell
   is not determined by this repository's code — it depends on the
   provider's decoding-level enforcement.
4. **The control arm is unaffected by construction.** The boundary code
   runs only on the generation step; the no-generation plan never reaches
   it. The control cell is therefore not re-run, and this exclusion is
   registered here rather than decided after the fact.

## Registered predictions (only what survived the section above)

- **P1:** cells C and D complete 10/10 with zero contract violations —
  zero full-schema-only rejections — and remain byte-equivalent in outcome
  (10 clean persisted rows each, ~same tokens, ~same cost). A 0/10
  observation bounds the underlying rate at ≤ 25.9% (one-sided 95%), as
  everywhere in this study.
- **P2:** the strengthened check's idle cost is zero — no additional model
  calls in C/D relative to the recorded C/D (validation strength changes
  no call pattern; only rejection behavior could, and P1 predicts none).

## Method (fixed before the run)

- **Boundary change (after this document is committed):** `_parse_strict`
  validates the complete `_RESPONSE_SCHEMA` as specified in the Question;
  pure stdlib, no new dependency (the pinned dependency set is part of the
  v0.4.1 reproducibility claim). Unit tests cover: each rejection class
  (missing key, extra key, non-string value, out-of-enum `request_type`,
  non-object, invalid JSON) and — deliberately, as executable documentation
  of determination fact 2 — the absorbing fallback's payload passing the
  full check.
- **Runner change (after this document is committed):** a generic evidence
  suffix (env `MATRIX_EVIDENCE_SUFFIX`), layered onto the existing path
  scheme exactly like the `-pg` suffix, so this arm's records land beside —
  never over — the immutable baseline evidence, and the resume mechanism's
  skip-recorded-(cell, rep) logic operates within this arm's own files.
- **Cells:** C, D, E, F; 10 repetitions each (40 executions of the
  two-agent inbound plan, each against a fresh SQLite).
- **Model:** `gemini-3.1-flash-lite` — the baseline model, held fixed; the
  boundary's validation strength is the only change.
- **Evidence:** suffix `fullschema` —
  `integrity_matrix-fullschema.jsonl`, `integrity_matrix_report-fullschema.json`,
  `matrix-{C,D,E,F}-fullschema.sqlite`, audit trails; tracked in
  `results/integrity-matrix/` in the existing formats.
- **Call budget (same arithmetic as the E/F pre-registration):** ≈4 model
  calls per execution in C/D/E, ≈5 in F — roughly 170 calls, inside
  free-tier quota with idempotent resumes if a daily cap interrupts.
- **What is read as the result:** reported status; contract
  violation/retry/failure counters; repair count; rows created in the ERP;
  per-row validity of the persisted `summary` by direct SQL inspection
  (never via platform state); tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — everything as determined and predicted.** C/D clean and
  byte-equivalent, E persists ten schema-valid garbage rows with counters
  at zero, F refuses 10/10. Committed consequence: the paper's §2 boundary
  description, F3's idle-cost sentence and the conclusion's first
  requirement are upgraded from "decode-shape check" to "full validation of
  the declared schema", each still bounded as an observation; E's result is
  reported as the measured form of the positional claim under maximal
  boundary strength — as a verification, never as a discovery.
- **Branch B — any full-schema-only rejection in C or D.** The count is
  reported in the data's exact terms as a provider-conformity finding (the
  guard's live path exercised naturally); P1 is reported as falsified.
- **Branch C — any deviation from determination facts 1 or 2** (any E rep
  not persisting the absorbed payload; any F rep persisting a row; any E/F
  counter nonzero): falsifies this document's code reading or the
  implementation, and is reported as a bug finding against the
  implementation, not smoothed into the narrative.
- **Branch D — anything else** (mixed reps, upstream selection
  divergences): reported separately in the data's exact terms, never
  composited.

## Why pre-registered

The uncomfortable branches are fixed in advance: Branch B would complicate
the study's claim that decoding-level enforcement suppresses natural
violations, and Branch C would falsify the very code reading on which the
2026-08-07 erratum rests. Both publish unconditionally. The determination
section exists so that no deterministic outcome can later be dressed up as
a discovery — the failure mode the erratum documented.
