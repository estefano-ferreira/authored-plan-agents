"""SQLAlchemy 2.0 table definitions for platform persistence (Postgres + pgvector).

Only imported on the Postgres path (`PLATFORM_DATABASE_URL` set); the local/in-memory
fallbacks never touch this module, so the `pgvector` dependency stays optional at runtime.
"""
from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pgvector.sqlalchemy import Vector

_EMBEDDING_DIM = 256


class Base(DeclarativeBase):
    """Declarative base shared by all platform persistence tables."""


class SessionEntry(Base):
    """One row per session turn: append-only, PK (session_id, turn_index) enforces no rewrites."""

    __tablename__ = "session_entries"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    turn_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class KnowledgeDoc(Base):
    """One row per knowledge document, with its embedding for cosine-distance search."""

    __tablename__ = "knowledge_docs"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    embedding: Mapped[list] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)


class AuditRecord(Base):
    """One row per audit event: append-only, complete, never sampled."""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    execution_id: Mapped[str] = mapped_column(String, nullable=False)
    plan_id: Mapped[str] = mapped_column(String, nullable=False)
    step_id: Mapped[str] = mapped_column(String, nullable=False)
    principal_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
