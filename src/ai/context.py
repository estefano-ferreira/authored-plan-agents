"""Assembly of an agent's context: instructions, knowledge, session history and current payload."""
import json


class ContextBuilder:
    """Assembles the messages sent to the model, compacting by token budget when necessary."""

    def __init__(self, session_store, knowledge_store):
        self.session_store = session_store
        self.knowledge_store = knowledge_store

    def build(self, agent_spec, session_id: str, payload: dict, token_budget: int) -> tuple[dict, ...]:
        """agent instructions + knowledge (if `memory_policy.knowledge_enabled`) + last N turns + payload."""
        head = [{"role": "system", "content": agent_spec.instructions}]
        policy = agent_spec.memory_policy
        if policy.knowledge_enabled:
            query = json.dumps(payload, sort_keys=True, default=str)
            for doc in self.knowledge_store.search(query, policy.knowledge_top_k):
                head.append({"role": "system", "content": f"[knowledge] {doc.get('text', '')}"})
        history = self.session_store.history(session_id)
        recent = history[-policy.session_last_n_turns:] if policy.session_last_n_turns else ()
        turns = [{"role": "user", "content": json.dumps(turn, sort_keys=True, default=str)} for turn in recent]
        tail = [{"role": "user", "content": json.dumps(payload, sort_keys=True, default=str)}]
        turns = self._compact(head, turns, tail, token_budget)
        return tuple(head + turns + tail)

    @staticmethod
    def _compact(head: list[dict], turns: list[dict], tail: list[dict], token_budget: int) -> list[dict]:
        """Estimate `len(text)//4`; drops oldest turns first, preserving the prefix and current payload."""
        def tokens(messages: list[dict]) -> int:
            return sum(len(m["content"]) // 4 for m in messages)

        remaining = list(turns)
        while remaining and tokens(head + remaining + tail) > token_budget:
            remaining.pop(0)
        return remaining
