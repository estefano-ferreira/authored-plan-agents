# Pre-registration — full-schema boundary arm on PostgreSQL (cells C/D/E/F)

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-07, after the SQLite
full-schema arm completed and before any Postgres-backed number for these
cells exists. No script or implementation change is required or planned for
this arm: the runner's generic evidence-suffix mechanism and its Postgres
support (both committed before this document) compose to
`-fullschema-pg`-suffixed evidence by construction. Deviations are recorded
as dated amendments above the registered text, never by editing it.

## What the code already determines (written before any prediction)

1. **The model cannot see the engine.** The persistence engine sits strictly
   downstream of every model call and of the boundary check; nothing in the
   request path encodes it. The generated content distribution in C/D and
   the injected corruption in E/F are therefore engine-independent by
   construction.
2. **Cells E and F remain code-determined**, for the reasons registered in
   `preregistration-boundary-schema-validation.md` (the absorbing fallback's
   payload is schema-valid by construction; the injector re-fences the
   strict guard's retry). The engine changes nothing upstream of the write.
3. **What the engine can decide is only write acceptance.** The ERP's
   invariants are declared identically on both engines (partial unique
   indexes); the corrupted `summary` in E is content of valid form, which no
   declared constraint distinguishes — the study's central point. The prior
   Postgres arm (cells A/B, 2026-08-06) measured exactly this
   non-interaction and found full transfer.
4. **What this arm adds is scope, not mechanism**: cells C, D, E, F have
   never run against PostgreSQL in any round (the 2026-08-06 engine arm
   covered A/B only). This run closes that gap; every outcome is expected
   to be a verification.

## Registered predictions (only what survived the section above)

- **P1:** full transfer — C/D `completed` 10/10, zero contract events, 10
  clean rows each in Postgres; E `completed` 10/10 with 10 garbage rows and
  counters at zero; F `failed_clean` 10/10, zero rows.
- **P2:** the natural-violation observation in C/D repeats (0/10 each,
  bounded ≤ 25.9% one-sided 95%) — same model, same decoding constraint;
  the engine gives no mechanism for change.

## Method (fixed before the run)

- **Cells:** C, D, E, F; 10 repetitions each; model
  `gemini-3.1-flash-lite`; boundary at full-schema validation (the code as
  committed for the SQLite full-schema arm — no change).
- **Configuration:** `MATRIX_EVIDENCE_SUFFIX=fullschema` plus
  `MATRIX_ERP_DATABASE_URL=postgresql+psycopg://erp:erp@127.0.0.1:5433/erp`
  (the `postgres-erp` compose service, isolated per the pattern's boundary
  rule); per-cell databases `matrix_<cell>_fullschema`, dropped fresh only
  while the cell has zero recorded reps.
- **Evidence:** `-fullschema-pg`-suffixed files in the uniform formats
  (`integrity_matrix-fullschema-pg.jsonl`, report, audit trails,
  `matrix-{C,D,E,F}-fullschema-pg.sqlite` snapshot exports). Ground truth
  read from the live Postgres by direct SQL, then snapshotted.
- **What is read as the result:** reported status; contract counters;
  repair count; rows created; per-row validity of the persisted `summary`
  by direct SQL against the live Postgres; tokens and cost.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — full transfer (P1, P2 hold).** Reported as the verification
  it is: the full-schema boundary readings are engine-portable; the
  self-built-system-of-record circularity remains open and declared, as in
  every engine arm.
- **Branch B — any engine-sensitive divergence** (a cell reading
  differently than its SQLite full-schema original, including
  engine-specific constraint behavior): reported in the data's exact terms
  as an engine-sensitivity finding against the study's generality.
- **Branch C — divergence upstream of the boundary** (selection or
  connector failures unrelated to the engine): reported separately, never
  composited.

## Why pre-registered

Same discipline as every arm: what counts as transfer versus artifact is
fixed before the number is seen. The determination section keeps this arm
honest about being a scope-closing verification — if it is published as
exactly that, no outcome here can be dressed up as a discovery.
