# Pre-registration — genuine-extractor repair cells G and H

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-07, before the
extractor instrument exists and before any number. The instrument
(`ExtractorRepairClient`), the `extractor_repair` composition parameter and
the runner cells G/H are implemented **only after this document is
committed.** Deviations are recorded as dated amendments above the
registered text, never by editing it.

## Question

The v0.5.0 external review (M1) observed that this study has measured only
an *absorbing* repairer — the historical fallback that stuffs the raw
response into the contract's fields — while the practitioner literature's
default repair is *extraction* (markdown fence removal, JSON span
extraction). In cell E the injected fence wraps schema-valid JSON, so a
genuine extractor would plausibly recover valid content where the absorber
persisted garbage. Two cells complete the repair-policy axis with a genuine
extractor:

- **Cell G** — real `responseSchema` + `GenerationFaultInjectionClient` +
  extractor repair;
- **Cell H** — schema stripped + injector + extractor repair.

**Instrument, fixed here.** `ExtractorRepairClient` mirrors the
practitioner "code fence removal" strategy and nothing else: for
`purpose == "generation"` responses whose content matches a single
markdown fence envelope (optional language tag) — `^\s*```[a-zA-Z]*\n
(.*)\n```\s*$`, single pass, no recursion — it replaces the content with
the inner text and records one repair; anything else passes through
unchanged. No absorption, no field-stuffing, no second pass. The boundary
guard (`retry_once_then_fail`, full-schema validation) runs downstream,
unmodified, in both cells.

## What the code already determines (written before any prediction)

1. **The injector's fence matches the extractor's pattern by
   construction.** The injector wraps exactly once:
   `f"```json\n{content}\n```"`. A single-pass extractor therefore always
   strips exactly the injected layer.
2. **Cell G is determined conditional on decoding conformity.** Under an
   active schema the provider returns a schema-valid JSON object (natural
   violation rate measured 0/10 twice — cells C/D at both validation
   strengths); the injector adds one fence; the extractor removes it; the
   recovered content passes full validation by the same conformity.
   Determined outcome: `completed` 10/10, **10 valid rows**, contract
   counters 0/0/0, repairs 10. If this holds, it is reported as a
   verification with one pre-committed framing consequence (Branch A
   below). The conditional (provider conformity) is the only opening.
3. **Cell H is genuinely open.** Without the schema the content under the
   injected fence is unconstrained. The historical incident round measured
   this model self-fencing 10/10 under prompt-only control; if the model
   self-fences, the injector's layer makes it double-fenced, a single-pass
   extractor strips only one layer, and the guard then refuses — but the
   self-fencing rate is a *model behavior*, not code, and the recovered
   inner content (when single-fenced) may or may not satisfy the full
   schema. H is this design's second genuinely stochastic cell.

## Registered predictions (only what survived the section above)

- **P1 (cell H):** based on the historical 10/10 self-fencing rate:
  `failed_clean` ≥ 8/10 (double fence defeats a single-pass extractor),
  zero rows persisted in failed repetitions.
- **P2 (both cells):** contract counters remain blind to every repaired
  violation (0 violations recorded in any repetition where extraction
  succeeds) — the positional mechanism is indifferent to whether the
  repairer absorbs or extracts.

## Method (fixed before the run)

- **Implementation (after this commit):**
  `src/infrastructure/providers/extractor_repair_client.py` as specified
  above; `build_platform(extractor_repair=...)` mutually exclusive with
  `tolerant_repair` (both True is a composition error, raised); runner
  cells `G = {schema, fault, extractor}` and `H = {no schema, fault,
  extractor}` with 10 default reps; unit tests for the extractor (strips
  single fence with/without language tag; passes through bare JSON,
  double-fenced content, prose; counts repairs).
- **Cells:** G and H, 10 reps each; model `gemini-3.1-flash-lite`; fresh
  SQLite per rep; boundary at full-schema validation (current `main`).
- **Evidence:** baseline namespace (no evidence suffix — G/H are new
  cells, not a re-run): records appended to `integrity_matrix.jsonl`,
  snapshots `matrix-{G,H}.sqlite`, audit trails, consolidated report.
- **What is read as the result:** status; contract counters; repair
  count; rows; per-row `summary` validity by direct SQL; tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — G persists 10 valid rows with counters at zero.** The
  committed framing consequence, stated now because it complicates this
  study's own conclusion: the sentence "the alternative to refusal was
  not recovery; it was corruption" becomes **repairer-specific** — true
  as measured for the absorbing fallback, false for a genuine extractor
  under an injected single fence. The paper's conclusion and §6 are
  updated to say exactly that, and F's refusals in cells B/F are
  re-described as refusing content a genuine extractor would have
  recovered (as §6 already concedes in principle). Telemetry blindness
  is NOT weakened by this branch — the counters stay at zero either way
  (P2) — but the *economic* case for strict-over-repair narrows to:
  what the guard buys is not superiority over any repair, it is typed
  visibility; a correct extractor beats both on availability while
  keeping the counters equally blind. If P2 also holds, that sentence
  is the branch's headline, verbatim.
- **Branch B — G refuses or persists garbage** (any G rep with invalid
  rows or `failed_clean`): falsifies determination fact 2's conditional
  or the instrument; reported in the data's exact terms, including as a
  bug finding if the extractor misbehaves.
- **Branch C — H in any distribution.** Reported as measured, per
  repetition, with the self-fencing rate it implies; P1 scored
  numerically.
- **Branch D — anything else** (upstream divergences): reported
  separately, never composited.

## Why pre-registered

Branch A is uncomfortable on purpose: it forces this study to publish the
strongest honest version of the reviewer's objection against its own
headline economics. The determination section fixes, before any number,
that G confirming is a verification — so the only discovery credit
available here is H's, where the code genuinely does not decide.
