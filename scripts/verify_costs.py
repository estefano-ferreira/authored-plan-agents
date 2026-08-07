#!/usr/bin/env python
"""Recompute every recorded cost figure from its token counts and the runner's
own pricing table, and fail loudly on any mismatch.

This is the executable behind the "Price basis" notes in results/README.md and
docs/paper/integrity-matrix.tex: a reader should not have to trust the claim
that recorded costs are reproducible from tokens x list rates -- they run this.

The pricing table is imported from the platform's observability writer (the
single source of truth the runner itself uses), never restated here. A cost
recomputed with zero cached tokens that matches the recorded figure exactly is
also arithmetic proof that no cached tokens were billed in that record: any
cache_read > 0 would lower the cost via the 90% cache discount.

Usage:  ./.venv/Scripts/python scripts/verify_costs.py
Exit code 0: every recorded cost reproduced exactly. Non-zero: mismatches
listed on stderr.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from infrastructure.observability.console_writer import (  # noqa: E402
    PRICING_USD_PER_MILLION_TOKENS,
)

MATRIX_DIR = REPO / "results" / "integrity-matrix"
TOLERANCE = 1e-9  # float noise only; recorded costs are sums of exact per-call terms


def recompute(model: str, tokens_in: int, tokens_out: int) -> float:
    prices = PRICING_USD_PER_MILLION_TOKENS[model]
    return tokens_in / 1_000_000 * prices["input"] + tokens_out / 1_000_000 * prices["output"]


def main() -> int:
    mismatches = []
    records = 0
    # Baseline file plus every model-suffixed cross-model file (integrity_matrix-<slug>.jsonl).
    jsonl_files = sorted(MATRIX_DIR.glob("integrity_matrix*.jsonl"))
    for jsonl in jsonl_files:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            records += 1
            expected = recompute(r["model"], r["tokens_in"], r["tokens_out"])
            if abs(expected - r["cost"]) > TOLERANCE:
                mismatches.append(
                    f"  {jsonl.name} cell={r['cell']} rep={r['rep']}: recorded {r['cost']!r}, "
                    f"recomputed {expected!r} from {r['tokens_in']}/{r['tokens_out']} tokens"
                )
    if mismatches:
        print(f"[verify_costs] {len(mismatches)}/{records} records do NOT reproduce:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(
        f"[verify_costs] {records}/{records} recorded costs reproduced exactly from "
        f"token counts x list rates (zero cached tokens confirmed arithmetically)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
