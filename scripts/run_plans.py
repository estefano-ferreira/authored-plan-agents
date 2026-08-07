#!/usr/bin/env python
"""Runs the two reference plans end to end and prints metrics per execution.

Usage:
    ./.venv/Scripts/python scripts/run_plans.py
    ./.venv/Scripts/python scripts/run_plans.py --provider local
    ./.venv/Scripts/python scripts/run_plans.py --erp inprocess
    ./.venv/Scripts/python scripts/run_plans.py --erp-url http://localhost:9000
    ./.venv/Scripts/python scripts/run_plans.py --serve-erp
    ./.venv/Scripts/python scripts/run_plans.py --repeat 10
    ./.venv/Scripts/python scripts/run_plans.py --provider gemini --repeat 20

The ERP/scheduling service is now the real `erp_service` (SQLAlchemy + SQLite), not an
in-memory mock -- its state is persisted and no longer resets itself between runs. Three ways
to reach it, controlled by `--erp {http,inprocess}` (default `http`):

- `--erp http` (default): talks to a real server over HTTP, at `--erp-url`, else env
  `ERP_BASE_URL`, else `http://127.0.0.1:8123`. Nothing is started for you -- bring one up
  first with `--serve-erp`, `docker compose up erp`, or a manual `uvicorn erp_service.api:app`.
  A clear error (with the same hints) is printed if that address isn't reachable.
- `--erp inprocess`: no server process at all -- `httpx.ASGITransport` directly over
  `erp_service.api.create_app("sqlite:///var/erp/erp.sqlite")`. Fast and dependency-free like
  the old in-process mock, but the SQLite file persists across runs of this script (see the
  module docstring's note on state persistence below).
- `--serve-erp`: brings up the real `erp_service` app itself via uvicorn on a local thread, at
  the same `http://127.0.0.1:8123` address `--erp http` would otherwise expect.

Two modes:

- **Legacy mode** (no `--repeat`): unchanged from the original script. Runs each of the two
  plans exactly once, selected *explicitly* (`request["plan"]`), and prints a short summary.
  No result file is written.
- **Measurement mode** (`--repeat [N]`, N defaults to 1 when the flag is given without a value):
  builds a single shared `Platform` (realistic sessions/caches across repeats) and runs each of
  the two plans N times. Requests carry a natural-language `intent` instead of an explicit
  `"plan"`, so the *model* selects the plan (and, inside each agent, the capability) — this is
  the primary variable under measurement. Per-execution and aggregate metrics (tokens, cost,
  latency, selection distribution, Output Contract violations) are printed to the console and
  written to `results/runs/run-<YYYYMMDD-HHMMSS>.json`.
- **Integrity matrix mode** (`--matrix-cell {A,B,C,D,control,E,F}`, requires `--repeat`): one
  experiment, same codebase, only `build_platform()` flags vary per cell (see
  `infrastructure/configurations.py`'s `strip_response_schema`/`inject_generation_fault`/
  `tolerant_repair`). Each invocation runs one cell's not-yet-recorded reps against that cell's
  own isolated ERP SQLite (`var/erp/matrix-<cell>.sqlite`), appends to
  `results/integrity-matrix/integrity_matrix.jsonl`, and (re)writes that cell's block of
  `results/integrity-matrix/integrity_matrix_report.json` (aggregates + a direct sqlite3 inspection of the ERP
  database). Already-recorded (cell, rep) pairs are skipped on the next invocation, so a matrix
  run can be stopped and resumed cell by cell, rep by rep. `--matrix-report-only` recomposes the
  report from disk (JSONL + per-cell SQLite files) with no model/ERP calls at all. See the
  "Integrity matrix mode" section below for the cell configurations and mechanics.
"""
import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
for path in (_REPO_ROOT / "src", _REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from core.identity import Principal  # noqa: E402
from infrastructure.configurations import build_platform  # noqa: E402
from infrastructure.observability.console_writer import (  # noqa: E402
    PRICING_USD_PER_MILLION_TOKENS,
)
# erp_service is imported lazily (inside --serve-erp / --erp inprocess paths only):
# the platform container deliberately does not ship the ERP code — its sole channel
# to the business system is HTTP (ERP_BASE_URL).

_ERP_HOST = "127.0.0.1"
_ERP_PORT = 8123
_ERP_DEFAULT_URL = f"http://{_ERP_HOST}:{_ERP_PORT}"
# Persistent (file-backed) SQLite for `--erp inprocess`: unlike the old in-memory mock, state
# survives across separate invocations of this script -- see the module docstring.
_ERP_INPROCESS_DATABASE_URL = "sqlite:///var/erp/erp.sqlite"
_RESULTS_DIR = _REPO_ROOT / "results"
_VAR_DIR = _REPO_ROOT / "var"

# Mirrors console_writer._cost_usd's discount for cache reads (kept local: the helper in
# console_writer is private and per-execution totals there are not broken down by purpose,
# which is exactly what the selection-vs-generation cost split below needs).
_CACHE_READ_DISCOUNT = 0.10

# Both the orchestrator's plan-selection prompt and the runtime's capability-selection prompt
# render their catalog as one "- <id>: <description...>" line per candidate (see
# `ai/orchestrator.py::_resolve_plan` and `ai/agents/runtime.py::_select_capability`). Extracting
# the offered ids this way lets the violation check below stay generic across both call sites.
_CATALOG_ID_RE = re.compile(r"(?m)^- ([\w.-]+):")


def _serve_erp_in_thread(host: str = _ERP_HOST, port: int = _ERP_PORT):
    """Brings up the real erp_service app via uvicorn on a daemon thread; returns the running Server object.

    Uses the module-level `erp_service.api.app` (env `ERP_DATABASE_URL`, default in-memory SQLite) --
    a fresh process import, so there is nothing to reset first.
    """
    import uvicorn

    from erp_service.api import app as erp_app

    config = uvicorn.Config(erp_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("ERP service did not start in time (--serve-erp)")
    return server


def _check_erp_reachable(base_url: str) -> None:
    """Preflight GET against the ERP's scheduling policy endpoint; friendly hint on connect failure.

    Without this, an unreachable ERP in `--erp http` mode only surfaces as a generic
    `httpx.ConnectError` buried inside the first plan's `ExecutionResult.rejection.reason` (the
    Orchestrator catches and compensates it like any other step failure) -- easy to misread as a
    business-invariant rejection instead of "nothing is listening on that port".
    """
    try:
        httpx.get(f"{base_url}/policies/scheduling", timeout=3.0)
    except httpx.ConnectError as exc:
        print(
            f"[run_plans] cannot reach ERP at {base_url}: {exc}\n"
            "  hint: start one first -- './.venv/Scripts/python scripts/run_plans.py --serve-erp', "
            "'docker compose up erp', or run this script with '--erp inprocess' "
            "(no server needed, persists to a local SQLite file).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _setup_erp(args) -> tuple[object | None, object | None]:
    """Resolves how the real ERP/scheduling service is reached; returns (asgi_transport, uvicorn_server)."""
    if args.serve_erp:
        server = _serve_erp_in_thread()
        os.environ["ERP_BASE_URL"] = _ERP_DEFAULT_URL
        print(f"[run_plans] ERP service served via uvicorn at {os.environ['ERP_BASE_URL']}")
        return None, server

    if args.erp == "inprocess":
        from erp_service.api import create_app as create_erp_app

        erp_app_inprocess = create_erp_app(_ERP_INPROCESS_DATABASE_URL)
        transport = httpx.ASGITransport(app=erp_app_inprocess)
        print(f"[run_plans] ERP service in-process via httpx.ASGITransport ({_ERP_INPROCESS_DATABASE_URL})")
        return transport, None

    # --erp http (default): a real server must already be reachable at this address.
    erp_base_url = args.erp_url or os.environ.get("ERP_BASE_URL", _ERP_DEFAULT_URL)
    os.environ["ERP_BASE_URL"] = erp_base_url
    _check_erp_reachable(erp_base_url)
    print(f"[run_plans] using ERP over HTTP at {erp_base_url}")
    return None, None


def _fmt_usd(value: float) -> str:
    return f"${value:.6f}"


# --------------------------------------------------------------------------------------
# Legacy mode (unchanged behavior: explicit "plan", two executions, no result file)
# --------------------------------------------------------------------------------------

def _plan_requests() -> list[dict]:
    """The two reference requests: one with a user principal, one without (becomes SystemPrincipal)."""
    return [
        {
            "plan": "schedule-appointment",
            "payload": {"customer_id": "customer-42", "slot": "2026-08-03T10:00"},
            "principal": Principal(id="user-42", type="user", scopes=("scheduling:write", "whatsapp:send")),
        },
        {
            "plan": "inbound-email-to-erp",
            "payload": {"email_id": "email-1001"},
            # no "principal": the Orchestrator issues a SystemPrincipal with the plan's entry_scopes.
        },
    ]


def _print_execution(plan_id: str, result, summary: dict) -> None:
    print(f"\n=== plan: {plan_id} ===")
    print(f"  execution_id : {result.execution_id}")
    print(f"  status       : {result.status}")
    if result.rejection is not None:
        print(f"  rejection    : {result.rejection.code} — {result.rejection.reason}")
    print(f"  output       : {result.output}")
    print(f"  model calls        : {summary['model_calls']}")
    print(f"  tokens in/out      : {summary['input_tokens']}/{summary['output_tokens']}")
    print(f"  cache_read_tokens  : {summary['cache_read_tokens']}")
    print(f"  cost (USD)         : {_fmt_usd(summary['cost_usd'])}")
    if summary["step_latency_ms"]:
        print("  latency per step (ms):")
        for step_id, latency in summary["step_latency_ms"].items():
            print(f"    - {step_id:<20} {latency:.1f}")


def _run_legacy_mode(args) -> int:
    erp_transport, server = _setup_erp(args)
    platform = build_platform(provider=args.provider, erp_transport=erp_transport)
    print(f"[run_plans] provider={args.provider or os.environ.get('AI_PROVIDER', 'local')}")

    results = []
    for request in _plan_requests():
        result = platform.orchestrator.handle(request)
        summary = platform.console_obs.summary(result.execution_id)
        _print_execution(request["plan"], result, summary)
        results.append((request["plan"], result, summary))

    completed = [r for _pid, r, _s in results if r.status == "completed"]
    total_cost = sum(s["cost_usd"] for _pid, _r, s in results)
    total_model_calls = sum(s["model_calls"] for _pid, _r, s in results)
    total_input = sum(s["input_tokens"] for _pid, _r, s in results)
    total_output = sum(s["output_tokens"] for _pid, _r, s in results)

    print("\n=== summary ===")
    print(f"  executions           : {len(results)}")
    print(f"  completed            : {len(completed)}")
    print(f"  model calls          : {total_model_calls}")
    print(f"  total tokens in/out  : {total_input}/{total_output}")
    print(f"  total cost (USD)     : {_fmt_usd(total_cost)}")
    if completed:
        cost_per_completed = total_cost / len(completed)
        print(f"  cost per completed task (USD) : {_fmt_usd(cost_per_completed)}")
    else:
        print("  cost per completed task (USD) : n/a (no execution completed)")

    platform.connectors["rest"].close()
    if server is not None:
        server.should_exit = True

    return 0 if len(completed) == len(results) else 1


# --------------------------------------------------------------------------------------
# Measurement mode (--repeat): model-driven plan/capability selection, shared platform,
# per-execution + aggregate metrics, JSON report.
# --------------------------------------------------------------------------------------

def _intent_requests(repeat_index: int) -> list[dict]:
    """Natural-language intents for one repeat round; the model must pick the plan (no explicit "plan").

    Payload values are varied by `repeat_index` so repeated writes against the ERP service (unique
    appointment slot, distinct email id) do not collide with earlier rounds within the same
    shared-platform run. Note this only guarantees no collision *within* one run: erp_service now
    persists real state (SQLite, see `--erp`), so two separate invocations of this script sharing
    the same ERP instance/file can still collide across runs -- see NOTES.md.
    """
    customer_id = f"customer-{repeat_index + 1}"
    slot = f"2026-09-01T{10 + (repeat_index % 10):02d}:00"
    email_id = f"email-{repeat_index + 1}"
    return [
        {
            "request_label": "schedule-appointment",
            "intent": f"book an appointment for customer {customer_id} at slot {slot}",
            "payload": {"customer_id": customer_id, "slot": slot},
        },
        {
            "request_label": "inbound-email-to-erp",
            "intent": f"process inbound email {email_id} received from the customer",
            "payload": {"email_id": email_id},
        },
    ]


def _call_cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    """Per-call cost from a single model_log entry (tokens x pricing table); mirrors console_writer's formula."""
    prices = PRICING_USD_PER_MILLION_TOKENS.get(model, {"input": 0.0, "output": 0.0})
    billable_input = max(input_tokens - cache_read_tokens, 0)
    cost = billable_input / 1_000_000 * prices["input"]
    cost += output_tokens / 1_000_000 * prices["output"]
    cost += cache_read_tokens / 1_000_000 * prices["input"] * _CACHE_READ_DISCOUNT
    return cost


def _extract_offered_ids(prompt: str) -> set[str]:
    return set(_CATALOG_ID_RE.findall(prompt or ""))


def _is_selection_violation(entry: dict) -> bool:
    """purpose=="selection": violation unless content is exactly an id offered in the prompt, or "none"."""
    content = (entry.get("content") or "").strip()
    if content == "none":
        return False
    return content not in _extract_offered_ids(entry.get("prompt", ""))


def _is_generation_violation(entry: dict) -> bool:
    """purpose=="generation": violation unless content parses as a JSON object with "request_type" and "summary".

    Matches what `CorrespondenceAgent/capabilities/ReadAndReply/capability.py::_parse` actually
    requires downstream: it `json.loads`s the content and, only on success, reads
    `data.get("request_type", ...)` / `data.get("summary", ...)` from the resulting dict — any
    other shape silently falls back to a canned dict built from the raw text. The prototype has
    no output_contract/retry layer, so that silent fallback is exactly what we are measuring here.
    """
    content = entry.get("content") or ""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return True
    if not isinstance(data, dict):
        return True
    return not ({"request_type", "summary"} <= data.keys())


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, stdev


def _selections_for_execution(audit_events: list[dict], execution_id: str) -> tuple[str | None, list[dict]]:
    """Reads the audit trail for one execution: the plan-selection choice and the ordered
    capability-selection choices (one per agent invocation; step_id is hardcoded to "selection"
    in `ai/agents/runtime.py`, so agent invocations are told apart only by their order of
    occurrence — which matches the order the orchestrator executed the plan's steps, plus any
    compensation invocation appended at the end).
    """
    plan_chosen: str | None = None
    capability_selections: list[dict] = []
    for event in audit_events:
        if event.get("execution_id") != execution_id or event.get("kind") != "proposal":
            continue
        step_id = event.get("step_id")
        detail = event.get("detail") or {}
        if step_id == "plan-selection":
            plan_chosen = detail.get("chosen")
        elif step_id == "selection":
            capability_selections.append({"chosen": detail.get("chosen"), "options": detail.get("options", [])})
    return plan_chosen, capability_selections


def _run_one_execution(platform, repeat_index: int, request_spec: dict, global_index: int) -> dict:
    request = {"intent": request_spec["intent"], "payload": request_spec["payload"]}
    log_start = len(platform.model_log)
    started = time.monotonic()
    result = platform.orchestrator.handle(request)
    latency_ms_total = (time.monotonic() - started) * 1000
    log_end = len(platform.model_log)
    call_log = list(platform.model_log[log_start:log_end])
    summary = platform.console_obs.summary(result.execution_id)

    calls_by_purpose: Counter = Counter(entry.get("purpose", "") for entry in call_log)
    cost_by_purpose: Counter = Counter()
    violation_details: list[dict] = []
    selection_violations = 0
    generation_violations = 0
    for entry in call_log:
        purpose = entry.get("purpose", "")
        cost = _call_cost_usd(
            entry.get("model", ""), entry.get("input_tokens", 0),
            entry.get("output_tokens", 0), entry.get("cache_read_tokens", 0),
        )
        cost_by_purpose[purpose] += cost
        if purpose == "selection" and _is_selection_violation(entry):
            selection_violations += 1
            violation_details.append({"purpose": "selection", "content": (entry.get("content") or "")[:200]})
        elif purpose == "generation" and _is_generation_violation(entry):
            generation_violations += 1
            violation_details.append({"purpose": "generation", "content": (entry.get("content") or "")[:200]})

    rejection = None
    if result.rejection is not None:
        rejection = {"code": result.rejection.code, "reason": result.rejection.reason, "scope": result.rejection.scope}

    return {
        "index": global_index,
        "repeat_round": repeat_index + 1,
        "request_label": request_spec["request_label"],
        "intent": request_spec["intent"],
        "execution_id": result.execution_id,
        "status": result.status,
        "rejection": rejection,
        "output": result.output,
        "tokens_in": summary["input_tokens"],
        "tokens_out": summary["output_tokens"],
        "cache_read_tokens": summary["cache_read_tokens"],
        "model_calls": summary["model_calls"],
        "model_calls_by_purpose": dict(calls_by_purpose),
        "cost_usd": summary["cost_usd"],
        "cost_usd_by_purpose": {k: round(v, 8) for k, v in cost_by_purpose.items()},
        "latency_ms_total": latency_ms_total,
        "latency_ms_per_step": summary["step_latency_ms"],
        "selection_violations": selection_violations,
        "generation_violations": generation_violations,
        "violation_details": violation_details,
        "models_seen": sorted({entry.get("model", "") for entry in call_log if entry.get("model")}),
    }


def _output_contract_counts_for_execution(audit_events: list[dict], execution_id: str) -> dict:
    """Reads the audit trail for one execution: counts of `kind=="output_contract"` events by
    `detail["action"]`, emitted by `ai/agents/correspondence/read_and_reply.py`'s
    `retry_once_then_fail` (a "violation" is logged on the first non-conforming response, a
    "retry" right after when the same call is repeated, and a "failed" if the retry also violates
    the schema -- at which point `OutputContractViolation` is raised and the step fails)."""
    violations = retries = failures = 0
    for event in audit_events:
        if event.get("execution_id") != execution_id or event.get("kind") != "output_contract":
            continue
        action = (event.get("detail") or {}).get("action")
        if action == "violation":
            violations += 1
        elif action == "retry":
            retries += 1
        elif action == "failed":
            failures += 1
    return {
        "contract_violations": violations,
        "contract_retries": retries,
        "contract_failures": failures,
    }


def _attach_output_contract_data(executions: list[dict], audit_events: list[dict]) -> None:
    """Fills in contract_violations/contract_retries/contract_failures for every execution."""
    for execution in executions:
        execution.update(_output_contract_counts_for_execution(audit_events, execution["execution_id"]))


def _attach_selection_data(executions: list[dict], audit_events: list[dict]) -> None:
    """Fills in `resolved_plan` / `capability_selections` for every execution from the audit trail."""
    for execution in executions:
        plan_chosen, capability_selections = _selections_for_execution(audit_events, execution["execution_id"])
        execution["resolved_plan"] = plan_chosen
        execution["capability_selections"] = [
            {"order": idx, "chosen": sel["chosen"], "options": sel["options"]}
            for idx, sel in enumerate(capability_selections)
        ]


def _selected_capability_label(execution: dict, plans: dict) -> str:
    """"step:capability" per agent invocation, joined by ",": empty if none, unjoined if just one.

    `capability_selections[i]["order"]` is the invocation order (see `_selections_for_execution`),
    which lines up with `plan.steps[i].id` for a normal completion; any selection beyond
    `len(plan.steps)` (e.g. a compensation invocation appended at the end) falls back to a
    positional label since it has no corresponding declared plan step.
    """
    selections = execution["capability_selections"]
    if not selections:
        return ""
    plan = plans.get(execution["resolved_plan"])
    plan_step_ids = [s.id for s in plan.steps] if plan is not None else []
    parts = []
    for sel in selections:
        idx = sel["order"]
        step_label = plan_step_ids[idx] if idx < len(plan_step_ids) else f"step{idx}"
        parts.append(f"{step_label}:{sel['chosen']}")
    return ",".join(parts)


def _finalize_langsmith_traces(platform, executions: list[dict], provider: str) -> None:
    """Closes each execution's LangSmith root trace with filterable tags (no-op if disabled)."""
    for execution in executions:
        platform.langsmith_obs.finalize_execution(execution["execution_id"], {
            "plan_id": execution["resolved_plan"] or "",
            "selected_capability": _selected_capability_label(execution, platform.plans),
            "provider": provider,
            "run_index": execution["index"],
            "output_contract_violation": bool(
                execution["selection_violations"] or execution["generation_violations"]
            ),
        })


def _aggregate_group(executions: list[dict]) -> dict:
    n = len(executions)
    completed = [e for e in executions if e["status"] == "completed"]

    def _series(key: str) -> list[float]:
        return [e[key] for e in executions]

    tokens_in_mean, tokens_in_std = _mean_std(_series("tokens_in"))
    tokens_out_mean, tokens_out_std = _mean_std(_series("tokens_out"))
    calls_mean, calls_std = _mean_std([e["model_calls"] for e in executions])
    latency_mean, latency_std = _mean_std(_series("latency_ms_total"))
    cost_mean, cost_std = _mean_std(_series("cost_usd"))

    plan_distribution: Counter = Counter(e["resolved_plan"] for e in executions)
    capability_distribution: Counter = Counter()
    for e in executions:
        for sel in e["capability_selections"]:
            capability_distribution[sel["chosen"]] += 1

    total_selection_violations = sum(e["selection_violations"] for e in executions)
    total_generation_violations = sum(e["generation_violations"] for e in executions)
    failed_with_selection_violation = sum(
        1 for e in executions if e["status"] != "completed" and e["selection_violations"] > 0
    )
    fallback_absorbed_generation_violation = sum(
        1 for e in executions if e["status"] == "completed" and e["generation_violations"] > 0
    )

    total_contract_violations = sum(e["contract_violations"] for e in executions)
    total_contract_retries = sum(e["contract_retries"] for e in executions)
    total_contract_failures = sum(e["contract_failures"] for e in executions)
    executions_with_contract_retry = sum(1 for e in executions if e["contract_retries"] > 0)
    executions_with_contract_failure = sum(1 for e in executions if e["contract_failures"] > 0)

    selection_cost = sum(e["cost_usd_by_purpose"].get("selection", 0.0) for e in executions)
    generation_cost = sum(e["cost_usd_by_purpose"].get("generation", 0.0) for e in executions)
    total_measured_cost = selection_cost + generation_cost

    return {
        "n": n,
        "completed": len(completed),
        "success_rate": (len(completed) / n) if n else 0.0,
        "tokens_in": {"mean": tokens_in_mean, "stdev": tokens_in_std},
        "tokens_out": {"mean": tokens_out_mean, "stdev": tokens_out_std},
        "model_calls": {"mean": calls_mean, "stdev": calls_std},
        "latency_ms_total": {"mean": latency_mean, "stdev": latency_std},
        "cost_usd": {"mean": cost_mean, "stdev": cost_std},
        "selection_distribution": {
            "resolved_plan": dict(plan_distribution),
            "capability": dict(capability_distribution),
        },
        "violations": {
            "selection_violations": total_selection_violations,
            "generation_violations": total_generation_violations,
            "executions_failed_with_selection_violation": failed_with_selection_violation,
            "executions_fallback_absorbed_generation_violation": fallback_absorbed_generation_violation,
        },
        "output_contract": {
            "contract_violations": total_contract_violations,
            "contract_retries": total_contract_retries,
            "contract_failures": total_contract_failures,
            "executions_with_contract_retry": executions_with_contract_retry,
            "executions_with_contract_failure": executions_with_contract_failure,
        },
        "cost_split": {
            "selection_usd": round(selection_cost, 8),
            "generation_usd": round(generation_cost, 8),
            "selection_fraction": (selection_cost / total_measured_cost) if total_measured_cost else 0.0,
        },
    }


def _print_execution_row(execution: dict) -> None:
    caps = ",".join(sel["chosen"] or "?" for sel in execution["capability_selections"]) or "-"
    calls = execution["model_calls_by_purpose"]
    calls_str = f"sel={calls.get('selection', 0)}/gen={calls.get('generation', 0)}"
    print(
        f"  [{execution['index']:>3}] round={execution['repeat_round']:<3} "
        f"request={execution['request_label']:<22} resolved={str(execution['resolved_plan']):<22} "
        f"capability={caps:<20} status={execution['status']:<12} "
        f"calls({calls_str}) tokens={execution['tokens_in']}/{execution['tokens_out']} "
        f"cache={execution['cache_read_tokens']} cost={_fmt_usd(execution['cost_usd'])} "
        f"latency_ms={execution['latency_ms_total']:.1f} "
        f"violations(sel={execution['selection_violations']},gen={execution['generation_violations']}) "
        f"contract(viol={execution['contract_violations']},retry={execution['contract_retries']},"
        f"fail={execution['contract_failures']})"
    )


def _print_aggregate_table(aggregate: dict) -> None:
    for label, agg in aggregate.items():
        print(f"\n--- aggregate: {label} ---")
        print(f"  n                    : {agg['n']}")
        print(f"  success rate         : {agg['completed']}/{agg['n']} ({agg['success_rate']:.1%})")
        print(f"  tokens in            : mean={agg['tokens_in']['mean']:.1f} stdev={agg['tokens_in']['stdev']:.1f}")
        print(f"  tokens out           : mean={agg['tokens_out']['mean']:.1f} stdev={agg['tokens_out']['stdev']:.1f}")
        print(f"  model calls          : mean={agg['model_calls']['mean']:.2f} stdev={agg['model_calls']['stdev']:.2f}")
        print(f"  latency total (ms)   : mean={agg['latency_ms_total']['mean']:.1f} stdev={agg['latency_ms_total']['stdev']:.1f}")
        print(f"  cost (USD)           : mean={_fmt_usd(agg['cost_usd']['mean'])} stdev={_fmt_usd(agg['cost_usd']['stdev'])}")
        print(f"  selection: plan      : {agg['selection_distribution']['resolved_plan']}")
        print(f"  selection: capability: {agg['selection_distribution']['capability']}")
        v = agg["violations"]
        print(
            f"  violations           : selection={v['selection_violations']} generation={v['generation_violations']} "
            f"failed_via_selection={v['executions_failed_with_selection_violation']} "
            f"fallback_absorbed_generation={v['executions_fallback_absorbed_generation_violation']}"
        )
        oc = agg["output_contract"]
        print(
            f"  output_contract      : violations={oc['contract_violations']} retries={oc['contract_retries']} "
            f"failures={oc['contract_failures']} executions_with_retry={oc['executions_with_contract_retry']} "
            f"executions_with_2nd_failure={oc['executions_with_contract_failure']}"
        )
        cs = agg["cost_split"]
        print(
            f"  cost split           : selection={_fmt_usd(cs['selection_usd'])} "
            f"generation={_fmt_usd(cs['generation_usd'])} selection_fraction={cs['selection_fraction']:.1%}"
        )


def _run_measurement_mode(args) -> int:
    repeat = args.repeat
    if repeat < 1:
        print(f"[run_plans] --repeat must be >= 1 (got {repeat})", file=sys.stderr)
        return 2

    erp_transport, server = _setup_erp(args)
    timestamp = datetime.now()
    run_tag = timestamp.strftime("%Y%m%d-%H%M%S")
    audit_path = _VAR_DIR / f"audit-run-{run_tag}.jsonl"

    platform = build_platform(
        provider=args.provider, erp_transport=erp_transport, audit_path=audit_path, record_model_calls=True,
        inject_generation_fault=args.inject_generation_fault,
    )
    provider = args.provider or os.environ.get("AI_PROVIDER", "local")
    fault_note = " inject_generation_fault=True" if args.inject_generation_fault else ""
    print(f"[run_plans] provider={provider} repeat={repeat} audit_path={audit_path}{fault_note}")

    executions: list[dict] = []
    global_index = 0
    for repeat_index in range(repeat):
        for request_spec in _intent_requests(repeat_index):
            execution = _run_one_execution(platform, repeat_index, request_spec, global_index)
            executions.append(execution)
            global_index += 1

    # Read the audit trail once, after all executions, and slice it per execution_id — cheaper
    # than re-reading the (monotonically growing) JSONL file after every single execution.
    audit_events = list(platform.audit.read_all())
    _attach_selection_data(executions, audit_events)
    _attach_output_contract_data(executions, audit_events)
    _finalize_langsmith_traces(platform, executions, provider)

    for execution in executions:
        _print_execution_row(execution)

    grouped: dict[str, list[dict]] = {}
    for execution in executions:
        grouped.setdefault(execution["request_label"], []).append(execution)
    aggregate = {label: _aggregate_group(group) for label, group in grouped.items()}
    aggregate["overall"] = _aggregate_group(executions)

    _print_aggregate_table(aggregate)

    model_names = sorted({m for e in executions for m in e["models_seen"]})
    metadata = {
        "provider": provider,
        "model": model_names[0] if len(model_names) == 1 else model_names,
        "repeat": repeat,
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "fault_injection": args.inject_generation_fault,
    }
    report = {"metadata": metadata, "per_execution": executions, "aggregate": aggregate}

    runs_dir = _RESULTS_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    result_path = runs_dir / f"run-{run_tag}.json"
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n[run_plans] wrote {result_path}")

    platform.connectors["rest"].close()
    if server is not None:
        server.should_exit = True

    overall = aggregate["overall"]
    return 0 if overall["completed"] == overall["n"] else 1


# --------------------------------------------------------------------------------------
# Integrity matrix mode (--matrix-cell): one experiment, same codebase, ONLY build_platform()
# instrumentation flags vary per cell -- see infrastructure/configurations.py's
# strip_response_schema / inject_generation_fault / tolerant_repair, and
# infrastructure/providers/{schema_stripping,fault_injection,tolerant_repair}_client.py for what
# each flag actually does. Reproduces, as controlled conditions, the accident and the fix
# documented in NOTES.md ("Structured output correction").
#
# Cells:
#   A: strip_response_schema + inject_generation_fault + tolerant_repair -- enforcement=none,
#      a corrupted response, and the historical tolerant fallback repairing it well enough for
#      the strict guard to accept the garbage. Inbound plan only.
#   B: strip_response_schema + inject_generation_fault, no repair -- the strict guard alone,
#      unaided, against exactly the failure class it exists to catch. Inbound plan only.
#   C: real schema, no injected fault, tolerant_repair still wired (repair should be a no-op
#      against already-conforming provider output). Inbound plan only.
#   D: real schema, no injector, no repair -- today's corrected/production-shaped
#      configuration. Inbound plan only.
#   control: same configuration as D, but restricted to schedule-appointment only (no
#      generation step at all) -- a pipeline sanity arm, not a cell under study.
#   E: real schema + inject_generation_fault + tolerant_repair -- post-decoding corruption
#      (the injected fence wraps schema-valid provider output) against the tolerant path.
#      Added 2026-08-06 per docs/preregistration-schema-fault-cells.md (committed BEFORE this
#      change, per the repo's pre-registration discipline). Inbound plan only.
#   F: real schema + inject_generation_fault, no repair -- the same post-decoding corruption
#      against the strict guard; the injector re-fences the retry too, so this cell verifies
#      the guard's coded behavior (typed failure, nothing persisted) under real-model
#      conditions. Same pre-registration as E. Inbound plan only.
#
# Mechanics:
#   - Each cell is invoked separately (one `--matrix-cell X --repeat N` call per cell) against
#     its own isolated ERP SQLite (var/erp/matrix-<cell>.sqlite): no cross-cell interference,
#     no cross-cell state.
#   - Resume: every completed rep is appended to results/integrity-matrix/integrity_matrix.jsonl immediately (not
#     batched at the end); the next invocation for the same cell loads that file, skips
#     (cell, rep) pairs already on record, and continues from the first missing rep. The cell's
#     ERP SQLite file is only ever zeroed (deleted, so create_app starts it fresh) the very first
#     time a cell has zero recorded reps -- once at least one rep is on record, the file is
#     resume state and is never touched by this script again.
#   - After the reps requested by this invocation are done (or skipped because already done),
#     the cell's block of results/integrity-matrix/integrity_matrix_report.json is (re)written from ALL recorded
#     reps for that cell (this invocation's plus any earlier invocation's), merged in with
#     whatever other cells' blocks are already in that file -- so cells can be run in any order,
#     interleaved across separate invocations, without clobbering each other.
#   - LangSmith: matrix mode never calls `finalize_execution` and never sets `LANGSMITH_API_KEY`
#     itself. `LangSmithObservabilityWriter` already no-ops entirely whenever that env var is
#     unset (checked at construction and re-checked by every public method -- see its own module
#     docstring), so simply not exporting it is the entire mechanism; nothing else is skipped or
#     special-cased for matrix runs.
#   - Cross-model runs: the original matrix ran on `_MATRIX_BASELINE_MODEL`
#     ("gemini-3.1-flash-lite"). Any other resolved model (see `_matrix_effective_model_id`) gets
#     every evidence path suffixed with a slug of its model id (see `_matrix_evidence_paths` /
#     `_matrix_model_slug`): `integrity_matrix-<slug>.jsonl`, `integrity_matrix_report-<slug>.json`,
#     `var/erp/matrix-<cell>-<slug>.sqlite`, `audit-matrix-<cell>-<slug>-<ts>.jsonl` -- landing
#     beside the baseline evidence rather than resuming into or overwriting it. The baseline model
#     itself is unaffected: its paths stay exactly as they always were.
#   - Postgres ERP (optional, env `MATRIX_ERP_DATABASE_URL`): unset -- everything above is
#     unchanged (per-cell SQLite, as always). Set to a server URL (e.g.
#     `postgresql+psycopg://erp:erp@127.0.0.1:5433/erp`, matching the `postgres-erp` compose
#     service): each (cell, model) pair gets isolated onto its own *database* on that server
#     instead of its own SQLite file -- `matrix_<cell>` plus `_<model-slug>` for any non-baseline
#     model, sanitized to `[a-z0-9_]` (see `_matrix_pg_db_name`) -- created fresh (DROP DATABASE IF
#     EXISTS + CREATE DATABASE) at the same "cell has zero recorded reps" moment the SQLite branch
#     deletes its file, and never dropped again once reps are on record. Every evidence path
#     additionally gains a `-pg` suffix layered onto the scheme above (see `_matrix_evidence_paths`),
#     so Postgres-backed evidence always lands beside, never over, SQLite-backed evidence for the
#     same (cell, model). After a cell's reps complete, its live Postgres tables are exported into a
#     SQLite snapshot at `var/erp/matrix-<cell>[-slug]-pg.sqlite` (see `_matrix_export_pg_snapshot`)
#     so the tracked-evidence format stays uniform regardless of which engine actually ran the cell.
# --------------------------------------------------------------------------------------

_MATRIX_CELLS = ("A", "B", "C", "D", "control", "E", "F")

_MATRIX_CELL_CONFIGS: dict[str, dict] = {
    "A": {"strip_response_schema": True, "inject_generation_fault": True, "tolerant_repair": True},
    "B": {"strip_response_schema": True, "inject_generation_fault": True, "tolerant_repair": False},
    "C": {"strip_response_schema": False, "inject_generation_fault": False, "tolerant_repair": True},
    "D": {"strip_response_schema": False, "inject_generation_fault": False, "tolerant_repair": False},
    "control": {"strip_response_schema": False, "inject_generation_fault": False, "tolerant_repair": False},
    "E": {"strip_response_schema": False, "inject_generation_fault": True, "tolerant_repair": True},
    "F": {"strip_response_schema": False, "inject_generation_fault": True, "tolerant_repair": False},
}

_MATRIX_DEFAULT_REPS: dict[str, int] = {"A": 10, "B": 10, "C": 10, "D": 10, "control": 3, "E": 10, "F": 10}

_MATRIX_DIR = _RESULTS_DIR / "integrity-matrix"
_MATRIX_JSONL_PATH = _MATRIX_DIR / "integrity_matrix.jsonl"
_MATRIX_REPORT_PATH = _MATRIX_DIR / "integrity_matrix_report.json"
_MATRIX_ERP_DIR = _VAR_DIR / "erp"

# An execution's total latency above this threshold is counted as a "stall" in the per-cell report.
_MATRIX_STALL_THRESHOLD_MS = 30_000.0

# The model the original matrix ran on. When the effective model resolves to exactly this, every
# evidence path below stays exactly as it already was (byte-identical to the pre-cross-model
# behavior). Any other model gets its own, model-suffixed set of paths -- see
# `_matrix_evidence_paths` -- so a cross-model run lands beside the original without ever
# resuming into (or overwriting) its records.
_MATRIX_BASELINE_MODEL = "gemini-3.1-flash-lite"

# Mirrors each provider client's own model resolution (env var override, else its module-level
# default -- see infrastructure/providers/{anthropic,openai,gemini,meta,local}_client.py) so the
# matrix runner can learn "which model is this run actually going to use" up front, before
# `build_platform` ever instantiates a real client (which would require live API credentials just
# to answer that question).
_MATRIX_PROVIDER_MODEL_ENV: dict[str, tuple[str | None, str]] = {
    "local": (None, "local-deterministic"),
    "anthropic": ("ANTHROPIC_MODEL", "claude-sonnet-5"),
    "openai": ("OPENAI_MODEL", "gpt-4o-mini"),
    "gemini": ("GEMINI_MODEL", _MATRIX_BASELINE_MODEL),
    "meta": ("META_MODEL", "muse-spark-1.2"),
    # No default: which family (Llama, Qwen, Claude, ...) answers via OpenRouter is itself an
    # experimental variable this repository pre-registers per experiment -- see
    # OpenRouterModelClient.__init__ and _matrix_effective_model_id below, which turns an
    # unresolved (empty-string) model into a loud SystemExit rather than letting a matrix run
    # silently proceed without knowing its own model for evidence-path purposes.
    "openrouter": ("OPENROUTER_MODEL", ""),
    # Same no-default rule as openrouter, for the same reason -- see GroqModelClient.__init__.
    "groq": ("GROQ_MODEL", ""),
}


def _matrix_effective_model_id(provider: str | None) -> str:
    """Resolves the model id `provider` will actually run with, the same way the platform itself
    resolves it: `build_platform`'s own provider fallback (explicit arg, else env `AI_PROVIDER`,
    else "local"), then that provider client's own model fallback (its env var override, else its
    module-level default)."""
    provider = provider or os.environ.get("AI_PROVIDER", "local")
    env_var, default = _MATRIX_PROVIDER_MODEL_ENV[provider]
    resolved = os.environ.get(env_var, default) if env_var else default
    if not resolved:
        raise SystemExit(
            f"[run_plans] matrix mode cannot resolve a model id for provider={provider!r}: "
            f"set env {env_var} first. Matrix runs must know the model up front (it drives "
            "evidence file paths); an unset OpenRouter model, in particular, is never defaulted "
            "-- see OpenRouterModelClient.__init__."
        )
    return resolved


def _matrix_model_slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", model_id.lower())


def _matrix_evidence_paths(model_id: str, pg: bool = False) -> dict:
    """The JSONL/report paths this run's evidence should land at, plus the slug (or None) used to
    derive them. `model_id == _MATRIX_BASELINE_MODEL`: unchanged baseline paths -- full backward
    compatibility. Any other `model_id`: every path suffixed `-{slug}`, landing beside the
    baseline evidence rather than overwriting or resuming into it. `pg=True` (Postgres ERP active
    -- env `MATRIX_ERP_DATABASE_URL`, see the "Postgres ERP" mechanics bullet above) layers a
    further `-pg` suffix onto whichever of the two path schemes above applies, so Postgres-backed
    evidence never resumes into or overwrites SQLite-backed evidence for the same model. Default
    `pg=False`: byte-identical to before this parameter existed."""
    if model_id == _MATRIX_BASELINE_MODEL:
        jsonl, report, slug = _MATRIX_JSONL_PATH, _MATRIX_REPORT_PATH, None
    else:
        slug = _matrix_model_slug(model_id)
        jsonl = _MATRIX_DIR / f"integrity_matrix-{slug}.jsonl"
        report = _MATRIX_DIR / f"integrity_matrix_report-{slug}.json"
    if pg:
        jsonl = jsonl.with_name(f"{jsonl.stem}-pg{jsonl.suffix}")
        report = report.with_name(f"{report.stem}-pg{report.suffix}")
    return {"jsonl": jsonl, "report": report, "slug": slug}


def _matrix_erp_path(cell: str, slug: str | None = None, pg: bool = False) -> Path:
    suffix = f"-{slug}" if slug else ""
    suffix += "-pg" if pg else ""
    return _MATRIX_ERP_DIR / f"matrix-{cell}{suffix}.sqlite"


# ---- Postgres ERP helpers (optional, env MATRIX_ERP_DATABASE_URL) ---------------------
# Only reached when that env var is set; see the "Postgres ERP" mechanics bullet above for the
# scheme these implement (per-(cell, model) database isolation, freshness timing, snapshot export).

_MATRIX_PG_DB_NAME_RE = re.compile(r"[^a-z0-9_]+")


def _matrix_erp_database_url() -> str | None:
    """`MATRIX_ERP_DATABASE_URL`, or `None` when unset/empty -- the single switch between the
    SQLite-per-cell behavior (unset, default) and the Postgres-per-cell-database behavior (set,
    e.g. `postgresql+psycopg://erp:erp@127.0.0.1:5433/erp` against the compose `postgres-erp`
    service)."""
    return os.environ.get("MATRIX_ERP_DATABASE_URL") or None


def _matrix_pg_db_name(cell: str, slug: str | None) -> str:
    """Per-(cell, model) Postgres database name: `matrix_<cell>`, plus `_<slug>` for any
    non-baseline model (mirrors the SQLite path's per-model suffixing) -- e.g. cell "A" + slug
    "gpt-4o-mini" -> "matrix_a_gpt_4o_mini". Sanitized to `[a-z0-9_]`, the safe subset of Postgres
    identifier characters that never needs quoting or escaping."""
    raw = f"matrix_{cell}" + (f"_{slug}" if slug else "")
    return _MATRIX_PG_DB_NAME_RE.sub("_", raw.lower())


def _matrix_pg_url_display(url: str) -> str:
    """`url` with its password masked -- safe to print or store in a report."""
    return make_url(url).render_as_string(hide_password=True)


def _matrix_pg_cell_url(base_url: str, db_name: str) -> str:
    """`base_url` (as given in `MATRIX_ERP_DATABASE_URL`) with its database swapped for
    `db_name` -- the per-cell isolated Postgres URL handed to `create_app`."""
    return make_url(base_url).set(database=db_name).render_as_string(hide_password=False)


def _matrix_pg_ensure_fresh_database(base_url: str, db_name: str) -> None:
    """DROP DATABASE IF EXISTS + CREATE DATABASE `db_name`, against the server named by
    `base_url`, over an autocommit connection (CREATE/DROP DATABASE cannot run inside a
    transaction block). Mirrors the SQLite branch's "delete only when the cell has zero recorded
    reps" freshness rule -- callers must only invoke this the first time a (cell, model) pair has
    zero recorded reps; once at least one rep is on record for that database, this must never run
    again for it (see `_run_matrix_cell_mode`)."""
    engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


def _matrix_export_pg_snapshot(pg_url: str, snapshot_path: Path) -> None:
    """Exports this cell's live Postgres ERP tables (`service_requests`, `appointments` -- see
    `erp_service/models.py`) into a fresh SQLite snapshot file at `snapshot_path`: stdlib
    `sqlite3` only, same column names, every row, no SQLAlchemy on the write side. Keeps the
    tracked-evidence format uniform with every SQLite-backed cell (same `_inspect_matrix_erp`
    sqlite3 branch, same reader scripts) even when the live ERP for this run was Postgres."""
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            service_request_rows = conn.execute(text(
                "SELECT id, record_id, customer_email, request_type, summary, status, created_at "
                "FROM service_requests ORDER BY id"
            )).fetchall()
            appointment_rows = conn.execute(text(
                "SELECT id, appointment_id, customer_id, slot, status, previous_appointment_id, "
                "created_at FROM appointments ORDER BY id"
            )).fetchall()
    finally:
        engine.dispose()

    def _row(values) -> tuple:
        # created_at comes back as a tz-aware datetime; stringify it (sqlite3 has no native
        # datetime binding) the same way json.dump(..., default=str) would elsewhere in this file.
        return tuple(v.isoformat() if hasattr(v, "isoformat") else v for v in values)

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot_path.exists():
        snapshot_path.unlink()
    snap_conn = sqlite3.connect(snapshot_path.as_posix())
    try:
        snap_conn.execute(
            "CREATE TABLE service_requests (id INTEGER PRIMARY KEY, record_id TEXT, "
            "customer_email TEXT, request_type TEXT, summary TEXT, status TEXT, created_at TEXT)"
        )
        snap_conn.execute(
            "CREATE TABLE appointments (id INTEGER PRIMARY KEY, appointment_id TEXT, "
            "customer_id TEXT, slot TEXT, status TEXT, previous_appointment_id TEXT, "
            "created_at TEXT)"
        )
        snap_conn.executemany(
            "INSERT INTO service_requests VALUES (?, ?, ?, ?, ?, ?, ?)",
            [_row(row) for row in service_request_rows],
        )
        snap_conn.executemany(
            "INSERT INTO appointments VALUES (?, ?, ?, ?, ?, ?, ?)",
            [_row(row) for row in appointment_rows],
        )
        snap_conn.commit()
    finally:
        snap_conn.close()


def _matrix_request(cell: str, rep_index: int) -> dict:
    """The single request one (cell, rep) runs: "control" only ever books an appointment (no
    generation step -- a pipeline sanity arm); every other cell only ever processes an inbound
    email (the plan whose generation step this matrix studies). Reuses `_intent_requests` (same
    per-rep payload variation, so repeated reps don't collide on the ERP's invariants) rather
    than reimplementing request construction."""
    label = "schedule-appointment" if cell == "control" else "inbound-email-to-erp"
    by_label = {r["request_label"]: r for r in _intent_requests(rep_index)}
    return by_label[label]


def _load_matrix_records(jsonl_path: Path = _MATRIX_JSONL_PATH) -> list[dict]:
    if not jsonl_path.exists():
        return []
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _append_matrix_record(record: dict, jsonl_path: Path = _MATRIX_JSONL_PATH) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _matrix_jsonl_row(execution: dict) -> dict:
    """Projects one `_run_matrix_rep` result into the exact row shape appended to
    `integrity_matrix.jsonl`: {cell, rep, execution_id, status, tokens_in/out, cost, latency_ms,
    contract counters, repairs_count, ts, model}."""
    return {
        "cell": execution["cell"],
        "rep": execution["rep"],
        "execution_id": execution["execution_id"],
        "status": execution["status"],
        "tokens_in": execution["tokens_in"],
        "tokens_out": execution["tokens_out"],
        "cost": execution["cost_usd"],
        "latency_ms": execution["latency_ms_total"],
        "contract_violations": execution["contract_violations"],
        "contract_retries": execution["contract_retries"],
        "contract_failures": execution["contract_failures"],
        "repairs_count": execution["repairs_count"],
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": execution["models_seen"][0] if execution["models_seen"] else "",
    }


def _run_matrix_rep(platform, cell: str, rep: int, rep_index: int, global_index: int) -> dict:
    """Runs one (cell, rep): one execution of `_matrix_request(cell, rep_index)`, tagged with
    `cell`/`rep`, its selection/output-contract audit data (same shape `_attach_selection_data`/
    `_attach_output_contract_data` produce in --repeat mode, computed per-rep here instead of
    once at the end -- resume needs each rep durable on its own) and the count of repairs THIS
    execution triggered (the delta of `platform.repair_client.repairs`, which otherwise
    accumulates across the whole shared platform's lifetime -- the same before/after-length trick
    `_run_one_execution` already uses for `platform.model_log`)."""
    repair_client = platform.repair_client
    repairs_before = len(repair_client.repairs) if repair_client is not None else 0
    request_spec = _matrix_request(cell, rep_index)
    execution = _run_one_execution(platform, rep_index, request_spec, global_index)
    repairs_after = len(repair_client.repairs) if repair_client is not None else 0

    execution["cell"] = cell
    execution["rep"] = rep
    execution["repairs_count"] = repairs_after - repairs_before

    audit_events = list(platform.audit.read_all())
    execution.update(_output_contract_counts_for_execution(audit_events, execution["execution_id"]))
    plan_chosen, capability_selections = _selections_for_execution(audit_events, execution["execution_id"])
    execution["resolved_plan"] = plan_chosen
    execution["capability_selections"] = [
        {"order": idx, "chosen": sel["chosen"], "options": sel["options"]}
        for idx, sel in enumerate(capability_selections)
    ]
    return execution


def _is_garbage_summary(summary: str) -> bool:
    """Heuristic for "the strict guard accepted repaired/unenforced content, and the ERP
    persisted it as-is": a `service_requests.summary` counts as garbage if it is empty (nothing
    usable was ever extracted), contains a markdown code fence ("```", the fault injector's
    signature artifact -- see providers/fault_injection_client.py), or starts with "{" (a raw
    JSON blob leaked into what should be a short human-readable string -- exactly what
    `TolerantRepairClient`'s `content[:200]` produces when the corrupted `content` it received
    was itself already JSON-shaped text, fences and all). Deliberately narrow: tuned to the
    specific corruption shapes this matrix's cells are built to produce, not a general "is this
    valid prose" detector."""
    if not summary:
        return True
    if "```" in summary:
        return True
    if summary.startswith("{"):
        return True
    return False


def _inspect_matrix_erp(cell: str, db_path: Path | None = None, pg_url: str | None = None) -> dict:
    """Inspects this cell's ERP database -- engine-agnostic. `pg_url` given (Postgres ERP active):
    queries that live database directly over SQLAlchemy (`create_engine` + `text` SELECTs), `db_path`
    is ignored. Otherwise: direct sqlite3 (stdlib) inspection of `db_path` -- no ASGI/HTTP call, no
    SQLAlchemy: opened read-only, queried once, closed. Either way, "control" only ever books
    appointments (its request never reaches the generation step this matrix studies, so no
    `service_requests` row is ever possible), so it reports `appointments` row counts instead and
    leaves the summary-garbage fields as None (not applicable, not zero). `db_path` defaults to the
    unsuffixed (baseline) per-cell path when not given and `pg_url` is None."""
    if pg_url is not None:
        engine = create_engine(pg_url)
        try:
            with engine.connect() as conn:
                if cell == "control":
                    rows_created = conn.execute(text("SELECT COUNT(*) FROM appointments")).scalar_one()
                    return {
                        "db_path": _matrix_pg_url_display(pg_url), "exists": True, "table": "appointments",
                        "rows_created": rows_created, "rows_valid_summary": None, "rows_garbage": None,
                    }
                summaries = [row[0] or "" for row in conn.execute(text("SELECT summary FROM service_requests"))]
                garbage = sum(1 for summary in summaries if _is_garbage_summary(summary))
                return {
                    "db_path": _matrix_pg_url_display(pg_url), "exists": True, "table": "service_requests",
                    "rows_created": len(summaries),
                    "rows_valid_summary": len(summaries) - garbage,
                    "rows_garbage": garbage,
                }
        finally:
            engine.dispose()

    db_path = db_path if db_path is not None else _matrix_erp_path(cell)
    if not db_path.exists():
        return {"db_path": db_path.relative_to(_REPO_ROOT).as_posix(), "exists": False}

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        if cell == "control":
            rows_created = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
            return {
                "db_path": db_path.relative_to(_REPO_ROOT).as_posix(), "exists": True, "table": "appointments",
                "rows_created": rows_created, "rows_valid_summary": None, "rows_garbage": None,
            }
        summaries = [row[0] or "" for row in conn.execute("SELECT summary FROM service_requests")]
        garbage = sum(1 for summary in summaries if _is_garbage_summary(summary))
        return {
            "db_path": db_path.relative_to(_REPO_ROOT).as_posix(), "exists": True, "table": "service_requests",
            "rows_created": len(summaries),
            "rows_valid_summary": len(summaries) - garbage,
            "rows_garbage": garbage,
        }
    finally:
        conn.close()


def _aggregate_matrix_cell(records: list[dict]) -> dict:
    """Aggregates every JSONL row recorded so far for one cell (across however many separate,
    resumed invocations it took) into that cell's report block: status counts
    (completed/failed_clean/compensated/compensation_failed/rejected), tokens, cost, mean
    latency, stalls (latency_ms > _MATRIX_STALL_THRESHOLD_MS) and repair/output-contract totals."""
    n = len(records)
    status_counts: Counter = Counter(r["status"] for r in records)
    latencies = [r["latency_ms"] for r in records]
    stalls = [r for r in records if r["latency_ms"] > _MATRIX_STALL_THRESHOLD_MS]
    return {
        "n": n,
        "status_counts": dict(status_counts),
        "tokens_in_total": sum(r["tokens_in"] for r in records),
        "tokens_out_total": sum(r["tokens_out"] for r in records),
        "cost_usd_total": round(sum(r["cost"] for r in records), 8),
        "latency_ms_mean": statistics.mean(latencies) if latencies else 0.0,
        "stalls": {
            "threshold_ms": _MATRIX_STALL_THRESHOLD_MS,
            "count": len(stalls),
            "max_latency_ms": max((r["latency_ms"] for r in stalls), default=0.0),
        },
        "repairs_total": sum(r["repairs_count"] for r in records),
        "output_contract": {
            "contract_violations": sum(r["contract_violations"] for r in records),
            "contract_retries": sum(r["contract_retries"] for r in records),
            "contract_failures": sum(r["contract_failures"] for r in records),
        },
    }


def _load_matrix_report(report_path: Path = _MATRIX_REPORT_PATH) -> dict:
    if not report_path.exists():
        return {}
    with open(report_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_matrix_cell_report(
    cell: str, records: list[dict], report_path: Path = _MATRIX_REPORT_PATH,
    erp_path: Path | None = None, pg_url: str | None = None,
) -> dict:
    """Merges this cell's aggregate + ERP inspection into `integrity_matrix_report.json` (or, for
    a suffixed `report_path`, that model's own report file), preserving whatever other cells'
    blocks are already there (cells are run one invocation at a time, in any order -- this must
    never clobber siblings). `erp_path` defaults to the unsuffixed (baseline) per-cell path when
    not given. `pg_url` given (Postgres ERP active): inspects that live database instead of any
    SQLite file -- see `_inspect_matrix_erp`."""
    report = _load_matrix_report(report_path)
    cell_report = {"aggregate": _aggregate_matrix_cell(records), "erp": _inspect_matrix_erp(cell, erp_path, pg_url)}
    report[cell] = cell_report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    return cell_report


def _run_matrix_cell_mode(args) -> int:
    """Runs the not-yet-recorded reps of one integrity-matrix cell, then (re)writes that cell's
    block of `results/integrity-matrix/integrity_matrix_report.json`. See the "Integrity matrix mode" section
    above for the full mechanics (resume, per-cell ERP isolation, LangSmith, Postgres ERP)."""
    cell = args.matrix_cell
    reps = args.repeat
    config = _MATRIX_CELL_CONFIGS[cell]

    model_id = _matrix_effective_model_id(args.provider)
    pg_base_url = _matrix_erp_database_url()
    evidence = _matrix_evidence_paths(model_id, pg=pg_base_url is not None)
    jsonl_path, report_path, slug = evidence["jsonl"], evidence["report"], evidence["slug"]

    all_records = _load_matrix_records(jsonl_path)
    cell_records = [r for r in all_records if r.get("cell") == cell]
    done_reps = {r["rep"] for r in cell_records}
    is_first_run = not cell_records

    erp_path = pg_db_name = pg_cell_url = snapshot_path = None
    if pg_base_url is not None:
        pg_db_name = _matrix_pg_db_name(cell, slug)
        pg_cell_url = _matrix_pg_cell_url(pg_base_url, pg_db_name)
        snapshot_path = _matrix_erp_path(cell, slug, pg=True)
        erp_database_url = pg_cell_url
        erp_engine = "postgres"
        erp_display = f"db={pg_db_name} url={_matrix_pg_url_display(pg_cell_url)}"
        if is_first_run:
            # First execution ever recorded for this (cell, model): start its Postgres database
            # from scratch. Once at least one rep is on record, the database is resume state --
            # never dropped by this script again.
            _matrix_pg_ensure_fresh_database(pg_base_url, pg_db_name)
    else:
        erp_path = _matrix_erp_path(cell, slug)
        erp_database_url = f"sqlite:///{erp_path.as_posix()}"
        erp_engine = "sqlite"
        erp_display = str(erp_path)
        if is_first_run:
            # First execution ever recorded for this cell: start its ERP database from scratch.
            # Once at least one rep is on record, the file is resume state -- never touched here
            # again.
            erp_path.parent.mkdir(parents=True, exist_ok=True)
            if erp_path.exists():
                erp_path.unlink()

    print(
        f"[run_plans] matrix model={model_id} erp_engine={erp_engine} evidence: jsonl={jsonl_path} "
        f"report={report_path} erp={erp_display}"
    )

    if not is_first_run:
        pending = [r for r in range(1, reps + 1) if r not in done_reps]
        if pending:
            print(
                f"[run_plans] resuming cell {cell} at rep {pending[0]} "
                f"({len(done_reps)}/{reps} already recorded)"
            )
        else:
            print(f"[run_plans] cell {cell} already has all {reps} rep(s) recorded -- nothing to run")

    from erp_service.api import create_app as create_erp_app

    erp_app_inprocess = create_erp_app(erp_database_url)
    transport = httpx.ASGITransport(app=erp_app_inprocess)

    run_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    audit_suffix = f"-{slug}" if slug else ""
    audit_suffix += "-pg" if pg_base_url is not None else ""
    audit_path = _VAR_DIR / f"audit-matrix-{cell}{audit_suffix}-{run_tag}.jsonl"

    platform = build_platform(
        provider=args.provider, erp_transport=transport, audit_path=audit_path,
        record_model_calls=True, **config,
    )
    print(f"[run_plans] matrix cell={cell} reps={reps} config={config} erp={erp_display}")

    global_index = 0
    new_reps = 0
    for rep in range(1, reps + 1):
        if rep in done_reps:
            continue
        execution = _run_matrix_rep(platform, cell, rep, rep - 1, global_index)
        _print_execution_row(execution)
        _append_matrix_record(_matrix_jsonl_row(execution), jsonl_path)
        global_index += 1
        new_reps += 1

    platform.connectors["rest"].close()

    cell_records = [r for r in _load_matrix_records(jsonl_path) if r.get("cell") == cell]
    if pg_base_url is not None:
        _write_matrix_cell_report(cell, cell_records, report_path, pg_url=pg_cell_url)
        _matrix_export_pg_snapshot(pg_cell_url, snapshot_path)
        print(f"[run_plans] exported Postgres ERP snapshot to {snapshot_path}")
    else:
        _write_matrix_cell_report(cell, cell_records, report_path, erp_path)
    print(
        f"[run_plans] matrix cell={cell}: {new_reps} new rep(s) run this invocation, "
        f"{len(cell_records)}/{reps} total recorded; wrote {report_path}"
    )

    return 0 if len(cell_records) >= reps else 1


def _run_matrix_report_only(model_id: str | None = None) -> int:
    """Recomposes `results/integrity-matrix/integrity_matrix_report.json` from `integrity_matrix.jsonl` plus each
    cell's ERP SQLite file, for every cell with at least one recorded rep. No model calls, no ERP
    calls, no Platform built at all. `model_id` is `None` (default -- baseline paths, unchanged
    behavior) unless `--matrix-model` was given, in which case it recomposes that model's own
    suffixed report from its own suffixed JSONL instead."""
    evidence = _matrix_evidence_paths(model_id or _MATRIX_BASELINE_MODEL)
    jsonl_path, report_path, slug = evidence["jsonl"], evidence["report"], evidence["slug"]

    records = _load_matrix_records(jsonl_path)
    if not records:
        print(f"[run_plans] --matrix-report-only: no records found at {jsonl_path}", file=sys.stderr)
        return 1
    cells = sorted({r["cell"] for r in records})
    for cell in cells:
        cell_records = [r for r in records if r["cell"] == cell]
        _write_matrix_cell_report(cell, cell_records, report_path, _matrix_erp_path(cell, slug))
        print(f"[run_plans] recomposed report for cell={cell} ({len(cell_records)} record(s))")
    print(f"[run_plans] wrote {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--provider", choices=["local", "anthropic", "openai", "gemini", "meta", "openrouter", "groq"], default=None,
        help="model provider (default: env AI_PROVIDER or 'local')",
    )
    parser.add_argument(
        "--erp", choices=["http", "inprocess"], default="http",
        help=(
            "how to reach the real erp_service: 'http' (default) talks to a server already running "
            "at --erp-url/env ERP_BASE_URL/http://127.0.0.1:8123; 'inprocess' uses httpx.ASGITransport "
            "over a persistent local SQLite file (sqlite:///var/erp/erp.sqlite), no server needed"
        ),
    )
    parser.add_argument("--erp-url", default=None, help="URL of an external ERP/scheduling service already running (implies --erp http)")
    parser.add_argument("--serve-erp", action="store_true", help="brings up the real erp_service app via uvicorn on a local thread")
    parser.add_argument(
        "--repeat", type=int, nargs="?", const=1, default=None,
        help=(
            "measurement mode: repeats each of the 2 plans N times (default 1 when the flag is given "
            "with no value) against one shared platform, selecting the plan/capability via the model "
            "(no explicit 'plan') and writing results/runs/run-<timestamp>.json. Omit entirely to keep the "
            "legacy behavior: 2 explicit-plan executions, no result file."
        ),
    )
    parser.add_argument(
        "--inject-generation-fault", action="store_true",
        help=(
            "measurement instrumentation only (requires --repeat): wraps the model client in "
            "GenerationFaultInjectionClient so every generation-purpose response comes back "
            "wrapped in markdown JSON fences, reproducing on purpose the 'guard catches a "
            "provider-side envelope surprise' cell of the integrity matrix (NOTES.md, 'Structured "
            "output correction'). NEVER use outside of controlled measurement runs."
        ),
    )
    parser.add_argument(
        "--matrix-cell", choices=list(_MATRIX_CELLS), default=None,
        help=(
            "integrity-matrix mode: runs the not-yet-recorded reps of one matrix cell against a "
            "per-cell isolated ERP SQLite (var/erp/matrix-<cell>.sqlite), appending to "
            "results/integrity-matrix/integrity_matrix.jsonl and (re)writing that cell's block of "
            "results/integrity-matrix/integrity_matrix_report.json. A/B/C/D vary strip_response_schema / "
            "inject_generation_fault / tolerant_repair (see infrastructure/configurations.py); "
            "'control' is cell D restricted to schedule-appointment only (no generation step). "
            f"Requires --repeat (typical: {_MATRIX_DEFAULT_REPS['A']} for A/B/C/D, "
            f"{_MATRIX_DEFAULT_REPS['control']} for control). Already-recorded (cell, rep) pairs "
            "are skipped -- safe to stop and resume."
        ),
    )
    parser.add_argument(
        "--matrix-report-only", action="store_true",
        help=(
            "recomposes results/integrity-matrix/integrity_matrix_report.json from results/integrity-matrix/integrity_matrix.jsonl "
            "plus each cell's ERP SQLite file, for every cell with at least one recorded rep. No "
            "model calls, no ERP calls; takes no other matrix/measurement flag (except "
            "--matrix-model)."
        ),
    )
    parser.add_argument(
        "--matrix-model", default=None,
        help=(
            "only with --matrix-report-only: recomposes the model-suffixed report instead of the "
            f"baseline one -- reads results/integrity-matrix/integrity_matrix-<slug>.jsonl and writes "
            "results/integrity-matrix/integrity_matrix_report-<slug>.json, where <slug> is this model id "
            "lowercased with runs of non [a-z0-9.-] characters collapsed to '-'. Omit to recompose "
            f"the baseline ({_MATRIX_BASELINE_MODEL}) report -- default, unchanged behavior."
        ),
    )
    args = parser.parse_args()

    if args.matrix_report_only:
        if args.matrix_cell is not None or args.repeat is not None or args.inject_generation_fault:
            parser.error(
                "--matrix-report-only stands alone: it takes no --matrix-cell/--repeat/"
                "--inject-generation-fault"
            )
        return _run_matrix_report_only(args.matrix_model)

    if args.matrix_model is not None:
        parser.error("--matrix-model only applies together with --matrix-report-only")

    if args.matrix_cell is not None:
        if args.repeat is None:
            parser.error(
                f"--matrix-cell requires --repeat (typical: {_MATRIX_DEFAULT_REPS['A']} for "
                f"A/B/C/D, {_MATRIX_DEFAULT_REPS['control']} for control)"
            )
        if args.erp_url or args.serve_erp:
            parser.error(
                "--matrix-cell manages its own per-cell in-process ERP; --erp-url/--serve-erp do not apply"
            )
        if args.inject_generation_fault:
            parser.error(
                "--matrix-cell already controls inject_generation_fault via the cell's own "
                "configuration; do not pass --inject-generation-fault separately"
            )
        return _run_matrix_cell_mode(args)

    if args.erp_url and args.serve_erp:
        parser.error("--erp-url and --serve-erp are mutually exclusive")
    if args.serve_erp and args.erp == "inprocess":
        parser.error("--serve-erp and --erp inprocess are mutually exclusive")
    if args.erp_url and args.erp == "inprocess":
        parser.error("--erp-url only applies to --erp http")
    if args.inject_generation_fault and args.repeat is None:
        parser.error("--inject-generation-fault requires --repeat (measurement mode only)")

    if args.repeat is None:
        return _run_legacy_mode(args)
    return _run_measurement_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
