#!/usr/bin/env python
"""Selection-layer sweep: plan-selection accuracy vs. catalog size (N).

Motivation: DACS (arXiv 2604.07911) reports flat-context
orchestrator steering accuracy degrading from 60.0% to 21.0% as agent count N and diversity D
grow. This study's own measurement so far (scripts/run_plans.py) lives at the easy extreme of
that curve -- 2 plans, near-zero ambiguity. This script is the large-catalog counterpart: it
exercises ONLY the selection layer (one model call per data point, no plan execution, no tools,
no generation step) so it stays cheap enough to run against a free-tier LLM.

Design (implemented exactly per the experiment spec):
  1. A fixed, hardcoded, deterministic seed bank of 40 synthetic plans (SEED_BANK below),
     grouped into "confusable" clusters of 2-4 near-duplicate plans (e.g. schedule-appointment /
     reschedule-appointment / cancel-appointment / confirm-appointment) spanning 15 business
     domains. The list order round-robins across clusters in growing "rounds" so that small
     catalogs (N=2, N=5) contain few or no complete confusable clusters, while large catalogs
     (N=20, N=40) accumulate many -- diversity grows with N, matching the DACS setup this
     experiment answers to.
  2. Hardcoded intents:
       - one "clear" paraphrase per plan (CLEAR_INTENTS): natural-language description of the
         customer's situation that avoids the plan's id words and trigger phrases verbatim, so
         scoring reflects semantic selection rather than string/keyword matching. Verified by
         `_check_no_leakage()` below, run at import time.
       - ~12 "ambiguous" intents (AMBIGUOUS_INTENTS): plausible for 2+ plans within the same
         confusable cluster, each with an authored `target` and an `acceptable` id list. An
         ambiguous intent only enters a catalog size's sweep if every id in `acceptable` is
         present in that catalog (checked in `_points_for_size`).
       - 3 "none" intents (NONE_INTENTS), clearly outside every plan; reused at every catalog
         size (restriction 7: typed refusal for planless requests).
  3. Catalog sizes N in {2, 5, 10, 20, 40}: the first N plans of SEED_BANK.
  4. Prompt format: copied verbatim from `ai/orchestrator.py::Orchestrator._resolve_plan` (see
     `build_prompt` below) -- KEEP THIS FUNCTION IN SYNC with that method; if the orchestrator's
     prompt changes, this script's results stop transferring to the real selection layer.
  5. Execution: direct model client only (no build_platform, no LangSmith) -- GeminiModelClient
     already retries on 429; LocalModelClient is for harness smoke only (its data has no
     research value, see the module docstring warning under Usage below).
  6. Checkpoint/resume: every data point is appended to results/selection-sweep/selection_sweep.jsonl as soon as
     it is measured. On start, existing records are loaded and any (model, rep, catalog_size,
     intent_id) already present is skipped -- the run survives a daily free-tier quota cutoff and
     resumes the next day without re-spending calls.
  7. Scoring: correct = (clear: chosen == target) | (ambiguous: chosen in acceptable) |
     (none: chosen == "none"). format_violation = chosen is neither an id offered in that
     catalog nor exactly "none" (stripped, case-sensitive).
  8. Reporting: console table by catalog_size x kind (n, accuracy, format_violations) plus mean
     input tokens per call (grows with N -- the cost of a larger catalog) and mean latency;
     results/selection-sweep/selection_sweep_report.json is written with the same data plus an accuracy-vs-N
     curve, broken out per model (so smoke-mode local data never gets averaged in with a real
     gemini run).

Usage:
    ./.venv/Scripts/python scripts/selection_sweep.py --provider local --reps 1 --max-calls 999
    ./.venv/Scripts/python scripts/selection_sweep.py --provider gemini --reps 2
    ./.venv/Scripts/python scripts/selection_sweep.py --report-only

--provider local is a zero-cost smoke test of the harness mechanics only: LocalModelClient
selects by keyword-in-intent (see infrastructure/providers/local_client.py::_select), and the
clear-intent paraphrases here are deliberately written to AVOID trigger words -- so local-mode
accuracy will be poor. That is expected; local mode validates plumbing (prompt format, JSONL
checkpointing, resume, report generation), not selection quality. Real numbers require
--provider gemini, which this script does not run on its own (free-tier budget is owned by
whoever launches it with --provider gemini).
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
for _path in (_REPO_ROOT / "src", _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from core.ports import ModelRequest  # noqa: E402

_RESULTS_DIR = _REPO_ROOT / "results"
_JSONL_PATH = _RESULTS_DIR / "selection-sweep" / "selection_sweep.jsonl"
_REPORT_PATH = _RESULTS_DIR / "selection-sweep" / "selection_sweep_report.json"

_DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
# Must match infrastructure/providers/local_client.py::_MODEL_NAME. That constant is private
# (module-internal) and this script does not import it -- kept in sync manually, same convention
# scripts/run_plans.py already uses for _CATALOG_ID_RE (duplicated, commented, from the same
# source module) rather than reaching into a private name.
_LOCAL_MODEL_NAME = "local-deterministic"

CATALOG_SIZES = (2, 5, 10, 20, 40)

# --------------------------------------------------------------------------------------
# 1. Seed bank: 40 synthetic plans, 15 confusable clusters (A..O), round-robin ordered so
#    diversity (complete confusable clusters present) grows with catalog size N.
# --------------------------------------------------------------------------------------

SEED_BANK: tuple[dict, ...] = (
    # -- round 1: one plan from each of the first clusters (A..J), + fill --
    {"id": "schedule-appointment", "description": "Book a new appointment slot for a customer who has none yet.",
     "triggers": ("schedule", "book", "new appointment", "availability")},
    {"id": "invoice-dispute", "description": "Open a dispute for charges the customer believes are incorrect on an invoice.",
     "triggers": ("dispute", "wrong charge", "incorrect", "billing error")},
    {"id": "reschedule-appointment", "description": "Move an existing appointment to a different date or time.",
     "triggers": ("reschedule", "move", "change time", "different day")},
    {"id": "employee-onboarding", "description": "Set up accounts, equipment and paperwork for a newly hired employee.",
     "triggers": ("onboarding", "new hire", "first day", "provisioning")},
    {"id": "order-tracking", "description": "Look up the current shipping status of a placed order.",
     "triggers": ("track order", "shipping status", "where is my order", "tracking number")},
    {"id": "invoice-payment", "description": "Process payment for an outstanding invoice balance.",
     "triggers": ("pay invoice", "outstanding balance", "settle", "pay bill")},
    {"id": "subscription-upgrade", "description": "Move a subscription to a higher tier plan.",
     "triggers": ("upgrade", "higher tier", "more features", "plan change up")},
    {"id": "cancel-appointment", "description": "Cancel an existing appointment and free the slot.",
     "triggers": ("cancel", "cancel appointment", "no longer need", "drop")},
    {"id": "order-cancellation", "description": "Cancel an order before it ships and reverse the charge.",
     "triggers": ("cancel order", "stop shipment", "before it ships", "void purchase")},
    {"id": "password-reset", "description": "Send a password reset link to the account owner.",
     "triggers": ("password reset", "forgot password", "reset link", "can't log in")},
    # -- round 2 completion of D/C/E/F + start of G/H --
    {"id": "order-return", "description": "Process a return for an item already delivered.",
     "triggers": ("return item", "send back", "delivered", "return label")},
    {"id": "employee-offboarding", "description": "Revoke access and close out accounts for a departing employee.",
     "triggers": ("offboarding", "termination", "last day", "deprovision")},
    {"id": "subscription-downgrade", "description": "Move a subscription to a lower tier plan.",
     "triggers": ("downgrade", "lower tier", "fewer features", "plan change down")},
    {"id": "subscription-cancellation", "description": "Cancel a subscription entirely, ending future billing.",
     "triggers": ("cancel subscription", "stop billing", "end plan", "unsubscribe")},
    {"id": "account-lockout-unlock", "description": "Unlock a customer account after repeated failed login attempts.",
     "triggers": ("locked out", "unlock account", "too many attempts", "suspended login")},
    {"id": "two-factor-reset", "description": "Reset two-factor authentication when the customer lost their device.",
     "triggers": ("2fa", "two-factor", "authenticator", "lost device")},
    {"id": "shipping-address-change", "description": "Update the delivery address on an order that hasn't shipped yet.",
     "triggers": ("change address", "delivery address", "wrong address", "update shipping")},
    {"id": "payment-method-update", "description": "Update the credit card or bank account on file for a customer.",
     "triggers": ("update payment method", "new card", "change card", "card on file")},
    {"id": "shipping-delay-inquiry", "description": "Investigate why a shipment is later than its estimated delivery date.",
     "triggers": ("delayed", "late delivery", "past due date", "still not arrived")},
    {"id": "payment-method-decline-help", "description": "Help a customer whose payment method was declined at checkout.",
     "triggers": ("declined", "payment failed", "card rejected", "checkout error")},
    # -- round 3: A/B/D/E/F/G/H (3rd/4th member) + I onward --
    {"id": "confirm-appointment", "description": "Send a confirmation reminder for an upcoming appointment.",
     "triggers": ("confirm", "confirmation", "reminder", "upcoming")},
    {"id": "refund-request", "description": "Issue a refund back to the customer's original payment method.",
     "triggers": ("refund", "money back", "reimburse", "return payment")},
    {"id": "shipping-damage-claim", "description": "File a claim for an item that arrived broken or damaged in transit.",
     "triggers": ("damaged", "broken", "arrived damaged", "transit damage")},
    {"id": "autopay-enrollment", "description": "Enroll a customer's account in automatic recurring payments.",
     "triggers": ("autopay", "auto-pay", "automatic payment", "enroll")},
    {"id": "warranty-claim", "description": "File a warranty claim for a defective product still under coverage.",
     "triggers": ("warranty", "defective", "under coverage", "malfunction")},
    {"id": "warranty-status-check", "description": "Check whether a product is still within its warranty coverage period.",
     "triggers": ("warranty status", "coverage period", "still covered", "expiration date")},
    {"id": "leave-request", "description": "Submit a request for time off work.",
     "triggers": ("time off", "leave request", "vacation", "PTO")},
    {"id": "leave-cancellation", "description": "Cancel a previously approved time-off request.",
     "triggers": ("cancel leave", "withdraw request", "no longer taking", "undo PTO")},
    {"id": "leave-balance-inquiry", "description": "Check how many days of paid time off a person has remaining.",
     "triggers": ("leave balance", "days remaining", "how many days left", "PTO balance")},
    {"id": "loyalty-points-redeem", "description": "Redeem accumulated loyalty points for a reward or discount.",
     "triggers": ("redeem points", "loyalty points", "cash in points", "rewards")},
    {"id": "loyalty-tier-upgrade", "description": "Move a customer to a higher loyalty membership tier.",
     "triggers": ("tier upgrade", "membership level", "elite status", "tier up")},
    {"id": "technical-support-ticket", "description": "Open a new technical support ticket for a product issue.",
     "triggers": ("tech support", "technical issue", "open ticket", "not working")},
    {"id": "technical-support-escalation", "description": "Escalate an existing support ticket to a higher tier of technical staff.",
     "triggers": ("escalate", "escalation", "senior support", "still unresolved")},
    {"id": "bug-report-submission", "description": "Submit a report describing a software bug for the engineering team.",
     "triggers": ("bug", "bug report", "software issue", "glitch")},
    {"id": "billing-plan-upgrade", "description": "Move a customer's billing plan to a more expensive tier with more usage included.",
     "triggers": ("billing upgrade", "more usage", "higher plan", "increase limit")},
    {"id": "billing-plan-downgrade", "description": "Move a customer's billing plan to a cheaper tier with less usage included.",
     "triggers": ("billing downgrade", "less usage", "lower plan", "decrease limit")},
    {"id": "complaint-submission", "description": "Log a formal complaint about a customer's experience.",
     "triggers": ("complaint", "unhappy", "formal complaint", "file a complaint")},
    {"id": "complaint-escalation", "description": "Escalate an unresolved complaint to a manager or supervisor.",
     "triggers": ("escalate complaint", "speak to manager", "supervisor", "unresolved complaint")},
    {"id": "gift-card-balance", "description": "Check the remaining balance on a gift card.",
     "triggers": ("gift card balance", "remaining balance", "check balance", "how much is left")},
    {"id": "gift-card-redemption", "description": "Apply a gift card's value toward a purchase.",
     "triggers": ("redeem gift card", "apply gift card", "use gift card", "gift card code")},
)

assert len(SEED_BANK) == 40, f"seed bank must have exactly 40 plans, has {len(SEED_BANK)}"
assert len({p["id"] for p in SEED_BANK}) == 40, "seed bank ids must be unique"

# --------------------------------------------------------------------------------------
# 2. Hardcoded intents.
# --------------------------------------------------------------------------------------

# One "clear" paraphrase per plan id -- natural language, no id words, no trigger phrases
# verbatim (enforced by `_check_no_leakage`, called at import time below).
CLEAR_INTENTS: dict[str, str] = {
    "schedule-appointment": "I'd like to find an open time with the doctor next week — I don't currently have anything reserved on the calendar.",
    "reschedule-appointment": "Something came up and I need to shift my visit with the therapist to another day — I already have one on the books, just at the wrong time now.",
    "cancel-appointment": "I won't be able to make my checkup after all — please take me off the calendar entirely, I don't need a new time.",
    "confirm-appointment": "Can someone verify that my visit next Tuesday is still on the books and send me a heads-up beforehand?",
    "invoice-dispute": "The amount on my latest bill doesn't match what I actually ordered — I think there's a mistake and want it looked into, not necessarily refunded yet.",
    "invoice-payment": "I want to clear what I still owe from my last statement right now using the card on file.",
    "refund-request": "I'd like my money returned to the card I used — the item wasn't what I expected and I already sent it back separately.",
    "employee-onboarding": "We just hired someone and need their accounts and gear set up before they start Monday.",
    "employee-offboarding": "Someone on the team is leaving the company and we need their system access shut off after their final day.",
    "order-tracking": "Can you tell me where my package currently is and when it might show up?",
    "order-cancellation": "I changed my mind about that purchase — please stop it before it goes out and take the charge off my card.",
    "order-return": "The item already arrived but it isn't right for me, and I'd like to ship it back to get my money back.",
    "subscription-upgrade": "I keep hitting limits on what I have now and want to move to the larger package with everything unlocked.",
    "subscription-downgrade": "I'm paying for more than I actually use — please switch me to something cheaper with less included.",
    "subscription-cancellation": "Please stop charging me for this service going forward — I don't want it anymore at any tier.",
    "password-reset": "I can't remember the credentials for my account and need a way to get back in.",
    "account-lockout-unlock": "My login got frozen after I mistyped my details a bunch of times, and now I can't get past the sign-in screen at all.",
    "two-factor-reset": "I lost the phone that had my verification codes on it and now I'm stuck outside my own account.",
    "shipping-address-change": "I entered the wrong place for my package to go — can you fix where it's headed before it leaves the warehouse?",
    "shipping-delay-inquiry": "My package was supposed to show up days ago and it still hasn't — what's going on?",
    "shipping-damage-claim": "What I received was smashed up inside the box — I want to report it and get this sorted out.",
    "payment-method-update": "My old card expired and I need to put a new one on my account for future charges.",
    "payment-method-decline-help": "My card got turned down when I tried to pay and I don't understand why.",
    "autopay-enrollment": "I'm tired of manually paying every month — can you set things up so it just happens on its own from now on?",
    "warranty-claim": "This thing stopped working properly not long after I bought it, and I believe it should still be covered.",
    "warranty-status-check": "I want to know if the protection on this purchase has run out yet or if I've still got time left on it.",
    "leave-request": "I'd like to book some days away from work next month for a family trip.",
    "leave-cancellation": "My plans fell through, so I no longer need those days off I had approved — please put them back.",
    "leave-balance-inquiry": "How many paid days do I still have banked before the year ends?",
    "loyalty-points-redeem": "I've built up a bunch of credit on my account from regular purchases and want to trade it in for something useful.",
    "loyalty-tier-upgrade": "I think I've earned enough to move up to better standing in the rewards program than what I have now.",
    "technical-support-ticket": "Something in the app is broken and I need someone from the engineering side to look into it.",
    "technical-support-escalation": "Nobody has fixed my issue yet after multiple tries — I need this passed along to someone more senior who can actually solve it.",
    "bug-report-submission": "I found something in the app that clearly isn't behaving the way it's supposed to and wanted the developers to know.",
    "billing-plan-upgrade": "I keep going over my usage cap every month and want a package with more room built in.",
    "billing-plan-downgrade": "I barely use half of what I'm paying for and want something cheaper with a smaller cap.",
    "complaint-submission": "I had a genuinely bad experience with your service and I want it on record officially.",
    "complaint-escalation": "I've raised this issue before and nothing changed — I want to speak with whoever is in charge above the person I already talked to.",
    "gift-card-balance": "Can you tell me how much value is still sitting on the one I was given as a present, before I use it?",
    "gift-card-redemption": "I have a code from something I received and want to put its value toward something I'm buying.",
}
assert set(CLEAR_INTENTS) == {p["id"] for p in SEED_BANK}, "every plan needs exactly one clear intent"

# ~12 "ambiguous" intents: plausible for 2+ plans in the same confusable cluster. `acceptable`
# lists every id the model may pick and still be scored correct; an intent only enters a given
# catalog size's sweep if ALL of `acceptable` is offered at that size (see `_points_for_size`).
AMBIGUOUS_INTENTS: tuple[dict, ...] = (
    {"id": "amb-appointment-fix-time", "target": "reschedule-appointment",
     "acceptable": ["reschedule-appointment", "schedule-appointment"],
     "text": "I need to sort out my visit time with the clinic — it's currently set for the wrong day and I need that fixed."},
    {"id": "amb-appointment-drop-or-move", "target": "cancel-appointment",
     "acceptable": ["cancel-appointment", "reschedule-appointment"],
     "text": "I don't want to keep the time I currently have booked with the clinic, but I'm not sure yet if I'll pick a new one or just let it go entirely."},
    {"id": "amb-invoice-pay-or-push-back", "target": "invoice-dispute",
     "acceptable": ["invoice-dispute", "invoice-payment"],
     "text": "There's a bill on my account and I'm honestly not sure yet whether I should just pay what's shown or push back on it because something looks off."},
    {"id": "amb-employee-status-change", "target": "employee-onboarding",
     "acceptable": ["employee-onboarding", "employee-offboarding"],
     "text": "Someone's status with the company is changing and we need to update all their system accounts to match, though I haven't said which direction the change is."},
    {"id": "amb-order-second-thoughts", "target": "order-cancellation",
     "acceptable": ["order-tracking", "order-cancellation"],
     "text": "I placed an order recently and I'm having second thoughts, but I also just want an update on where things stand right now."},
    {"id": "amb-order-not-arrived-yet", "target": "order-return",
     "acceptable": ["order-cancellation", "order-return"],
     "text": "The item hasn't shown up at my door yet, but honestly if it does arrive I might just want to send it right back anyway."},
    {"id": "amb-subscription-tier-mismatch", "target": "subscription-upgrade",
     "acceptable": ["subscription-upgrade", "subscription-downgrade"],
     "text": "My needs on this plan have shifted lately and I think the tier I'm on doesn't fit anymore, one way or another."},
    {"id": "amb-cant-get-in", "target": "password-reset",
     "acceptable": ["password-reset", "account-lockout-unlock"],
     "text": "I'm completely stuck trying to get into my profile and nothing I type seems to work anymore."},
    {"id": "amb-locked-verification", "target": "account-lockout-unlock",
     "acceptable": ["account-lockout-unlock", "two-factor-reset"],
     "text": "I'm locked out of my profile and it has something to do with the extra verification step, though I can't tell exactly what broke."},
    {"id": "amb-shipping-off-and-slow", "target": "shipping-address-change",
     "acceptable": ["shipping-address-change", "shipping-delay-inquiry"],
     "text": "Something about where my package is headed seems off, and it's also taking way longer than I expected."},
    {"id": "amb-payment-checkout-trouble", "target": "payment-method-decline-help",
     "acceptable": ["payment-method-update", "payment-method-decline-help"],
     "text": "My payment info on the account might be out of date, and every time I try to use it something goes wrong at checkout."},
    {"id": "amb-support-fresh-or-push", "target": "technical-support-escalation",
     "acceptable": ["technical-support-ticket", "technical-support-escalation"],
     "text": "I already asked for help with this problem once and it's still not fixed, so I don't know if I need to start fresh or push it further up."},
)

NONE_INTENTS: tuple[str, ...] = (
    "What's the weather going to be like this weekend?",
    "Can you recommend a good recipe for banana bread?",
    "I'm trying to learn to play the guitar — any tips for a total beginner?",
)


def _check_no_leakage() -> None:
    """Guards restriction from the spec: a clear intent must not string-match its own plan.

    Checks, per plan: (a) no id word (the id split on '-') appears in the paraphrase as a whole
    word, and (b) no trigger phrase appears verbatim as a substring. Both checks are
    case-insensitive and scoped to the plan's OWN id/triggers only (cross-plan overlap is
    expected and fine -- that is exactly what makes the ambiguous intents plausible).
    """
    import re

    violations = []
    for plan in SEED_BANK:
        text_lower = CLEAR_INTENTS[plan["id"]].lower()
        for word in plan["id"].split("-"):
            if re.search(rf"\b{re.escape(word)}\b", text_lower):
                violations.append(f"{plan['id']}: id word '{word}' leaked into its clear intent")
        for trigger in plan["triggers"]:
            if trigger.lower() in text_lower:
                violations.append(f"{plan['id']}: trigger '{trigger}' leaked into its clear intent")
    if violations:
        raise AssertionError("clear-intent leakage detected:\n  " + "\n  ".join(violations))


_check_no_leakage()


# --------------------------------------------------------------------------------------
# 4. Prompt format -- copied verbatim from ai/orchestrator.py::Orchestrator._resolve_plan.
#    KEEP IN SYNC: if that method's prompt text changes, mirror the change here or results
#    stop transferring to the real selection layer.
# --------------------------------------------------------------------------------------

def build_prompt(intent: str, catalog_plans: list[dict]) -> str:
    """Byte-identical to `_resolve_plan`'s prompt, given the same intent and plan catalog."""
    catalog = "\n".join(
        f"- {p['id']}: {p['description']} (triggers: {', '.join(p['triggers'])})" for p in catalog_plans
    )
    return (
        "Choose the id of the plan best suited to the intent below, or answer exactly 'none' "
        f"if none applies.\nIntent: {intent}\n\nAvailable plans:\n{catalog}"
    )


# --------------------------------------------------------------------------------------
# 3. Catalog sizes + per-size data points (clear + eligible ambiguous + none).
# --------------------------------------------------------------------------------------

def _points_for_size(n: int) -> list[dict]:
    """All data points for catalog size n: one clear point per offered plan, every ambiguous
    intent whose full `acceptable` set is offered, and the 3 none intents."""
    catalog_plans = list(SEED_BANK[:n])
    offered_ids = {p["id"] for p in catalog_plans}
    points = []
    for p in catalog_plans:
        points.append({
            "intent_id": p["id"], "kind": "clear", "target": p["id"], "acceptable": [p["id"]],
            "intent_text": CLEAR_INTENTS[p["id"]],
        })
    for amb in AMBIGUOUS_INTENTS:
        if set(amb["acceptable"]) <= offered_ids:
            points.append({
                "intent_id": amb["id"], "kind": "ambiguous", "target": amb["target"],
                "acceptable": list(amb["acceptable"]), "intent_text": amb["text"],
            })
    for i, text in enumerate(NONE_INTENTS):
        points.append({
            "intent_id": f"none-{i + 1}", "kind": "none", "target": "none", "acceptable": ["none"],
            "intent_text": text,
        })
    return points


SIZE_POINTS: dict[int, list[dict]] = {n: _points_for_size(n) for n in CATALOG_SIZES}


# --------------------------------------------------------------------------------------
# 5-7. Execution, checkpoint/resume, scoring.
# --------------------------------------------------------------------------------------

def _build_client(provider: str, model_label: str):
    if provider == "gemini":
        os.environ["GEMINI_MODEL"] = model_label  # must be set before instantiation (see gemini_client.py)
        from infrastructure.providers.gemini_client import GeminiModelClient
        return GeminiModelClient()
    from infrastructure.providers.local_client import LocalModelClient
    return LocalModelClient()


def _resolve_model_label(provider: str, model_arg: str | None) -> str:
    if provider == "gemini":
        return model_arg or os.environ.get("GEMINI_MODEL") or _DEFAULT_GEMINI_MODEL
    if model_arg:
        print(f"[selection_sweep] --model is ignored for --provider local "
              f"(LocalModelClient always reports model={_LOCAL_MODEL_NAME!r})")
    return _LOCAL_MODEL_NAME


def _load_existing(path: Path) -> tuple[set[tuple], list[dict]]:
    """Reads the checkpoint JSONL; returns (keys already measured, all raw records)."""
    if not path.exists():
        return set(), []
    keys: set[tuple] = set()
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records.append(record)
            keys.add((record["model"], record["rep"], record["catalog_size"], record["intent_id"]))
    return keys, records


def _run_point(client, model_label: str, rep: int, catalog_size: int, catalog_plans: list[dict],
               offered_ids: set[str], point: dict) -> dict:
    prompt = build_prompt(point["intent_text"], catalog_plans)
    request = ModelRequest(messages=({"role": "user", "content": prompt},), purpose="selection", max_tokens=1024)
    started = time.monotonic()
    response = client.complete(request)
    latency_ms = (time.monotonic() - started) * 1000
    chosen = response.content.strip()

    kind, target, acceptable = point["kind"], point["target"], point["acceptable"]
    if kind == "clear":
        correct = chosen == target
    elif kind == "ambiguous":
        correct = chosen in acceptable
    else:
        correct = chosen == "none"
    format_violation = chosen != "none" and chosen not in offered_ids

    return {
        "model": response.model or model_label,
        "rep": rep,
        "catalog_size": catalog_size,
        "intent_id": point["intent_id"],
        "kind": kind,
        "target": target,
        "acceptable": acceptable,
        "chosen": chosen,
        "correct": correct,
        "format_violation": format_violation,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": latency_ms,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _run_sweep(provider: str, reps: int, max_calls: int, model_label: str) -> None:
    _JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing_keys, _existing_records = _load_existing(_JSONL_PATH)

    total_points_per_rep = sum(len(points) for points in SIZE_POINTS.values())
    total_expected = reps * total_points_per_rep
    print(f"[selection_sweep] provider={provider} model={model_label} reps={reps} "
          f"max_calls={max_calls} points_per_rep={total_points_per_rep} total_expected={total_expected}")
    for n in CATALOG_SIZES:
        print(f"  N={n:>2}: {len(SIZE_POINTS[n])} points "
              f"(clear={n}, ambiguous={sum(1 for p in SIZE_POINTS[n] if p['kind'] == 'ambiguous')}, "
              f"none={sum(1 for p in SIZE_POINTS[n] if p['kind'] == 'none')})")

    already_done = sum(1 for k in existing_keys if k[0] == model_label)
    if already_done:
        print(f"[selection_sweep] resume: {already_done} points already measured for model={model_label}, skipping them")

    client = _build_client(provider, model_label)
    calls_made = 0
    stopped_early = False

    with open(_JSONL_PATH, "a", encoding="utf-8") as fh:
        for rep in range(1, reps + 1):
            if stopped_early:
                break
            for n in CATALOG_SIZES:
                if stopped_early:
                    break
                catalog_plans = list(SEED_BANK[:n])
                offered_ids = {p["id"] for p in catalog_plans}
                for point in SIZE_POINTS[n]:
                    key = (model_label, rep, n, point["intent_id"])
                    if key in existing_keys:
                        continue
                    if calls_made >= max_calls:
                        stopped_early = True
                        break
                    record = _run_point(client, model_label, rep, n, catalog_plans, offered_ids, point)
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
                    existing_keys.add(key)
                    calls_made += 1
                    status = "OK" if record["correct"] else ("FMT" if record["format_violation"] else "MISS")
                    print(f"  [{calls_made:>4}] rep={rep} N={n:>2} {point['kind']:<9} {point['intent_id']:<30} "
                          f"chosen={record['chosen']!r:<32} {status} ({record['latency_ms']:.0f}ms)")

    print(f"[selection_sweep] made {calls_made} model calls this run")
    if stopped_early:
        remaining = total_expected - sum(1 for k in existing_keys if k[0] == model_label)
        print(f"[selection_sweep] budget guard: stopped at --max-calls={max_calls}. "
              f"{remaining} data points still unmeasured for model={model_label} -- rerun to continue (resume via JSONL).")


# --------------------------------------------------------------------------------------
# 8. Reporting.
# --------------------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _kind_stats(records: list[dict]) -> dict:
    n = len(records)
    correct = sum(1 for r in records if r["correct"])
    violations = sum(1 for r in records if r["format_violation"])
    return {"n": n, "accuracy": (correct / n) if n else 0.0, "format_violations": violations}


def _build_report(all_records: list[dict]) -> dict:
    by_model: dict[str, dict] = {}
    models = sorted({r["model"] for r in all_records})
    for model in models:
        model_records = [r for r in all_records if r["model"] == model]
        by_size: dict[str, dict] = {}
        curve = []
        for n in sorted({r["catalog_size"] for r in model_records}):
            size_records = [r for r in model_records if r["catalog_size"] == n]
            by_kind = {}
            for kind in ("clear", "ambiguous", "none"):
                kind_records = [r for r in size_records if r["kind"] == kind]
                if kind_records:
                    by_kind[kind] = _kind_stats(kind_records)
            overall = _kind_stats(size_records)
            by_size[str(n)] = {
                "overall": overall,
                "by_kind": by_kind,
                "avg_input_tokens": _mean([r["input_tokens"] for r in size_records]),
                "avg_output_tokens": _mean([r["output_tokens"] for r in size_records]),
                "avg_latency_ms": _mean([r["latency_ms"] for r in size_records]),
            }
            curve.append({"catalog_size": n, "n": overall["n"], "accuracy": overall["accuracy"]})
        by_model[model] = {"by_catalog_size": by_size, "accuracy_curve": curve, "n_records": len(model_records)}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_jsonl": _JSONL_PATH.relative_to(_REPO_ROOT).as_posix(),
        "n_records": len(all_records),
        "models_present": models,
        "by_model": by_model,
    }


def _print_report(report: dict) -> None:
    for model, data in report["by_model"].items():
        print(f"\n=== model: {model} ({data['n_records']} records) ===")
        for size_str, size_data in data["by_catalog_size"].items():
            overall = size_data["overall"]
            print(f"\n  N={size_str:>2}  overall: n={overall['n']:<3} accuracy={overall['accuracy']:.1%} "
                  f"format_violations={overall['format_violations']}")
            print(f"        avg_input_tokens={size_data['avg_input_tokens']:.1f}  "
                  f"avg_output_tokens={size_data['avg_output_tokens']:.1f}  "
                  f"avg_latency_ms={size_data['avg_latency_ms']:.1f}")
            for kind, stats in size_data["by_kind"].items():
                print(f"        {kind:<10} n={stats['n']:<3} accuracy={stats['accuracy']:.1%} "
                      f"format_violations={stats['format_violations']}")
        print("\n  accuracy curve (N vs accuracy):")
        for point in data["accuracy_curve"]:
            print(f"    N={point['catalog_size']:>2}  n={point['n']:<3}  accuracy={point['accuracy']:.1%}")


def _write_report(report: dict) -> None:
    _JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n[selection_sweep] wrote {_REPORT_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "local"], default="gemini",
                         help="model provider (default: gemini; local is harness-smoke only, see module docstring)")
    parser.add_argument("--reps", type=int, default=2, help="repetitions of the full sweep (default: 2)")
    parser.add_argument("--max-calls", type=int, default=400,
                         help="budget guard: stop cleanly after this many NEW model calls in this run (default: 400)")
    parser.add_argument("--model", default=None,
                         help="model name (gemini only; default: env GEMINI_MODEL or gemini-3.1-flash-lite)")
    parser.add_argument("--report-only", action="store_true",
                         help="skip the sweep; just (re)build the console table and report JSON from the existing JSONL")
    args = parser.parse_args()

    if args.reps < 1:
        print(f"[selection_sweep] --reps must be >= 1 (got {args.reps})", file=sys.stderr)
        return 2
    if args.max_calls < 0:
        print(f"[selection_sweep] --max-calls must be >= 0 (got {args.max_calls})", file=sys.stderr)
        return 2

    if not args.report_only:
        model_label = _resolve_model_label(args.provider, args.model)
        _run_sweep(args.provider, args.reps, args.max_calls, model_label)

    _, all_records = _load_existing(_JSONL_PATH)
    if not all_records:
        print("[selection_sweep] no records in results/selection-sweep/selection_sweep.jsonl -- nothing to report")
        return 0
    report = _build_report(all_records)
    _print_report(report)
    _write_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
