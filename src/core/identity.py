"""Identity of the requester: who proposed the action, propagated up to the system-of-record target."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Who authorizes an action: an authenticated user or the system itself (autonomous execution)."""
    id: str
    type: str
    scopes: tuple[str, ...]


class SystemPrincipal(Principal):
    """Principal issued by the Orchestrator when execution does not originate from a user."""

    @classmethod
    def with_scopes(cls, scopes: tuple[str, ...]) -> Principal:
        return cls(id="system", type="system", scopes=tuple(scopes))
