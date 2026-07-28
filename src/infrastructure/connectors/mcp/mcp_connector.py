"""Generic MCP connector (stdio): reads servers.yaml, calls call_tool; falls back to a per-server stub on connection failure."""
import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import yaml

from core.ports import ConnectorTarget, IConnector
from core.identity import Principal

_SERVERS_PATH = Path(__file__).parent / "servers.yaml"
_logger = logging.getLogger(__name__)


def _load_servers(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _short_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:8]


def _result_to_dict(result) -> dict:
    """Extracts a dict from a CallToolResult: structuredContent, otherwise parses text as JSON."""
    if getattr(result, "isError", False):
        texts = "; ".join(getattr(b, "text", "") for b in result.content)
        raise RuntimeError(f"MCP tool returned an error: {texts}")
    if result.structuredContent:
        return dict(result.structuredContent)
    texts = [getattr(b, "text", "") for b in result.content if getattr(b, "type", None) == "text"]
    joined = "\n".join(texts)
    try:
        parsed = json.loads(joined)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return {"text": joined}


class McpConnector(IConnector):
    """Real MCP client via the `mcp` SDK (stdio); falls back to a deterministic per-server stub on connection failure."""

    # messages "sent" by the stub (gmail/whatsapp), accessible to tests to check compensation.
    stub_outbox: list[dict] = []
    _warned_servers: set[str] = set()

    def __init__(self, servers_path: str | Path | None = None) -> None:
        self._servers = _load_servers(Path(servers_path) if servers_path else _SERVERS_PATH)

    def invoke(self, target: ConnectorTarget, operation: str, payload: dict, principal: Principal) -> dict:
        server_name = target.name
        config = self._servers.get(server_name)
        if config is None:
            raise ValueError(f"MCP server not declared in servers.yaml: {server_name}")

        try:
            return self._invoke_real(server_name, config, operation, payload)
        except Exception as exc:  # noqa: BLE001 - any connection failure falls back to the stub
            self._warn_fallback(server_name, exc)
            return self._invoke_stub(server_name, operation, payload)

    def _invoke_real(self, server_name: str, config: dict, operation: str, payload: dict) -> dict:
        command = config.get("command")
        if not command or shutil.which(command) is None:
            raise RuntimeError(f"MCP server command not found for {server_name!r}: {command!r}")

        required_env = config.get("env") or {}
        env_vars = dict(os.environ)
        for credential_name in required_env:
            if not os.environ.get(credential_name):
                raise RuntimeError(f"missing credential for MCP server {server_name!r}: {credential_name}")

        args = config.get("args") or []
        return asyncio.run(self._call_async(command, args, env_vars, operation, payload))

    @staticmethod
    async def _call_async(command: str, args: list[str], env_vars: dict, operation: str, payload: dict) -> dict:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=command, args=args, env=env_vars)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(operation, payload)
                return _result_to_dict(result)

    def _warn_fallback(self, server_name: str, exc: Exception) -> None:
        if server_name in self._warned_servers:
            return
        self._warned_servers.add(server_name)
        _logger.warning("McpConnector: falling back to stub for %r (%s)", server_name, exc)
        print(f"[mcp] warning: server {server_name!r} unavailable ({exc}); using deterministic stub.")

    def _invoke_stub(self, server_name: str, operation: str, payload: dict) -> dict:
        digest = _short_hash(payload)

        if server_name == "gmail" and operation == "read_email":
            # Sender derives deterministically from the email id: distinct inbound emails come
            # from distinct customers, so repeated measurement rounds don't all collide on the
            # ERP's one-open-request-per-customer constraint (a fixed sender would).
            email_id = payload.get("email_id") or f"email-{digest}"
            return {
                "email_id": email_id,
                "sender": f"customer-{email_id}@example.com",
                "subject": "Support request",
                "body": "Hello, I need support with my request 123.",
            }
        if server_name == "gmail" and operation == "send_reply":
            result = {"reply_id": f"reply-{digest}", "status": "sent"}
            self._record_outbox(server_name, operation, payload, result)
            return result
        if server_name == "whatsapp" and operation == "send_confirmation":
            result = {"message_id": f"wa-{digest}", "status": "delivered"}
            self._record_outbox(server_name, operation, payload, result)
            return result

        raise ValueError(f"no stub for operation on MCP server {server_name!r}: {operation}")

    @classmethod
    def _record_outbox(cls, server_name: str, operation: str, payload: dict, result: dict) -> None:
        cls.stub_outbox.append(
            {"server": server_name, "operation": operation, "payload": payload, "result": result}
        )
