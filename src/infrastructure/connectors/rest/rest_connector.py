"""Generic REST connector: routes (target, operation) to an HTTP method/route, via httpx.

Uses `httpx.AsyncClient` internally (asyncio->sync bridge in this module) because
`httpx.ASGITransport` — used by the tests to talk to the in-process FastAPI —
only implements async transport; the same client covers real calls over the network.
"""
import asyncio

import httpx

from core.ports import ConnectorRejection, ConnectorTarget, IConnector
from core.identity import Principal

# (target.name, operation) -> (method, route function); the function receives the payload.
_ROUTES: dict[tuple[str, str], tuple[str, callable]] = {
    ("scheduling", "check_availability"): ("GET", lambda p: ("/appointments/availability", {"slot": p.get("slot")})),
    ("scheduling", "retrieve_policy"): ("GET", lambda p: ("/policies/scheduling", {})),
    ("scheduling", "create_appointment"): ("POST", lambda p: ("/appointments", None)),
    ("erp", "create_record"): ("POST", lambda p: ("/records", None)),
    ("erp", "cancel_record"): ("POST", lambda p: (f"/records/{p.get('record_id')}/cancel", None)),
}


class RestConnector(IConnector):
    """Real HTTP client; accepts a real `base_url` or a `transport` (e.g. `httpx.ASGITransport` in tests).

    Each `invoke()` runs its own `asyncio.run()` (synchronous bridge, project style constraint). That is
    why the `httpx.AsyncClient` is created and closed *inside* the same loop of each call, instead of
    being shared across calls: a persistent client created in `__init__` binds its connections/streams
    to the first loop that uses it, and reusing it in a subsequent `asyncio.run()` (a new loop) fails
    with `RuntimeError: Event loop is closed` as soon as there are real network connections
    (not reproduced with `ASGITransport`, which never opens a socket).
    """

    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if base_url is None:
            base_url = "http://erp.internal" if transport is not None else "http://localhost"
        self._base_url = base_url
        self._transport = transport

    def invoke(self, target: ConnectorTarget, operation: str, payload: dict, principal: Principal) -> dict:
        return asyncio.run(self._invoke_async(target, operation, payload, principal))

    async def _invoke_async(
        self, target: ConnectorTarget, operation: str, payload: dict, principal: Principal
    ) -> dict:
        route_key = (target.name, operation)
        if route_key not in _ROUTES:
            raise ValueError(f"operation not routed for RestConnector: {target.key()}::{operation}")
        method, route_fn = _ROUTES[route_key]
        path, query = route_fn(payload)

        headers = {
            "X-Principal-Id": principal.id,
            "X-Principal-Type": principal.type,
            "X-Principal-Scopes": " ".join(principal.scopes),
        }

        async with httpx.AsyncClient(base_url=self._base_url, transport=self._transport) as client:
            if method == "GET":
                response = await client.get(path, params=query or None, headers=headers)
            else:
                response = await client.post(path, json=payload, headers=headers)

        if response.status_code == 422:
            body = response.json()
            raise ConnectorRejection(
                code=body.get("code", "invariant_violated"),
                violation=body.get("violation", ""),
                detail=body.get("detail", ""),
            )
        if not (200 <= response.status_code < 300):
            raise RuntimeError(
                f"REST call failed: {method} {path} -> {response.status_code} {response.text}"
            )
        return response.json()

    def close(self) -> None:
        """No-op: there is no persistent client to close (each invoke() opens and closes its own)."""
