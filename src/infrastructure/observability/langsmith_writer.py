"""Observability via the LangSmith SDK (RunTree), without LangChain.

No-op (fully silent) whenever `LANGSMITH_API_KEY` is unset, and permanently degrades to
no-op (after one warning) if the SDK raises at runtime -- tracing is best-effort measurement
tooling and must never affect the executions it observes. `ConsoleObservabilityWriter` is the
one writer the study's metrics ever depend on; this writer only adds optional, richer tracing
on top via `CompositeObservabilityWriter`.

Span hierarchy per execution (`execution_id`)
----------------------------------------------
- **root** ("execution:<execution_id>"): one per execution, created lazily on the very first
  event seen for that `execution_id` (whichever comes first: a `ModelCallEvent` or a
  `StepEvent`), so its `start_time` is backdated to that first event's own start rather than
  "whenever the writer happened to notice it".
- **step spans** ("step:<agent>:<step_id>"), one per `StepEvent`, nested directly under root.
  A `StepEvent` only arrives *after* its step (and everything the step did) has finished --
  it carries `latency_ms` -- so the span is created at that point but backdated with
  `start_time = event_time - latency_ms`.
- **model-call spans** ("model_call:<purpose>:<step_id>"), one per `ModelCallEvent`, nested
  under the step during which the call happened.

The "capability" level of the platform's own hierarchy (agent -> capability selection ->
capability execution; see `ai/agents/runtime.py::AgentRuntime._select_capability`/`_execute`)
is *not* a distinct span here: it shows up as the step span plus, nested inside it, the
'selection' model-call span (`ModelCallEvent.step_id == "selection"`) alongside the
capability's own generation call(s) -- i.e. the step span *is* the capability-selection +
capability-execution unit.

Buffering (why it's needed): a `ModelCallEvent` always arrives *before* the `StepEvent` of the
plan step it happened during (the step isn't "done" until after the call returns), so the
step span this call belongs to does not exist yet when the call is observed. Every model call
is therefore buffered per `execution_id` until the *next* `StepEvent` for that execution
arrives; at that point the step span is created and every call buffered since the *previous*
`StepEvent` (or since the start of the execution, for the first step) is attached under it as
a child -- this is what nests capability-selection + capability-generation calls under the
correct plan step. One exception: `ModelCallEvent.step_id == "plan-selection"` is the single
call `Orchestrator._resolve_plan` makes to pick the plan itself, before the step loop even
starts; it is attached directly under root instead of being buffered, since it can never
belong to any step. Any calls still buffered when the execution ends (e.g. from the runtime's
intra-capability compensation path, which is not wrapped in its own `StepEvent`) are flushed
directly under root as a fallback so nothing is silently dropped.

Prompt/response/token correlation
----------------------------------
The constructor optionally takes `model_log`, the *live* list a `RecordingModelClient` is
still appending to. Each `ModelCallEvent` consumes the *next not-yet-consumed* entry of that
list via a single monotonically increasing cursor (`self._log_cursor`). This index-based
correlation (rather than matching on request/response content) is only valid because a
platform execution is single-threaded and synchronous end to end: `RecordingModelClient`
appends to its log the instant `complete()` returns, strictly before the caller (the
orchestrator or `AgentRuntime._MeasuringModel`) computes latency and emits the corresponding
`ModelCallEvent` -- so log entries and `ModelCallEvent`s are produced in exactly the same
order, one to one, with no interleaving possible. Without `model_log` (measurement runs that
don't record calls), spans still carry full token/metadata, just no `prompt`/`content`.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from core.ports import IObservabilityWriter, ModelCallEvent, StepEvent

_logger = logging.getLogger(__name__)

_DEFAULT_PROJECT = "agent-platform-reference"
_PLAN_SELECTION_STEP_ID = "plan-selection"


class LangSmithObservabilityWriter(IObservabilityWriter):
    """See the module docstring for the span hierarchy, buffering strategy and log correlation."""

    def __init__(self, model_log: list | None = None) -> None:
        self._enabled = bool(os.environ.get("LANGSMITH_API_KEY"))
        self._project = os.environ.get("LANGSMITH_PROJECT", _DEFAULT_PROJECT)
        self._model_log = model_log
        self._log_cursor = 0
        # execution_id -> root RunTree. Private (not part of IObservabilityWriter) but kept
        # around deliberately: it is what lets a test structurally inspect the in-memory span
        # tree (`.child_runs`, `.tags`, ...) without ever posting to the real API. To exercise
        # that path without a real LANGSMITH_API_KEY, the simplest option is to build the
        # writer normally and then monkeypatch `writer._enabled = True` -- every method below
        # re-checks `_enabled` on its own, so that single flag is enough to flip the writer
        # from "fully no-op" to "fully active" for a test.
        self._roots: dict[str, object] = {}
        # execution_id -> list of (ModelCallEvent, log_entry|None, start_time, end_time) not yet
        # attached to a step span; see "Buffering" in the module docstring.
        self._pending: dict[str, list[tuple]] = {}

    # -- IObservabilityWriter -----------------------------------------------------------

    def model_call(self, e: ModelCallEvent) -> None:
        if not self._enabled:
            return
        try:
            log_entry = self._next_log_entry()
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(milliseconds=max(e.latency_ms, 0.0))
            if e.step_id == _PLAN_SELECTION_STEP_ID:
                root = self._get_or_create_root(e.execution_id, start_time)
                self._materialize_model_call(root, e, log_entry, start_time, end_time)
            else:
                self._get_or_create_root(e.execution_id, start_time)
                self._pending.setdefault(e.execution_id, []).append((e, log_entry, start_time, end_time))
        except Exception as exc:  # noqa: BLE001 - observability never brings down the main flow
            self._disable_after_error("model_call", exc)

    def step(self, e: StepEvent) -> None:
        if not self._enabled:
            return
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(milliseconds=max(e.latency_ms, 0.0))
            root = self._get_or_create_root(e.execution_id, start_time)
            step_span = root.create_child(
                name=f"step:{e.agent}:{e.step_id}",
                run_type="chain",
                inputs={"execution_id": e.execution_id, "plan_id": e.plan_id, "agent": e.agent, "step_id": e.step_id},
                outputs={"status": e.status, "latency_ms": e.latency_ms},
                start_time=start_time,
                end_time=end_time,
                tags=[f"agent:{e.agent}", f"step:{e.step_id}", f"status:{e.status}"],
            )
            self._post(step_span)
            for call_event, log_entry, call_start, call_end in self._pending.pop(e.execution_id, []):
                self._materialize_model_call(step_span, call_event, log_entry, call_start, call_end)
        except Exception as exc:  # noqa: BLE001
            self._disable_after_error("step", exc)

    def trace(self, execution_id: str, payload: dict) -> None:
        if not self._enabled:
            return
        try:
            root = self._get_or_create_root(execution_id)
            self._flush_pending_to_root(execution_id, root)
            if root.end_time is None:
                root.end(
                    outputs=dict(payload),
                    metadata={"plan": payload.get("plan"), "status": payload.get("status")},
                )
            self._patch(root)
        except Exception as exc:  # noqa: BLE001
            self._disable_after_error("trace", exc)

    # -- extension: closes the root with filterable tags; called once per execution by the
    # --repeat runner (scripts/run_plans.py). Not part of IObservabilityWriter -- callers reach
    # it via Platform.langsmith_obs directly.

    def finalize_execution(self, execution_id: str, tags: dict) -> None:
        """Patches the root trace's tags/metadata; never creates or re-posts the root run.

        Expected `tags` keys: `plan_id`, `selected_capability`, `provider`, `run_index`,
        `output_contract_violation` (bool). Every key is rendered as a filterable "key:value"
        tag string, except `output_contract_violation`: its tag ("output_contract_violation")
        is added only when the value is True, but the boolean itself is always recorded in full
        in the root's metadata regardless of value.

        Intended to run *after* `trace()` has already run for the same `execution_id` --
        `trace()` closes (end + patch) the root as a legacy fallback for callers that never call
        this method at all, so under the normal --repeat flow this only issues one more
        `patch()` on top of the already-posted root; it never duplicates the run. If called
        without a preceding `trace()` (e.g. a caller skips it), the root is closed here instead.

        No-op, silently, if disabled or if no root exists yet for `execution_id`.
        """
        if not self._enabled:
            return
        root = self._roots.get(execution_id)
        if root is None:
            return
        try:
            self._flush_pending_to_root(execution_id, root)
            if root.end_time is None:
                root.end(outputs={"status": "finalized"})
            tag_list = [f"{key}:{value}" for key, value in tags.items() if key != "output_contract_violation"]
            if tags.get("output_contract_violation"):
                tag_list.append("output_contract_violation:true")
            root.add_tags(tag_list)
            root.add_metadata({"finalize_tags": dict(tags)})
            self._patch(root)
        except Exception as exc:  # noqa: BLE001
            self._disable_after_error("finalize_execution", exc)
        finally:
            # Bounds memory for long --repeat runs; safe once finalize has run since nothing
            # else will reference this execution_id's root/pending buffer again.
            self._roots.pop(execution_id, None)
            self._pending.pop(execution_id, None)

    # -- internals ------------------------------------------------------------------------

    def _next_log_entry(self) -> dict | None:
        """Consumes the next not-yet-seen `model_log` entry by index; see module docstring."""
        if self._model_log is None or self._log_cursor >= len(self._model_log):
            return None
        entry = self._model_log[self._log_cursor]
        self._log_cursor += 1
        return entry

    def _get_or_create_root(self, execution_id: str, hint_start_time: datetime | None = None):
        root = self._roots.get(execution_id)
        if root is not None:
            return root
        from langsmith.run_trees import RunTree

        root = RunTree(
            name=f"execution:{execution_id}",
            run_type="chain",
            inputs={"execution_id": execution_id},
            start_time=hint_start_time or datetime.now(timezone.utc),
            project_name=self._project,
        )
        self._post(root)
        self._roots[execution_id] = root
        self._pending.setdefault(execution_id, [])
        return root

    def _materialize_model_call(self, parent, e: ModelCallEvent, log_entry, start_time, end_time) -> None:
        """Creates the model-call span as a child of `parent` (root, for plan-selection; a step
        span otherwise) and posts it. Carries prompt/response only when `log_entry` (the
        correlated `model_log` entry) is available; tokens/purpose/model always go to metadata."""
        inputs: dict = {"purpose": e.purpose, "step_id": e.step_id}
        outputs: dict = {}
        if log_entry is not None:
            inputs["prompt"] = log_entry.get("prompt")
            outputs["content"] = log_entry.get("content")
        metadata = {
            "purpose": e.purpose,
            "model": e.model,
            "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens,
            "cache_read_tokens": e.cache_read_tokens,
        }
        span = parent.create_child(
            name=f"model_call:{e.purpose}:{e.step_id}",
            run_type="llm",
            inputs=inputs,
            outputs=outputs,
            start_time=start_time,
            end_time=end_time,
            extra={"metadata": metadata},
            tags=[f"purpose:{e.purpose}", f"model:{e.model}"],
        )
        self._post(span)

    def _flush_pending_to_root(self, execution_id: str, root) -> None:
        """Attaches any model calls never claimed by a step span directly under root (fallback)."""
        for call_event, log_entry, call_start, call_end in self._pending.pop(execution_id, []):
            self._materialize_model_call(root, call_event, log_entry, call_start, call_end)
        self._pending[execution_id] = []

    def _post(self, run) -> None:
        if not self._enabled:
            return
        try:
            run.post()
        except Exception as exc:  # noqa: BLE001
            self._disable_after_error("post", exc)

    def _patch(self, run) -> None:
        if not self._enabled:
            return
        try:
            run.patch()
        except Exception as exc:  # noqa: BLE001
            self._disable_after_error("patch", exc)

    def _disable_after_error(self, where: str, exc: Exception) -> None:
        """Permanently degrades to no-op after any SDK failure; every public method re-checks
        `_enabled` before doing anything, so this is only ever reached (and only ever warns)
        once per writer instance."""
        self._enabled = False
        _logger.warning(
            "LangSmithObservabilityWriter: disabling after error in %s (%s); degrading to no-op for the rest of the run",
            where, exc,
        )
