# Pre-registration — cells A and B with PostgreSQL as the system of record

**Status: PRE-REGISTERED, NOT RUN.** Authored 2026-08-06, before any number
exists. One honesty note on ordering: the runner's generic Postgres support
(per-cell databases, `-pg` evidence suffix, snapshot export) was built and
committed before this document, as provider-agnostic infrastructure; what
this document fixes before any number is the question, the configuration,
and the publication branches. Deviations are recorded as dated amendments
above the registered text, never by editing it.

## Question

Do the boundary-axis readings of cells A and B hold when the system of
record runs on PostgreSQL — a different constraint engine executing the
same declared invariants (partial unique indexes: one `open` request per
customer, one `booked` appointment per slot)? The original matrix and
every subsequent arm ran the ERP on SQLite.

**Scope, stated precisely.** This varies the *engine*, not the *owner*:
the ERP remains this study's own service, with invariants its author
declared. The self-built-system-of-record circularity documented in the
paper's limitations is **not** addressed by this experiment and no claim
of independence is made. What is tested is narrower: whether the
boundary readings are portable across constraint engines — a SQLite
artifact would be a real finding against the study's generality.

## Method (fixed before the run)

- **Cells:** A and B, 10 reps each, exactly as originally configured
  (schema stripped + fault injector + tolerant/strict respectively).
- **Model:** `gemini-3.1-flash-lite` — the baseline model, held fixed:
  one variable per experiment; the engine is the only change.
- **ERP:** `MATRIX_ERP_DATABASE_URL=postgresql+psycopg://erp:erp@127.0.0.1:5433/erp`
  against the compose service `postgres-erp` (isolated from the
  platform's Postgres per the pattern's own boundary rule); per-cell
  databases (`matrix_a`, `matrix_b`), dropped fresh only while a cell
  has zero recorded reps.
- **Evidence:** `-pg`-suffixed files beside the baseline
  (`integrity_matrix-pg.jsonl`, report, audit trails) and per-cell
  snapshot SQLite exports as tracked evidence — the uniform format every
  reader script already accepts. Ground truth read from the live
  Postgres by direct SQL, then snapshotted.

## Pre-committed outcomes (decided before any number exists)

**Publication is unconditional in every branch.**

- **Branch A — full transfer.** A: `completed` 10/10 with garbage
  persisted in Postgres and platform counters at zero; B: `failed_clean`
  10/10, zero rows. The boundary readings are engine-portable; the
  paper's telemetry-blindness instances gain an engine-diversity note,
  nothing else changes.
- **Branch B — divergence.** Any cell reading differently than its
  SQLite original — including engine-specific constraint behavior
  (e.g., Postgres rejecting or accepting writes the SQLite path
  did not) — is reported in the data's exact terms as an
  engine-sensitivity finding against the study's generality.
- **Branch C — divergence upstream of the boundary** (selection or
  connector failures unrelated to the engine): reported separately,
  never composited.

## Why pre-registered

Same discipline as every arm since E/F: what counts as transfer, what
counts as an engine artifact, and what counts as a different question is
fixed before the number is seen.
