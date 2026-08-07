#!/usr/bin/env python
"""Pre-registered paired analysis: LLM vs embedding selector, per unique intent.

The executable behind the embedding-baseline reading (power amendment of
2026-08-06 in docs/preregistration-embedding-baseline.md): pooled per intent
kind, exact one-sided sign test (alpha = 0.05, pre-registered direction =
embedding deficit), plus the catastrophic refusal criterion (>=5
one-directional discordants of 15).

The amendment did not fix how the LLM's 2 recorded reps collapse into one
per-intent verdict; per the collapse-rule amendment (same date), BOTH
defensible rules are computed and reported side by side -- strict (correct in
both reps) and rep1 (first recorded rep) -- so the reader sees exactly where
the decision is and is not invariant to that analytic choice.

Usage:  ./.venv/Scripts/python scripts/paired_selection_analysis.py
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SWEEP_JSONL = REPO / "results" / "selection-sweep" / "selection_sweep.jsonl"
EMB_JSONL = REPO / "results" / "selection-sweep" / "embedding_baseline.jsonl"

ALPHA = 0.05
LLM_MODEL = "gemini-3.1-flash-lite"


def sign_test_geq(k: int, d: int) -> float:
    """One-sided exact P(X >= k | d, 0.5)."""
    return sum(math.comb(d, i) for i in range(k, d + 1)) / 2**d


def main() -> int:
    llm = defaultdict(list)
    for line in SWEEP_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("model", LLM_MODEL) != LLM_MODEL:
            continue
        llm[(r["catalog_size"], r["intent_id"])].append(bool(r["correct"]))

    emb, kind_of = {}, {}
    for line in EMB_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["catalog_size"], r["intent_id"])
        emb[key] = bool(r["correct"])
        kind_of[key] = r["kind"]

    for collapse_name, collapse in (("strict(both reps)", all), ("rep1", lambda v: v[0])):
        print(f"=== LLM collapse rule: {collapse_name} ===")
        by_kind = defaultdict(lambda: {"n": 0, "llm_only": 0, "emb_only": 0, "both": 0, "neither": 0})
        for key, emb_ok in emb.items():
            if key not in llm:
                continue
            llm_ok = collapse(llm[key])
            b = by_kind[kind_of[key]]
            b["n"] += 1
            if llm_ok and not emb_ok:
                b["llm_only"] += 1
            elif emb_ok and not llm_ok:
                b["emb_only"] += 1
            elif llm_ok:
                b["both"] += 1
            else:
                b["neither"] += 1
        for k, b in sorted(by_kind.items()):
            d = b["llm_only"] + b["emb_only"]
            p = sign_test_geq(b["llm_only"], d) if d else 1.0
            diff = (b["llm_only"] - b["emb_only"]) / b["n"] * 100
            flag = "SIGNIFICANT" if p <= ALPHA else ""
            print(
                f"  {k:<10} n={b['n']:<3} both={b['both']:<3} neither={b['neither']:<2} "
                f"LLM-only={b['llm_only']} EMB-only={b['emb_only']} | paired diff={diff:+.1f} pts "
                f"| one-sided p(deficit)={p:.4f} {flag}"
            )
        none_b = by_kind.get("none", {"llm_only": 0})
        print(
            f"  refusal catastrophic criterion: LLM-only discordants={none_b['llm_only']}/15 "
            f"(>=5 one-directional is significant alone)\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
