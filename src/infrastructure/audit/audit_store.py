"""Business audit trail: append-only JSONL, never sampled."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.ports import AuditEvent, IAuditWriter

_DEFAULT_PATH = Path("var/audit.jsonl")


class JsonlAuditWriter(IAuditWriter):
    """Writes one JSON per line (ISO timestamp + AuditEvent fields) to `var/audit.jsonl`."""

    def __init__(self, path: str | Path = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, e: AuditEvent) -> None:
        line = {"timestamp": datetime.now(timezone.utc).isoformat(), **asdict(e)}
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def read_all(self) -> tuple[dict, ...]:
        """Reads all events recorded so far. Used by the tests."""
        if not self._path.exists():
            return ()
        with open(self._path, "r", encoding="utf-8") as fh:
            return tuple(json.loads(line) for line in fh if line.strip())


class PostgresAuditWriter(IAuditWriter):
    """Business audit trail on Postgres: one INSERT per record, never sampled, never updated."""

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine

        from infrastructure.memory.models import AuditRecord, Base

        self._engine = create_engine(database_url)
        self._record_model = AuditRecord
        Base.metadata.create_all(self._engine, tables=[AuditRecord.__table__], checkfirst=True)

    def record(self, e: AuditEvent) -> None:
        from sqlalchemy.orm import Session

        AuditRecord = self._record_model
        timestamp = datetime.now(timezone.utc).isoformat()
        with Session(self._engine) as session:
            session.add(AuditRecord(
                ts=timestamp,
                execution_id=e.execution_id,
                plan_id=e.plan_id,
                step_id=e.step_id,
                principal_id=e.principal_id,
                kind=e.kind,
                detail=dict(e.detail),
            ))
            session.commit()

    def read_all(self) -> tuple[dict, ...]:
        """Reads all events, in the same dict shape as `JsonlAuditWriter.read_all` (timestamp + AuditEvent fields)."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        AuditRecord = self._record_model
        with Session(self._engine) as session:
            rows = session.execute(select(AuditRecord).order_by(AuditRecord.id)).scalars().all()
        return tuple(
            {
                "timestamp": r.ts,
                "execution_id": r.execution_id,
                "plan_id": r.plan_id,
                "step_id": r.step_id,
                "principal_id": r.principal_id,
                "kind": r.kind,
                "detail": r.detail,
            }
            for r in rows
        )
