"""Short-term memory: append-only per session, with TTL. Redis if REDIS_URL is defined."""
import logging
import time

from core.memory import ISessionStore, SessionRecord, SessionRewriteError

_logger = logging.getLogger(__name__)


class InMemorySessionStore(ISessionStore):
    """In-process dict: append-only list per session + creation timestamp for TTL."""

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, list[dict]] = {}
        self._created_at: dict[str, float] = {}
        self._seen_turn_ids: dict[str, set] = {}

    def _expire_if_needed(self, session_id: str) -> None:
        created = self._created_at.get(session_id)
        if created is not None and (time.monotonic() - created) > self._ttl_seconds:
            self._sessions.pop(session_id, None)
            self._seen_turn_ids.pop(session_id, None)
            self._created_at.pop(session_id, None)

    def append(self, session_id: str, entry: dict) -> None:
        self._expire_if_needed(session_id)

        turn_id = entry.get("turn")
        seen = self._seen_turn_ids.setdefault(session_id, set())
        if turn_id is not None:
            if turn_id in seen:
                raise SessionRewriteError(
                    f"turn {turn_id!r} already recorded in session {session_id!r}: session is append-only"
                )
            seen.add(turn_id)

        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._created_at[session_id] = time.monotonic()
        self._sessions[session_id].append(dict(entry))

    def history(self, session_id: str) -> tuple[dict, ...]:
        self._expire_if_needed(session_id)
        return tuple(self._sessions.get(session_id, ()))


class RedisSessionStore(ISessionStore):
    """Same semantics (append-only + TTL) using Redis: RPUSH + EXPIRE, never LSET."""

    def __init__(self, redis_url: str, ttl_seconds: int = 1800) -> None:
        import redis  # lazy import

        self._ttl_seconds = ttl_seconds
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._seen_turn_ids: dict[str, set] = {}

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def append(self, session_id: str, entry: dict) -> None:
        import json

        turn_id = entry.get("turn")
        seen = self._seen_turn_ids.setdefault(session_id, set())
        if turn_id is not None:
            if turn_id in seen:
                raise SessionRewriteError(
                    f"turn {turn_id!r} already recorded in session {session_id!r}: session is append-only"
                )
            seen.add(turn_id)

        key = self._key(session_id)
        self._redis.rpush(key, json.dumps(entry))
        self._redis.expire(key, self._ttl_seconds)

    def history(self, session_id: str) -> tuple[dict, ...]:
        import json

        raw = self._redis.lrange(self._key(session_id), 0, -1)
        return tuple(json.loads(item) for item in raw)


class PostgresSessionStore(ISessionStore):
    """Same append-only + TTL semantics on Postgres.

    `turn_index` is computed as the next index for the session (MAX(turn_index) + 1) and the
    composite primary key `(session_id, turn_index)` is the only thing that ever rejects a
    write: a concurrent append landing on the same index hits a PK violation, which is
    translated into `SessionRewriteError`. No UPDATE/DELETE is ever issued against a turn —
    append-only by construction, not by convention.
    """

    def __init__(self, database_url: str, ttl_seconds: int = 1800) -> None:
        from sqlalchemy import create_engine

        from infrastructure.memory.models import Base, SessionEntry

        self._ttl_seconds = ttl_seconds
        self._engine = create_engine(database_url)
        self._entry_model = SessionEntry
        Base.metadata.create_all(self._engine, tables=[SessionEntry.__table__], checkfirst=True)

    def append(self, session_id: str, entry: dict) -> None:
        from sqlalchemy import func, select
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        SessionEntry = self._entry_model
        now = time.time()
        with Session(self._engine) as session:
            next_index = session.execute(
                select(func.coalesce(func.max(SessionEntry.turn_index), -1) + 1).where(
                    SessionEntry.session_id == session_id
                )
            ).scalar_one()
            session.add(SessionEntry(
                session_id=session_id,
                turn_index=next_index,
                entry=dict(entry),
                created_at=now,
                expires_at=now + self._ttl_seconds,
            ))
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SessionRewriteError(
                    f"turn {next_index!r} already recorded in session {session_id!r}: session is append-only"
                ) from exc

    def history(self, session_id: str) -> tuple[dict, ...]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        SessionEntry = self._entry_model
        now = time.time()
        with Session(self._engine) as session:
            rows = session.execute(
                select(SessionEntry.entry)
                .where(SessionEntry.session_id == session_id, SessionEntry.expires_at > now)
                .order_by(SessionEntry.turn_index)
            ).scalars().all()
        return tuple(rows)

    def session_records(self, session_id: str) -> tuple[SessionRecord, ...]:
        """Typed, canonical row representation (used where callers need turn_index/created_at)."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        SessionEntry = self._entry_model
        now = time.time()
        with Session(self._engine) as session:
            rows = session.execute(
                select(SessionEntry)
                .where(SessionEntry.session_id == session_id, SessionEntry.expires_at > now)
                .order_by(SessionEntry.turn_index)
            ).scalars().all()
        return tuple(
            SessionRecord(session_id=r.session_id, turn_index=r.turn_index, entry=r.entry, created_at=r.created_at)
            for r in rows
        )


def build_session_store(redis_url: str | None, ttl_seconds: int = 1800) -> ISessionStore:
    """Factory: uses Redis if `redis_url` (env REDIS_URL) is defined and reachable; otherwise an in-memory dict."""
    if not redis_url:
        return InMemorySessionStore(ttl_seconds=ttl_seconds)
    try:
        store = RedisSessionStore(redis_url, ttl_seconds=ttl_seconds)
        store._redis.ping()
        return store
    except Exception as exc:  # noqa: BLE001 - any Redis failure falls back to the in-memory store
        _logger.warning("RedisSessionStore unavailable (%s); using InMemorySessionStore.", exc)
        print(f"[memory] warning: Redis unavailable ({exc}); using InMemorySessionStore.")
        return InMemorySessionStore(ttl_seconds=ttl_seconds)
