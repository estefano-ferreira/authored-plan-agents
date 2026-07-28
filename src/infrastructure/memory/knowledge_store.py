"""Long-term memory: semantic search via a deterministic bag-of-words hashing embedding (no numpy).

The embedder (`embed_text`) is shared by the local in-memory store and the Postgres/pgvector
store below, so both index the exact same vector space and are drop-in interchangeable.
"""
import hashlib
import math
import re

from core.memory import IKnowledgeStore

EMBEDDING_DIM = 256
_DIM = EMBEDDING_DIM
_TOKEN_RE = re.compile(r"[a-zA-Zà-üÀ-Ü0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed_text(text: str) -> list[float]:
    """Bag-of-words with hashing: each token increments a fixed position by hash(token) % dim."""
    vector = [0.0] * _DIM
    for token in _tokenize(text):
        index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % _DIM
        vector[index] += 1.0
    return vector


# Backward-compatible alias (module-private name used before the store was factored out).
_embed = embed_text


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class LocalVectorKnowledgeStore(IKnowledgeStore):
    """Local in-memory vector index: deterministic embedding + cosine similarity."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def upsert(self, doc_id: str, text: str, metadata: dict) -> None:
        self._docs[doc_id] = {"text": text, "metadata": dict(metadata), "vector": _embed(text)}

    def search(self, query: str, top_k: int) -> tuple[dict, ...]:
        query_vector = _embed(query)
        scored = [
            {
                "doc_id": doc_id,
                "text": doc["text"],
                "score": _cosine(query_vector, doc["vector"]),
                "metadata": doc["metadata"],
            }
            for doc_id, doc in self._docs.items()
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        return tuple(scored[:top_k])


class PgVectorKnowledgeStore(IKnowledgeStore):
    """Postgres + pgvector vector index: same deterministic embedding, cosine distance in SQL.

    Returns the exact same shape as `LocalVectorKnowledgeStore.search` — {"doc_id", "text",
    "score", "metadata"} — so callers (`ai/context.py`) are indifferent to which store is wired.
    """

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine, text as sql_text

        from infrastructure.memory.models import Base, KnowledgeDoc

        self._engine = create_engine(database_url)
        self._doc_model = KnowledgeDoc
        with self._engine.begin() as conn:
            conn.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(self._engine, tables=[KnowledgeDoc.__table__], checkfirst=True)

    def upsert(self, doc_id: str, text: str, metadata: dict) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.orm import Session

        KnowledgeDoc = self._doc_model
        vector = embed_text(text)
        stmt = pg_insert(KnowledgeDoc).values(
            doc_id=doc_id, text=text, doc_metadata=dict(metadata), embedding=vector,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[KnowledgeDoc.doc_id],
            set_={"text": stmt.excluded.text, "doc_metadata": stmt.excluded.doc_metadata,
                  "embedding": stmt.excluded.embedding},
        )
        with Session(self._engine) as session:
            session.execute(stmt)
            session.commit()

    def search(self, query: str, top_k: int) -> tuple[dict, ...]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        KnowledgeDoc = self._doc_model
        query_vector = embed_text(query)
        distance = KnowledgeDoc.embedding.cosine_distance(query_vector)
        with Session(self._engine) as session:
            rows = session.execute(
                select(KnowledgeDoc, distance.label("distance"))
                .order_by(distance)
                .limit(top_k)
            ).all()
        return tuple(
            {
                "doc_id": doc.doc_id,
                "text": doc.text,
                "score": 1.0 - float(distance_value),
                "metadata": doc.doc_metadata,
            }
            for doc, distance_value in rows
        )
