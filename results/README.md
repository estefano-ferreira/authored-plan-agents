# results/ — tracked measurement evidence

Everything in this directory is **evidence, not documentation**: append-only,
never rewritten, never regenerated in place (reports are the one exception —
they are deterministic recompositions of the JSONL records and are safe to
rebuild with the report-only commands below). This rule exists because the
study lost its first baseline to a cleanup step while `results/` was still
gitignored; see "Provenance notes".

## Layout

| Directory | Contents | Produced by |
|---|---|---|
| `runs/` | `run-<YYYYMMDD-HHMMSS>.json` (per-execution metrics + aggregates, one file per measurement run) and `audit-run-<ts>.jsonl` (the complete audit trail of that run) | `scripts/run_plans.py --repeat N` |
| `integrity-matrix/` | `integrity_matrix.jsonl` (one record per (cell, rep) — the raw data), `integrity_matrix_report.json` (per-cell aggregates + direct SQLite inspection), `audit-matrix-<cell>-<ts>.jsonl` (audit trails; cell C has **three** files because the run crossed a daily-quota boundary and was resumed twice), `matrix-{A,B,C,D,control}.sqlite` (**immutable post-run snapshots** of each cell's ERP database — cell A's 10 garbage rows are directly inspectable here) | `scripts/run_plans.py --matrix-cell X --repeat N` (via `scripts/resume_matrix.py`); snapshots copied from `var/erp/` after completion |
| `selection-sweep/` | `selection_sweep.jsonl` (one record per (model, rep, N, intent) — checkpoint/resume granularity), `selection_sweep_report.json` (accuracy-vs-N curves per model) | `scripts/selection_sweep.py` |

## Regenerating the reports (safe; reads JSONL + snapshots, no model calls)

```bash
python scripts/run_plans.py --matrix-report-only
python scripts/selection_sweep.py --report-only
```

## Reading the evidence

- **Model:** every real-model record in this directory is
  `gemini-3.1-flash-lite`, except the cross-model spot-check records inside
  `selection-sweep/selection_sweep.jsonl` (`gemini-3.6-flash`,
  `gemini-3-flash-preview`).
- **The DB is the ground truth, not the status.** The integrity matrix's
  central finding is that statuses and even audit counters can read clean
  while the database holds garbage (cell A). When in doubt, open the SQLite
  snapshots.
- **Vocabulary:** audit trails recorded before 2026-07-28 carry the
  pre-rename kind `authoritative`; the current vocabulary calls it
  `system_of_record` (see `docs/adr/ADR-001-system-of-record-classification.md`).
  Any reader script must accept both. The intermediate name `custodial` never
  reached recorded evidence.
- **Latency caveats:** matrix latency aggregates include 429-retry backoff
  waits (the run crossed a free-tier daily quota); stall counts and the 111 s
  worst case are flagged in `NOTES.md` § Integrity matrix. Row counts,
  statuses, tokens and costs are per-call and unaffected.
- Reports embed only repo-relative paths. The `db_path` fields in
  `integrity_matrix_report.json` point at the live runtime DBs under `var/`
  (gitignored); the tracked snapshots of those same databases sit in
  `integrity-matrix/` beside the report.

## Provenance notes

- `runs/run-20260727-212338.json` (the first real-model round's raw
  per-execution file) **does not exist**: it was deleted by a cleanup step
  before the repository was under version control, while `results/` was still
  gitignored. Its aggregates survive in `NOTES.md` § Real-model findings and
  § Structured output correction. Consequence applied: `results/` is tracked
  evidence ever since, and this manifest exists.
- File naming keeps the original run timestamps (`<YYYYMMDD-HHMMSS>`, local
  time of the machine that ran them); files are never renamed after the fact.
