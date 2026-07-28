#!/usr/bin/env python
"""One-command resume for the integrity-matrix run (cells C, D, control).

Loads credentials from .env (LangSmith deliberately EXCLUDED — matrix runs keep
it inert), pins the matrix model, and runs the remaining cells in order. Every
cell is independently resumable: already-recorded (cell, rep) pairs in
results/integrity-matrix/integrity_matrix.jsonl are skipped, so re-running this script after a
daily-quota exhaustion continues exactly where it stopped.

Usage:  ./.venv/Scripts/python scripts/resume_matrix.py
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MATRIX_MODEL = "gemini-3.1-flash-lite"  # same model for every cell — do not mix
CELLS = (("A", "10"), ("B", "10"), ("C", "10"), ("D", "10"), ("control", "3"))


def load_env() -> None:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if value and not key.startswith("LANGSMITH"):
            os.environ[key] = value
    os.environ["GEMINI_MODEL"] = MATRIX_MODEL


def main() -> int:
    load_env()
    if not os.environ.get("GOOGLE_API_KEY"):
        print("GOOGLE_API_KEY missing in .env — fill it in first.", file=sys.stderr)
        return 2
    for cell, reps in CELLS:
        print(f"\n=== matrix cell {cell} (reps={reps}) — resumable ===")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "run_plans.py"),
             "--provider", "gemini", "--matrix-cell", cell, "--repeat", reps],
            cwd=REPO,
        )
        if result.returncode != 0:
            print(f"cell {cell} exited {result.returncode} — likely quota; "
                  "re-run this script later, it will resume.", file=sys.stderr)
            return result.returncode
    print("\nAll cells recorded. Consolidated report:")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "run_plans.py"), "--matrix-report-only"],
        cwd=REPO,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
