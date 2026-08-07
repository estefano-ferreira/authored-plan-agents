"""Text-based observability: prints events and accumulates metrics/cost per execution."""
from core.ports import IObservabilityWriter, ModelCallEvent, StepEvent

# price per model in USD per million tokens; cache_read billed at 10% of the input price.
PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-3.6-flash": {"input": 1.50, "output": 7.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    # Meta (Llama API) pricing is pay-as-you-go but the exact list rates are behind the account
    # dashboard and were not available at the time this entry was added: prices are account-gated
    # and not yet entered; cost figures for this model are recorded as $0 and MUST NOT be
    # published until real list rates replace these zeros.
    "muse-spark-1.2": {"input": 0.0, "output": 0.0},
    # Groq-served, rates as recorded 2026-08-06 from public pricing pages (pre-registration
    # amendment in docs/preregistration-crossmodel-integrity-AB.md). Caveat on cache: Groq's own
    # cached-input discount is 50%, not this module's 10% constant -- moot while recorded cache
    # reads are zero (all runs to date), and verify_costs.py exposes any nonzero cache exactly.
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "local-deterministic": {"input": 0.0, "output": 0.0},
    # OpenRouter and Groq have no entries here by design: both front many underlying model
    # families (Llama, Qwen, Claude, OpenAI's open-weights models, ...) under caller-chosen ids
    # (see OpenRouterModelClient and GroqModelClient, neither of which has a default model), and
    # each family's real list rate must be entered per pre-registration amendment for the specific
    # model that experiment names -- never assumed from another provider's table, and never
    # assumed to be the same across the two providers even for the same model id. Until an entry
    # exists for a given OpenRouter/Groq model id, `_cost_usd`'s `.get(model, {"input": 0.0,
    # "output": 0.0})` fallback records its cost as $0, and that $0 figure MUST NOT be published
    # as a real cost.
}
_CACHE_READ_DISCOUNT = 0.10


def _cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    prices = PRICING_USD_PER_MILLION_TOKENS.get(model, {"input": 0.0, "output": 0.0})
    billable_input = max(input_tokens - cache_read_tokens, 0)
    cost = billable_input / 1_000_000 * prices["input"]
    cost += output_tokens / 1_000_000 * prices["output"]
    cost += cache_read_tokens / 1_000_000 * prices["input"] * _CACHE_READ_DISCOUNT
    return cost


class ConsoleObservabilityWriter(IObservabilityWriter):
    """Prints one line per event (`[obs] ...`) and accumulates metrics by execution_id."""

    def __init__(self) -> None:
        self._metrics: dict[str, dict] = {}

    def _bucket(self, execution_id: str) -> dict:
        return self._metrics.setdefault(
            execution_id,
            {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "step_latency_ms": {},
            },
        )

    def model_call(self, e: ModelCallEvent) -> None:
        print(
            f"[obs] model_call execution={e.execution_id} step={e.step_id} model={e.model} "
            f"purpose={e.purpose} in={e.input_tokens} out={e.output_tokens} "
            f"cache_read={e.cache_read_tokens} latency_ms={e.latency_ms:.1f}"
        )
        bucket = self._bucket(e.execution_id)
        bucket["model_calls"] += 1
        bucket["input_tokens"] += e.input_tokens
        bucket["output_tokens"] += e.output_tokens
        bucket["cache_read_tokens"] += e.cache_read_tokens
        bucket["cost_usd"] += _cost_usd(e.model, e.input_tokens, e.output_tokens, e.cache_read_tokens)

    def step(self, e: StepEvent) -> None:
        print(
            f"[obs] step execution={e.execution_id} plan={e.plan_id} step={e.step_id} "
            f"agent={e.agent} status={e.status} latency_ms={e.latency_ms:.1f}"
        )
        bucket = self._bucket(e.execution_id)
        bucket["step_latency_ms"][e.step_id] = e.latency_ms

    def trace(self, execution_id: str, payload: dict) -> None:
        print(f"[obs] trace execution={execution_id} payload={payload}")

    def summary(self, execution_id: str) -> dict:
        """Accumulated metrics for an execution: calls, tokens, cache, per-step latency and cost."""
        bucket = self._metrics.get(execution_id)
        if bucket is None:
            return {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "step_latency_ms": {},
            }
        return dict(bucket, step_latency_ms=dict(bucket["step_latency_ms"]))


class CompositeObservabilityWriter(IObservabilityWriter):
    """Fan-out: forwards each event to multiple writers (e.g. console + LangSmith)."""

    def __init__(self, *writers: IObservabilityWriter) -> None:
        self._writers = writers

    def model_call(self, e: ModelCallEvent) -> None:
        for writer in self._writers:
            writer.model_call(e)

    def step(self, e: StepEvent) -> None:
        for writer in self._writers:
            writer.step(e)

    def trace(self, execution_id: str, payload: dict) -> None:
        for writer in self._writers:
            writer.trace(execution_id, payload)
