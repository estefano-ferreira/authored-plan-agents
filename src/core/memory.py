"""Agent memory contracts: short-term session and long-term knowledge."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryPolicy:
    """How much session history and knowledge an agent uses to assemble context."""
    session_last_n_turns: int = 10
    session_ttl_seconds: int = 1800
    knowledge_enabled: bool = False
    knowledge_top_k: int = 3


class ISessionStore(ABC):
    """Short-term memory: append-only per session, never rewrites a turn already recorded."""

    @abstractmethod
    def append(self, session_id: str, entry: dict) -> None: ...

    @abstractmethod
    def history(self, session_id: str) -> tuple[dict, ...]: ...


class SessionRewriteError(Exception):
    """Raised when attempting to rewrite a turn already recorded in a session."""


class IKnowledgeStore(ABC):
    """Long-term, cross-session memory, retrievable via semantic search."""

    @abstractmethod
    def upsert(self, doc_id: str, text: str, metadata: dict) -> None: ...

    @abstractmethod
    def search(self, query: str, top_k: int) -> tuple[dict, ...]: ...


@dataclass(frozen=True)
class SessionRecord:
    """Persistable record of a single session turn."""
    session_id: str
    turn_index: int
    entry: dict
    created_at: float


@dataclass(frozen=True)
class MemoryRecord:
    """Persistable record of a single knowledge document."""
    doc_id: str
    text: str
    metadata: dict
