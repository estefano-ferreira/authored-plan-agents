#!/usr/bin/env python
"""Embedding-similarity selection baseline vs. the LLM selection sweep.

Governed by docs/preregistration-embedding-baseline.md -- read that document first; this
script exists to make deviation from it mechanically impossible, not merely discouraged.
Do not add flags, defaults or code paths that let the eval phase adjust tau, that let the
eval dataset diverge from results/selection-sweep/selection_sweep.jsonl, or that let a
missing credential fall back to an unmeasured run silently.

Question: does cosine similarity between an intent embedding and each catalog plan's
embedded (description + triggers) match LLM plan selection on the IDENTICAL 240-point set
scripts/selection_sweep.py measured? Two phases, structurally separated:

  1. `calibrate` -- grid-searches a refusal threshold tau on a small, DISJOINT synthetic set
     hardcoded in this file (CALIBRATION_PLANS / CALIBRATION_*_INTENTS below), never touching
     scripts/selection_sweep.py's dataset. Writes a frozen JSON artifact: tau, the embedding
     model id, the provider, a content hash of the calibration set, and the grid-search
     objective -- nothing here reads results/selection-sweep/selection_sweep.jsonl.
  2. `run` -- loads that frozen artifact (refuses to start if it is missing), then evaluates
     against the real 240-point set by importing scripts/selection_sweep.py's generator
     (SEED_BANK / SIZE_POINTS -- deterministic, hardcoded, no RNG, see that module's
     docstring) and ASSERTING every point recorded in selection_sweep.jsonl matches the
     generator exactly (fails loudly on any mismatch -- see `_assert_dataset_matches_recorded`).
     `run` never has a `--tau` flag: tau can only ever come from the frozen artifact, so
     there is no code path by which the eval phase could adjust it. `run` also refuses to
     start if an existing output JSONL was measured under a DIFFERENT calibration artifact
     (hash mismatch), which would otherwise let a silent recalibration mix incomparable
     records into one file across a resumed run.

Selector: cosine similarity, top-1. chosen = argmax plan by similarity if that similarity
>= tau, else "none" (typed out-of-catalog refusal). Scoring is identical to
scripts/selection_sweep.py: clear = (chosen == target), ambiguous = (chosen in acceptable),
none = (chosen == "none"); format_violation = chosen is neither an offered id nor "none".

Embedding backend is pluggable by CLI (--provider, --embedding-model):
  - gemini: REST calls (httpx, not the google-genai SDK -- the pre-registration specifically
    calls for a REST provider here) to the Generative Language API's embedContent endpoint.
    Requires GOOGLE_API_KEY; missing credentials raise at construction, before any call is
    attempted -- an unmeasured run must never masquerade as a measured one.
  - local: lazy-imports the OPTIONAL `sentence-transformers` package (NOT added to
    pyproject.toml -- same convention as infrastructure/providers/local_client.py's
    LocalModelClient: this path is harness-smoke only, its data has no research value).
    Raises a clear RuntimeError at construction if the package is not installed.
--embedding-model has NO default on either subcommand: the pre-registration requires the
model to be a conscious, recorded choice made on price/availability "never on eval-set
performance" -- a silent default would be exactly the kind of undocumented choice that
discipline is meant to prevent.

Every embedding call is cached on disk, content-addressed by sha256(provider, model, text),
under var/embedding_cache/ (gitignored runtime data, same directory class as var/erp/) --
a resumed `calibrate` or `run` re-embeds nothing it has already paid for. Since a text's
embedding is a pure function of (provider, model, text), --reps beyond 1 on `run` is
redundant (every repeated point is served from the cache) -- kept only for report-shape
parity with scripts/selection_sweep.py's --reps.

Outputs (written only when actually run, into results/selection-sweep/ beside the LLM
sweep's evidence, per the pre-registration):
  - embedding_baseline_calibration.json  (calibrate)
  - embedding_baseline.jsonl             (run; checkpointed, resumable, one record per
                                           (model, rep, catalog_size, intent_id))
  - embedding_baseline_report.json       (run; same shape as selection_sweep_report.json:
                                           accuracy per intent kind per catalog size,
                                           out-of-catalog refusal, accuracy-vs-N curve)

Usage:
    ./.venv/Scripts/python scripts/embedding_baseline.py calibrate \\
        --provider gemini --embedding-model gemini-embedding-001
    ./.venv/Scripts/python scripts/embedding_baseline.py run \\
        --provider gemini --embedding-model gemini-embedding-001
    ./.venv/Scripts/python scripts/embedding_baseline.py run \\
        --provider gemini --embedding-model gemini-embedding-001 --report-only

--provider local is a zero-cost smoke test of the harness mechanics only (plumbing: caching,
tau freezing, checkpoint/resume, report generation) -- it requires `pip install
sentence-transformers` manually and its data has no research value, exactly like
scripts/selection_sweep.py --provider local. Real numbers require --provider gemini.
"""
import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
for _path in (_SCRIPT_DIR, _REPO_ROOT / "src", _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import selection_sweep as ss  # noqa: E402  -- read-only: the real 240-point generator + the

# recorded LLM checkpoint JSONL this script asserts against. `run` is the ONLY code path in
# this file that references `ss`; `calibrate` never imports or touches it (see module docstring).

_RESULTS_DIR = _REPO_ROOT / "results"
_LLM_JSONL_PATH = _RESULTS_DIR / "selection-sweep" / "selection_sweep.jsonl"
_CALIBRATION_PATH_DEFAULT = _RESULTS_DIR / "selection-sweep" / "embedding_baseline_calibration.json"
_EVAL_JSONL_PATH = _RESULTS_DIR / "selection-sweep" / "embedding_baseline.jsonl"
_EVAL_REPORT_PATH = _RESULTS_DIR / "selection-sweep" / "embedding_baseline_report.json"
_CACHE_DIR = _REPO_ROOT / "var" / "embedding_cache"  # gitignored (var/), content-addressed


# --------------------------------------------------------------------------------------
# Embedding providers.
# --------------------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    """Boundary for an embedding backend. `model_id` is stamped into every cached vector and
    every output record -- never silently defaulted, always the exact string the caller passed."""
    provider_name: str
    model_id: str

    @abstractmethod
    def embed_one(self, text: str) -> list[float]: ...


class GeminiEmbeddingProvider(EmbeddingProvider):
    """REST client (httpx) against the Generative Language API's embedContent endpoint.

    Deliberately REST, not the google-genai SDK already used by GeminiModelClient (see
    infrastructure/providers/gemini_client.py) -- the pre-registration specifically calls for
    a REST provider here.
    """
    provider_name = "gemini"

    def __init__(self, model_id: str) -> None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set; refusing to run without measurement: set the "
                "environment variable to use GeminiEmbeddingProvider. No silent fallback -- "
                "an unmeasured run must never masquerade as a measured one."
            )
        import httpx  # already a hard repo dependency (pyproject.toml); no lazy-import needed

        self.model_id = model_id
        model_path = model_id if model_id.startswith("models/") else f"models/{model_id}"
        self._embed_path = f"/v1beta/{model_path}:embedContent"
        # Auth via header, not a `?key=` query param, so the key never lands in a URL that
        # might get logged by an intermediate proxy or printed in an error trace.
        self._client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com",
            headers={"x-goog-api-key": api_key},
            timeout=30.0,
        )

    def embed_one(self, text: str) -> list[float]:
        response = self._post_with_rate_limit_retry({"content": {"parts": [{"text": text}]}})
        data = response.json()
        try:
            return [float(v) for v in data["embedding"]["values"]]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"unexpected embedContent response shape: {data!r}") from exc

    def _post_with_rate_limit_retry(self, body: dict, max_attempts: int = 10):
        """Same 429/retryDelay backoff convention as GeminiModelClient (see gemini_client.py's
        `_generate_with_rate_limit_retry`); any other HTTP error propagates untouched."""
        for attempt in range(max_attempts):
            response = self._client.post(self._embed_path, json=body)
            if response.status_code == 429:
                if attempt == max_attempts - 1:
                    response.raise_for_status()
                hint = re.search(r"retry in (\d+(?:\.\d+)?)s", response.text, re.IGNORECASE)
                delay = float(hint.group(1)) + 1.0 if hint else 15.0 * (attempt + 1)
                print(f"[embedding_baseline] rate limited (429); retrying in {delay:.0f}s "
                      f"(attempt {attempt + 1}/{max_attempts})")
                time.sleep(min(delay, 70.0))
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")


class LocalEmbeddingProvider(EmbeddingProvider):
    """Harness-smoke only. Lazy-imports the OPTIONAL `sentence-transformers` package (not a
    repo dependency -- do not add it to pyproject.toml); fails loudly and clearly if absent."""
    provider_name = "local"

    def __init__(self, model_id: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "'sentence-transformers' is not installed. It is an OPTIONAL dependency, "
                "deliberately NOT added to pyproject.toml (this provider is a harness-smoke "
                "path only, same convention as infrastructure/providers/local_client.py's "
                "LocalModelClient -- its data has no research value, see "
                "docs/preregistration-embedding-baseline.md). Install it yourself with "
                "`pip install sentence-transformers` if you want to exercise this path; use "
                "--provider gemini for the measured run."
            ) from exc
        self.model_id = model_id
        self._model = SentenceTransformer(model_id)

    def embed_one(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=False)
        return [float(x) for x in vector]


def _build_provider(provider_name: str, embedding_model: str) -> EmbeddingProvider:
    if provider_name == "gemini":
        return GeminiEmbeddingProvider(embedding_model)
    if provider_name == "local":
        return LocalEmbeddingProvider(embedding_model)
    raise ValueError(f"unknown provider {provider_name!r}")  # unreachable: argparse restricts choices


# --------------------------------------------------------------------------------------
# Content-addressed disk cache (var/embedding_cache/, gitignored). Resuming a run re-embeds
# nothing already paid for; a text's embedding is a pure function of (provider, model, text).
# --------------------------------------------------------------------------------------

def _cache_key(provider_name: str, model_id: str, text: str) -> str:
    digest = hashlib.sha256()
    for part in (provider_name, model_id, text):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class CachedEmbedder:
    """Wraps an EmbeddingProvider with an on-disk cache. Tracks hits/misses for budget guards
    and console reporting; `misses` counts only calls actually made THIS process."""

    def __init__(self, provider: EmbeddingProvider, cache_dir: Path = _CACHE_DIR) -> None:
        self._provider = provider
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def embed(self, text: str) -> list[float]:
        key = _cache_key(self._provider.provider_name, self._provider.model_id, text)
        path = self._cache_dir / f"{key}.json"
        if path.exists():
            self.hits += 1
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)["embedding"]
        vector = self._provider.embed_one(text)
        self.misses += 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "provider": self._provider.provider_name,
                "model": self._provider.model_id,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "embedding": vector,
            }, fh)
        return vector


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _plan_text(plan: dict) -> str:
    """Text embedded per catalog plan: description + triggers (the contract's exact wording).
    Identical for a given plan across every catalog size it appears in, so the on-disk cache
    embeds it at most once per (provider, model) regardless of how many points reference it."""
    return f"{plan['description']} Triggers: {', '.join(plan['triggers'])}"


# --------------------------------------------------------------------------------------
# Calibration dataset: DISJOINT from scripts/selection_sweep.py's SEED_BANK/CLEAR_INTENTS/
# AMBIGUOUS_INTENTS/NONE_INTENTS -- different domain space entirely (library / restaurant /
# vehicle rental / event ticketing vs. the eval set's appointments / billing / HR / orders /
# subscriptions / security / shipping / warranty / leave / loyalty / support / complaints /
# gift cards), so there is zero id or text overlap by construction. Same authoring method as
# the eval set (confusable clusters of 3, one no-leakage clear paraphrase per plan, a few
# ambiguous intents naming an `acceptable` set, a handful of out-of-catalog "none" intents) --
# see docs/preregistration-embedding-baseline.md's "same generator, different seed" and the
# ambiguity note in this script's design writeup: there is no literal seeded RNG generator in
# scripts/selection_sweep.py to re-invoke (its dataset is hardcoded literal text, not sampled),
# so "different seed" is realized here as a hand-authored disjoint set following the same
# method, tagged with the nominal id below instead of a numeric seed.
# --------------------------------------------------------------------------------------

_CALIBRATION_SEED_LABEL = "embedding-baseline-calibration-v1"

CALIBRATION_PLANS: tuple[dict, ...] = (
    {"id": "borrow-book", "description": "Check out a book from the library catalog for a member who does not currently have it.",
     "triggers": ("borrow", "check out", "take out", "new loan")},
    {"id": "renew-book-loan", "description": "Extend the due date on a book a member already has checked out.",
     "triggers": ("renew", "extend due date", "keep longer", "more time")},
    {"id": "return-book", "description": "Mark a checked-out book as returned and close its loan.",
     "triggers": ("return", "bring back", "give back", "close loan")},
    {"id": "book-table", "description": "Reserve a table at the restaurant for a party that has no existing reservation.",
     "triggers": ("book table", "reserve", "new reservation", "party of")},
    {"id": "modify-table-reservation", "description": "Change the time, date or party size on a restaurant reservation that already exists.",
     "triggers": ("modify reservation", "change reservation", "different time", "update party size")},
    {"id": "cancel-table-reservation", "description": "Cancel an existing restaurant reservation and release the table.",
     "triggers": ("cancel reservation", "cancel table", "no longer coming", "release table")},
    {"id": "rent-vehicle", "description": "Start a new vehicle rental agreement for a customer with no current rental.",
     "triggers": ("rent a car", "new rental", "pick up vehicle", "start rental")},
    {"id": "extend-vehicle-rental", "description": "Lengthen the return date on a vehicle a customer has already rented.",
     "triggers": ("extend rental", "keep the car longer", "push back return", "more days")},
    {"id": "cancel-vehicle-rental", "description": "Cancel a vehicle rental before pickup and void the reservation.",
     "triggers": ("cancel rental", "cancel booking", "don't need the car", "void reservation")},
    {"id": "purchase-event-ticket", "description": "Buy a new ticket to an event for someone who does not already have one.",
     "triggers": ("buy ticket", "purchase ticket", "new ticket", "get a seat")},
    {"id": "transfer-event-ticket", "description": "Move an already-purchased ticket to a different attendee's name.",
     "triggers": ("transfer ticket", "change name on ticket", "give ticket away", "reassign ticket")},
    {"id": "refund-event-ticket", "description": "Refund the cost of a previously purchased ticket back to the buyer.",
     "triggers": ("refund ticket", "money back", "get refund", "cancel and refund")},
)
assert len(CALIBRATION_PLANS) == 12, f"calibration bank must have exactly 12 plans, has {len(CALIBRATION_PLANS)}"
assert len({p["id"] for p in CALIBRATION_PLANS}) == 12, "calibration plan ids must be unique"
assert not ({p["id"] for p in CALIBRATION_PLANS} & {p["id"] for p in ss.SEED_BANK}), \
    "calibration set must be disjoint from the eval set's ids"

CALIBRATION_CLEAR_INTENTS: dict[str, str] = {
    "borrow-book": "I'd like to take one of the titles on your shelves home with me — I don't have anything of yours checked out right now.",
    "renew-book-loan": "I still need a while longer with the copy I already have before it's due back.",
    "return-book": "I'm finished with the copy I had and want to hand it back so my account shows it's no longer out.",
    "book-table": "We'd like a spot to eat at your place this weekend — we haven't set anything up with you yet.",
    "modify-table-reservation": "We already have a spot saved with you but need to shift the headcount and the hour a bit.",
    "cancel-table-reservation": "Our plans changed and we won't be coming in after all — please free up the spot you were holding for us.",
    "rent-vehicle": "I need a set of wheels for a few days and don't currently have anything reserved with you.",
    "extend-vehicle-rental": "I already have a set of wheels from you and would like to hang onto it a bit past when it's due.",
    "cancel-vehicle-rental": "I haven't picked up the car yet and now I don't need it after all — please drop the arrangement.",
    "purchase-event-ticket": "I'd like to get myself a spot at the show — I don't currently hold one.",
    "transfer-event-ticket": "I already hold a spot at the show but can't make it, and want to put it in a friend's name instead.",
    "refund-event-ticket": "I already hold a spot at a show I can no longer attend and would like what I paid for it sent back to me.",
}
assert set(CALIBRATION_CLEAR_INTENTS) == {p["id"] for p in CALIBRATION_PLANS}, \
    "every calibration plan needs exactly one clear intent"

CALIBRATION_AMBIGUOUS_INTENTS: tuple[dict, ...] = (
    {"id": "cal-amb-library-item-status", "target": "renew-book-loan",
     "acceptable": ["renew-book-loan", "return-book"],
     "text": "I still have a copy of yours at home and I'm not sure yet whether I want to keep it a while longer or just bring it back now."},
    {"id": "cal-amb-reservation-change", "target": "cancel-table-reservation",
     "acceptable": ["cancel-table-reservation", "modify-table-reservation"],
     "text": "Our plans with your restaurant are up in the air — we might just let the reservation go, or we might need to shift the details instead."},
    {"id": "cal-amb-rental-duration", "target": "extend-vehicle-rental",
     "acceptable": ["extend-vehicle-rental", "cancel-vehicle-rental"],
     "text": "I have a car from you right now and I'm torn between holding onto it longer or just ending the rental early."},
    {"id": "cal-amb-ticket-cant-attend", "target": "transfer-event-ticket",
     "acceptable": ["transfer-event-ticket", "refund-event-ticket"],
     "text": "I have a ticket to an event I can no longer attend, and I haven't decided whether to pass it to someone else or just get my money back."},
)

CALIBRATION_NONE_INTENTS: tuple[str, ...] = (
    "What's a good movie to watch this weekend?",
    "Can you suggest an easy pasta recipe for dinner?",
    "Any beginner tips for starting a small vegetable garden?",
)


def _check_calibration_no_leakage() -> None:
    """Same guard as scripts/selection_sweep.py's `_check_no_leakage`, scoped to the
    calibration set: a clear intent must not string-match its own plan's id words or triggers."""
    violations = []
    for plan in CALIBRATION_PLANS:
        text_lower = CALIBRATION_CLEAR_INTENTS[plan["id"]].lower()
        for word in plan["id"].split("-"):
            if re.search(rf"\b{re.escape(word)}\b", text_lower):
                violations.append(f"{plan['id']}: id word '{word}' leaked into its clear intent")
        for trigger in plan["triggers"]:
            if trigger.lower() in text_lower:
                violations.append(f"{plan['id']}: trigger '{trigger}' leaked into its clear intent")
    if violations:
        raise AssertionError("calibration clear-intent leakage detected:\n  " + "\n  ".join(violations))


_check_calibration_no_leakage()


def _calibration_points() -> list[dict]:
    points = []
    for plan in CALIBRATION_PLANS:
        points.append({"intent_id": plan["id"], "kind": "clear", "target": plan["id"],
                        "acceptable": [plan["id"]], "intent_text": CALIBRATION_CLEAR_INTENTS[plan["id"]]})
    for amb in CALIBRATION_AMBIGUOUS_INTENTS:
        points.append({"intent_id": amb["id"], "kind": "ambiguous", "target": amb["target"],
                        "acceptable": list(amb["acceptable"]), "intent_text": amb["text"]})
    for i, text in enumerate(CALIBRATION_NONE_INTENTS):
        points.append({"intent_id": f"cal-none-{i + 1}", "kind": "none", "target": "none",
                        "acceptable": ["none"], "intent_text": text})
    return points


def _tau_grid(tau_min: float, tau_max: float, tau_step: float) -> list[float]:
    n_steps = round((tau_max - tau_min) / tau_step)
    return [round(tau_min + i * tau_step, 10) for i in range(n_steps + 1)]


def _score_chosen(kind: str, target: str, acceptable: list[str], chosen: str) -> bool:
    if kind == "clear":
        return chosen == target
    if kind == "ambiguous":
        return chosen in acceptable
    return chosen == "none"  # kind == "none"


def _evaluate_tau(embeddings: dict[tuple[str, str], list[float]], points: list[dict],
                   catalog_plans: tuple[dict, ...], tau: float) -> tuple[int, int]:
    correct = 0
    for point in points:
        intent_vec = embeddings[("intent", point["intent_id"])]
        best_id, best_sim = None, -1.0
        for plan in catalog_plans:
            sim = _cosine_similarity(intent_vec, embeddings[("plan", plan["id"])])
            if sim > best_sim:
                best_sim, best_id = sim, plan["id"]
        chosen = best_id if best_sim >= tau else "none"
        if _score_chosen(point["kind"], point["target"], point["acceptable"], chosen):
            correct += 1
    return correct, len(points)


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Grid-searches tau on the disjoint calibration set ONLY -- never imports or reads
    scripts/selection_sweep.py's dataset or results/selection-sweep/selection_sweep.jsonl."""
    provider = _build_provider(args.provider, args.embedding_model)
    embedder = CachedEmbedder(provider)

    points = _calibration_points()
    print(f"[embedding_baseline] calibrating: provider={args.provider} model={args.embedding_model} "
          f"plans={len(CALIBRATION_PLANS)} points={len(points)}")

    embeddings: dict[tuple[str, str], list[float]] = {}
    for plan in CALIBRATION_PLANS:
        embeddings[("plan", plan["id"])] = embedder.embed(_plan_text(plan))
    for point in points:
        embeddings[("intent", point["intent_id"])] = embedder.embed(point["intent_text"])
    print(f"[embedding_baseline] embedded {len(embeddings)} unique calibration texts "
          f"({embedder.hits} cache hits, {embedder.misses} new calls)")

    grid = _tau_grid(args.tau_min, args.tau_max, args.tau_step)
    if not grid:
        print("[embedding_baseline] empty tau grid -- check --tau-min/--tau-max/--tau-step", file=sys.stderr)
        return 2

    best_tau, best_accuracy = None, -1.0
    per_tau = []
    for tau in grid:  # ascending order: a strict '>' update keeps the SMALLEST tau that
        correct, total = _evaluate_tau(embeddings, points, CALIBRATION_PLANS, tau)  # reaches the max
        accuracy = correct / total if total else 0.0
        per_tau.append({"tau": tau, "accuracy": accuracy, "correct": correct, "total": total})
        if accuracy > best_accuracy + 1e-12:
            best_accuracy, best_tau = accuracy, tau

    artifact = {
        "provider": args.provider,
        "embedding_model": args.embedding_model,
        "tau": best_tau,
        "calibration_accuracy": best_accuracy,
        "tau_grid": {"min": args.tau_min, "max": args.tau_max, "step": args.tau_step, "n_values": len(grid)},
        "objective": (
            "micro-averaged accuracy (every calibration point weighted equally regardless of "
            "kind -- clear: top-1==target; ambiguous: top-1 in acceptable; none: top-1 "
            "similarity < tau) on the disjoint calibration set defined in this script "
            "(CALIBRATION_PLANS / CALIBRATION_*_INTENTS). Ties broken toward the smallest tau "
            "achieving the maximum: the grid is scanned ascending with a strict '>' update, so "
            "the first (smallest) tau to reach the max accuracy is kept -- the more conservative "
            "choice against spurious refusal on the unseen eval set."
        ),
        "calibration_set_id": _CALIBRATION_SEED_LABEL,
        "generator_seed": _CALIBRATION_SEED_LABEL,
        "calibration_set_size": len(points),
        "calibration_catalog_size": len(CALIBRATION_PLANS),
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "per_tau": per_tau,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)
    print(f"[embedding_baseline] tau={best_tau} frozen (calibration accuracy={best_accuracy:.1%}) -> {args.output}")
    return 0


# --------------------------------------------------------------------------------------
# `run`: evaluates the frozen tau against the real 240-point set. This is the ONLY function
# group that imports/uses `ss` (scripts/selection_sweep.py).
# --------------------------------------------------------------------------------------

def _load_recorded_points() -> dict[tuple[int, str], dict]:
    """Reads results/selection-sweep/selection_sweep.jsonl READ-ONLY (never written here).
    Returns {(catalog_size, intent_id): {kind, target, acceptable}} for every distinct point
    recorded there. The JSONL does not store plan description/triggers/intent_text -- it is
    used only to assert equality against the regenerated dataset, never as the text source."""
    if not _LLM_JSONL_PATH.exists():
        raise SystemExit(
            f"[embedding_baseline] recorded evidence file not found: {_LLM_JSONL_PATH}. The "
            "pre-registration requires evaluating the identical 240-point set from the LLM "
            "sweep; run scripts/selection_sweep.py first, or check out the tracked results/ "
            "directory."
        )
    points: dict[tuple[int, str], dict] = {}
    with open(_LLM_JSONL_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            key = (record["catalog_size"], record["intent_id"])
            entry = {"kind": record["kind"], "target": record["target"], "acceptable": record["acceptable"]}
            if key in points and points[key] != entry:
                raise SystemExit(
                    f"[embedding_baseline] recorded JSONL is internally inconsistent for {key}: "
                    f"{points[key]} vs {entry}"
                )
            points[key] = entry
    return points


def _assert_dataset_matches_recorded() -> None:
    """Fails loudly on ANY mismatch between scripts/selection_sweep.py's generator (SIZE_POINTS,
    deterministic and hardcoded -- no RNG to regenerate 'under a recorded seed', so equality is
    the applicable guarantee here) and every point recorded in selection_sweep.jsonl. This is
    the mechanical enforcement of "no new intents, no rewording": if SEED_BANK/CLEAR_INTENTS/
    AMBIGUOUS_INTENTS/NONE_INTENTS in selection_sweep.py ever drift from what was measured, this
    script refuses to run rather than silently evaluating a redefinition of the eval set."""
    recorded = _load_recorded_points()
    generated_index: dict[tuple[int, str], dict] = {}
    for n, points in ss.SIZE_POINTS.items():
        for p in points:
            generated_index[(n, p["intent_id"])] = {
                "kind": p["kind"], "target": p["target"], "acceptable": p["acceptable"],
            }

    mismatches = []
    for key, expected in recorded.items():
        actual = generated_index.get(key)
        if actual is None:
            mismatches.append(f"{key}: recorded in JSONL but the generator no longer produces this point")
        elif actual != expected:
            mismatches.append(f"{key}: recorded={expected} generator={actual}")
    if mismatches:
        raise SystemExit(
            "[embedding_baseline] dataset drift detected between scripts/selection_sweep.py's "
            "generator and results/selection-sweep/selection_sweep.jsonl -- refusing to run "
            "(the pre-registration requires the IDENTICAL point set):\n  " + "\n  ".join(mismatches)
        )
    if not recorded:
        raise SystemExit("[embedding_baseline] recorded JSONL has zero points -- nothing to evaluate")
    print(f"[embedding_baseline] dataset identity verified: {len(recorded)} recorded points all "
          f"match scripts/selection_sweep.py's generator")


def _load_calibration(path: Path) -> tuple[dict, str]:
    """Loads the frozen calibration artifact. Refuses to start if it is missing, or if its
    mtime is not strictly before "now" (a future mtime means it could not have been frozen
    before this run started -- clock skew or a write racing this invocation)."""
    if not path.exists():
        raise SystemExit(
            f"[embedding_baseline] calibration artifact not found: {path}. Run "
            f"`python scripts/embedding_baseline.py calibrate ...` first and freeze tau before "
            f"touching the eval set -- see docs/preregistration-embedding-baseline.md."
        )
    raw = path.read_bytes()
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    mtime = path.stat().st_mtime
    if mtime > time.time():
        raise SystemExit(
            f"[embedding_baseline] {path} has a future mtime -- it cannot have been frozen "
            "before this run started; refusing (tau must predate the eval run it governs)."
        )
    calibration = json.loads(raw.decode("utf-8"))
    return calibration, artifact_sha256


def _load_existing(path: Path) -> tuple[set[tuple], list[dict]]:
    """Duplicated from scripts/selection_sweep.py's `_load_existing` rather than importing a
    private name -- same convention that module already documents for _CATALOG_ID_RE."""
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


def _assert_calibration_unchanged(existing_records: list[dict], artifact_sha256: str, calibration_path: Path) -> None:
    """If the output JSONL already has records, every one of them must carry the SAME
    calibration artifact hash as the one about to be used. A mismatch means the calibration
    artifact was overwritten (recalibrated) between runs -- appending more records now would
    silently mix eval points measured under different frozen thresholds into one file."""
    stale = {r.get("calibration_sha256") for r in existing_records} - {artifact_sha256}
    if stale:
        raise SystemExit(
            f"[embedding_baseline] {calibration_path} has changed since earlier records in "
            f"{_EVAL_JSONL_PATH} were measured (recorded hash(es): {sorted(h[:12] for h in stale)!r}, "
            f"current hash: {artifact_sha256[:12]!r}). Recalibrating tau mid-run would mix eval "
            "records measured under different frozen thresholds into one file -- refusing. Start "
            "a fresh --calibration and a fresh output file if you intend a new calibration; never "
            "overwrite an existing calibration artifact in place."
        )


def _run_point(embedder: CachedEmbedder, tau: float, model_label: str, provider_name: str, rep: int,
               catalog_size: int, catalog_plans: list[dict], offered_ids: set[str], point: dict,
               calibration_sha256: str) -> tuple[dict, int]:
    misses_before = embedder.misses
    started = time.monotonic()

    intent_vec = embedder.embed(point["intent_text"])
    best_id, best_sim = None, -1.0
    for plan in catalog_plans:
        sim = _cosine_similarity(intent_vec, embedder.embed(_plan_text(plan)))
        if sim > best_sim:
            best_sim, best_id = sim, plan["id"]
    chosen = best_id if best_sim >= tau else "none"
    latency_ms = (time.monotonic() - started) * 1000

    correct = _score_chosen(point["kind"], point["target"], point["acceptable"], chosen)
    format_violation = chosen != "none" and chosen not in offered_ids

    # No usage/token field is returned by embedContent; this is a character-based heuristic
    # (same convention as infrastructure/providers/local_client.py's LocalModelClient) kept
    # ONLY for cost-shape parity with selection_sweep_report.json's "grows with N" story --
    # never billed usage.
    input_tokens = sum(len(t) // 4 for t in [point["intent_text"]] + [_plan_text(p) for p in catalog_plans])

    record = {
        "model": model_label,
        "provider": provider_name,
        "rep": rep,
        "catalog_size": catalog_size,
        "intent_id": point["intent_id"],
        "kind": point["kind"],
        "target": point["target"],
        "acceptable": point["acceptable"],
        "chosen": chosen,
        "similarity": best_sim,
        "tau": tau,
        "correct": correct,
        "format_violation": format_violation,
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "latency_ms": latency_ms,
        "calibration_sha256": calibration_sha256,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return record, embedder.misses - misses_before


def _run_eval(embedder: CachedEmbedder, tau: float, model_label: str, provider_name: str, reps: int,
              max_calls: int, calibration_sha256: str, calibration_path: Path) -> None:
    _EVAL_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing_keys, existing_records = _load_existing(_EVAL_JSONL_PATH)
    _assert_calibration_unchanged(existing_records, calibration_sha256, calibration_path)

    total_points_per_rep = sum(len(points) for points in ss.SIZE_POINTS.values())
    total_expected = reps * total_points_per_rep
    print(f"[embedding_baseline] provider={provider_name} model={model_label} tau={tau} reps={reps} "
          f"max_calls={max_calls} points_per_rep={total_points_per_rep} total_expected={total_expected}")

    already_done = sum(1 for k in existing_keys if k[0] == model_label)
    if already_done:
        print(f"[embedding_baseline] resume: {already_done} points already measured for "
              f"model={model_label}, skipping them")

    calls_made = 0
    stopped_early = False
    with open(_EVAL_JSONL_PATH, "a", encoding="utf-8") as fh:
        for rep in range(1, reps + 1):
            if stopped_early:
                break
            for n in ss.CATALOG_SIZES:
                if stopped_early:
                    break
                catalog_plans = list(ss.SEED_BANK[:n])
                offered_ids = {p["id"] for p in catalog_plans}
                for point in ss.SIZE_POINTS[n]:
                    key = (model_label, rep, n, point["intent_id"])
                    if key in existing_keys:
                        continue
                    if calls_made >= max_calls:
                        stopped_early = True
                        break
                    record, new_calls = _run_point(embedder, tau, model_label, provider_name, rep, n,
                                                     catalog_plans, offered_ids, point, calibration_sha256)
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    fh.flush()
                    existing_keys.add(key)
                    calls_made += new_calls
                    status = "OK" if record["correct"] else ("FMT" if record["format_violation"] else "MISS")
                    print(f"  rep={rep} N={n:>2} {point['kind']:<9} {point['intent_id']:<30} "
                          f"chosen={record['chosen']!r:<32} sim={record['similarity']:.3f} {status}")

    print(f"[embedding_baseline] made {calls_made} new embedding calls this run "
          f"({embedder.hits} cumulative cache hits, {embedder.misses} cumulative new calls)")
    if stopped_early:
        remaining = total_expected - sum(1 for k in existing_keys if k[0] == model_label)
        print(f"[embedding_baseline] budget guard: stopped at --max-calls={max_calls}. "
              f"{remaining} data points still unmeasured for model={model_label} -- rerun to continue.")


# --------------------------------------------------------------------------------------
# Reporting -- duplicated + adapted from scripts/selection_sweep.py's `_kind_stats` /
# `_build_report` / `_print_report` (same records shape: model/catalog_size/kind/correct/
# format_violation/input_tokens/output_tokens/latency_ms) rather than importing private names
# bound to that module's own JSONL path. Kept byte-for-byte identical in output SHAPE.
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
        "source_jsonl": _EVAL_JSONL_PATH.relative_to(_REPO_ROOT).as_posix(),
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
    _EVAL_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_EVAL_REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n[embedding_baseline] wrote {_EVAL_REPORT_PATH}")


def cmd_run(args: argparse.Namespace) -> int:
    calibration, calibration_sha256 = _load_calibration(args.calibration)
    if calibration["provider"] != args.provider or calibration["embedding_model"] != args.embedding_model:
        raise SystemExit(
            f"[embedding_baseline] --provider/--embedding-model ({args.provider}/"
            f"{args.embedding_model}) do not match the frozen calibration "
            f"({calibration['provider']}/{calibration['embedding_model']}) in {args.calibration}. "
            "tau was chosen for a specific model; refusing to apply it to a different one."
        )
    tau = calibration["tau"]
    print(f"[embedding_baseline] loaded frozen tau={tau} from {args.calibration} "
          f"(calibrated_at={calibration['calibrated_at']}, calibration_accuracy="
          f"{calibration['calibration_accuracy']:.1%}, sha256={calibration_sha256[:12]}...)")

    _assert_dataset_matches_recorded()

    provider = _build_provider(args.provider, args.embedding_model)
    embedder = CachedEmbedder(provider)
    model_label = args.embedding_model

    if not args.report_only:
        _run_eval(embedder, tau, model_label, args.provider, args.reps, args.max_calls,
                  calibration_sha256, args.calibration)

    _, all_records = _load_existing(_EVAL_JSONL_PATH)
    if not all_records:
        print(f"[embedding_baseline] no records in {_EVAL_JSONL_PATH} -- nothing to report")
        return 0
    report = _build_report(all_records)
    _print_report(report)
    _write_report(report)
    return 0


# --------------------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="embedding_baseline.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    calibrate = subparsers.add_parser(
        "calibrate",
        help="grid-search tau on a disjoint synthetic set and freeze it to a JSON artifact "
             "(must run before 'run'; never touches the eval set)",
    )
    calibrate.add_argument("--provider", choices=["gemini", "local"], required=True,
                            help="embedding backend")
    calibrate.add_argument("--embedding-model", required=True,
                            help="embedding model id (e.g. gemini-embedding-001 for gemini, or a "
                                 "sentence-transformers model name for local). No default: the "
                                 "pre-registration requires this to be a conscious, recorded "
                                 "choice, never a silent default.")
    calibrate.add_argument("--tau-min", type=float, default=0.05, help="grid search lower bound (default: 0.05)")
    calibrate.add_argument("--tau-max", type=float, default=0.95, help="grid search upper bound (default: 0.95)")
    calibrate.add_argument("--tau-step", type=float, default=0.01, help="grid search step (default: 0.01)")
    calibrate.add_argument("--output", type=Path, default=_CALIBRATION_PATH_DEFAULT,
                            help=f"where to write the frozen calibration artifact "
                                 f"(default: {_CALIBRATION_PATH_DEFAULT.relative_to(_REPO_ROOT).as_posix()})")
    calibrate.set_defaults(func=cmd_calibrate)

    run = subparsers.add_parser(
        "run",
        help="evaluate the frozen tau against the identical 240-point eval set (this subcommand "
             "has no --tau flag -- tau can only come from --calibration)",
    )
    run.add_argument("--provider", choices=["gemini", "local"], required=True, help="embedding backend")
    run.add_argument("--embedding-model", required=True,
                      help="must match the model recorded in --calibration (checked; refuses to start otherwise)")
    run.add_argument("--calibration", type=Path, default=_CALIBRATION_PATH_DEFAULT,
                      help="frozen calibration artifact written by 'calibrate' (default: "
                           f"{_CALIBRATION_PATH_DEFAULT.relative_to(_REPO_ROOT).as_posix()}); "
                           "run refuses to start if this file does not exist")
    run.add_argument("--reps", type=int, default=1,
                      help="repetitions of the eval sweep (default: 1 -- embedding selection is "
                           "deterministic given (model, text), so extra reps are served entirely "
                           "from the on-disk cache; kept for report-shape parity with "
                           "selection_sweep.py's --reps)")
    run.add_argument("--max-calls", type=int, default=1000,
                      help="budget guard: stop cleanly after this many NEW (cache-miss) embedding "
                           "calls in this run (default: 1000)")
    run.add_argument("--report-only", action="store_true",
                      help="skip the eval sweep; just rebuild the console table and report JSON "
                           "from the existing JSONL")
    run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.command == "calibrate":
        if args.tau_step <= 0:
            print(f"[embedding_baseline] --tau-step must be > 0 (got {args.tau_step})", file=sys.stderr)
            return 2
        if args.tau_min > args.tau_max:
            print(f"[embedding_baseline] --tau-min must be <= --tau-max "
                  f"(got {args.tau_min} > {args.tau_max})", file=sys.stderr)
            return 2
    elif args.command == "run":
        if args.reps < 1:
            print(f"[embedding_baseline] --reps must be >= 1 (got {args.reps})", file=sys.stderr)
            return 2
        if args.max_calls < 0:
            print(f"[embedding_baseline] --max-calls must be >= 0 (got {args.max_calls})", file=sys.stderr)
            return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
