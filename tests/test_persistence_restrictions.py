"""Persistence-phase restrictions: the platform's isolation from the ERP's storage, and the
consequences of compensation now that both the ERP and (optionally) the platform persist state.

All 4 tests run without Docker: the ERP is a real `erp_service` FastAPI app backed by a file-based
SQLite database (via `httpx.ASGITransport`, no network), and the platform uses the "local" model
provider (zero cost, deterministic). Where the tests need to inspect the ERP's persisted state
directly, they open the SQLite file with the stdlib `sqlite3` module -- bypassing the platform and
the ERP's own API on purpose, to observe the *actual* durable state, not a claim about it.
"""
import dataclasses
import json
import re
import sqlite3
import types
from enum import Enum
from pathlib import Path

import httpx
import pytest
import yaml

import erp_service.api as erp_api
from ai.orchestrator import load_plans
from core.identity import Principal
from core.ports import ConnectorRejection, ConnectorTarget
from infrastructure.configurations import build_platform
from infrastructure.connectors.mcp.mcp_connector import McpConnector
from infrastructure.connectors.rest.rest_connector import RestConnector

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"


@pytest.fixture(autouse=True)
def _clear_mcp_outbox():
    """The MCP stub outbox is a class-level list (shared across connector instances); isolate tests."""
    McpConnector.stub_outbox.clear()
    yield
    McpConnector.stub_outbox.clear()


def _build_platform(tmp_path: Path, subdir: str = "platform"):
    """Fresh Platform (local provider) wired to a fresh file-backed ERP SQLite DB under tmp_path.

    Returns `(platform, db_path)`. Each call gets its own subdirectory so a test can build more
    than one independent platform/ERP pair without their databases colliding.
    """
    root = tmp_path / subdir
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "erp.sqlite"
    erp_app = erp_api.create_app(f"sqlite:///{db_path}")
    transport = httpx.ASGITransport(app=erp_app)
    audit_path = root / "audit.jsonl"
    platform = build_platform(provider="local", erp_transport=transport, audit_path=audit_path)
    return platform, db_path


def _service_requests(db_path: Path) -> list[tuple]:
    """Reads (record_id, customer_email, status) for every row in the ERP's service_requests table."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT record_id, customer_email, status FROM service_requests")
        return cur.fetchall()
    finally:
        conn.close()


def _boom_plan_dict(register_compensate_intent: str) -> dict:
    """A 3-step test-only plan: acknowledge -> register (both as in the real inbound plan) -> a
    third step ('boom') whose intent no erp-agent capability matches, so capability selection
    itself fails *after* register's SYSTEM_OF_RECORD write already landed in the ERP's database.

    This is composition for the test only (a plan dict built and loaded here, then injected into
    `platform.orchestrator.plans`) -- `ai/plans/*.yaml` is untouched.
    """
    return {
        "id": "boom-after-register",
        "version": "1.0.0",
        "description": "Test-only plan: acknowledge, register, then a step that fails capability selection.",
        "triggers": [],
        "entry_scopes": ["gmail:read", "gmail:send", "erp:write"],
        "steps": [
            {
                "id": "acknowledge",
                "agent": "correspondence-agent",
                "intent": "read inbound email and send acknowledgement reply",
                "input": {"email_id": "{{ request.email_id }}"},
                "effect": True,
                "compensate": {
                    "agent": "correspondence-agent",
                    "intent": "send correction about failed processing",
                    "input": {"email_id": "{{ request.email_id }}", "mode": "correction"},
                },
            },
            {
                "id": "register",
                "agent": "erp-agent",
                "intent": "register customer request record",
                "depends_on": ["acknowledge"],
                "input": {
                    "customer_email": "{{ steps.acknowledge.result.sender }}",
                    "summary": "{{ steps.acknowledge.result.summary }}",
                    "request_type": "{{ steps.acknowledge.result.request_type }}",
                },
                "effect": True,
                "compensate": {
                    "agent": "erp-agent",
                    "intent": register_compensate_intent,
                    "input": {"record_id": "{{ steps.register.result.record_id }}"},
                },
            },
            {
                "id": "boom",
                "agent": "erp-agent",
                # No erp-agent capability (register-request, cancel-request) matches this intent
                # -> the local provider's selection returns "none" -> CapabilitySelectionError.
                "intent": "make breakfast",
                "depends_on": ["register"],
                "input": {},
                "effect": False,
            },
        ],
        "output": {},
    }


def _load_boom_plan(tmp_path: Path, register_compensate_intent: str):
    plans_dir = tmp_path / "boom-plan"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "boom_after_register.yaml").write_text(
        yaml.safe_dump(_boom_plan_dict(register_compensate_intent), sort_keys=False),
        encoding="utf-8",
    )
    loaded = load_plans(plans_dir)
    return loaded["boom-after-register"]


# --- Test 1: static + compose + dynamic isolation -----------------------------------------------

_FORBIDDEN_ERP_STORAGE_RE = re.compile(r"erp.*(sqlite|\.db|/data)", re.IGNORECASE)
_OPAQUE_TYPES = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType)


def _collect_strings(obj, depth: int = 0, seen: set | None = None, budget: list | None = None):
    """Bounded recursive walk collecting every string reachable from `obj`'s attribute graph.

    Depth-limited, cycle-safe (by id) and node-budgeted so it can be pointed at an arbitrary live
    object graph (the assembled Platform) without either recursing forever or walking into
    functions/classes/modules it has no business inspecting.
    """
    if seen is None:
        seen = set()
    if budget is None:
        budget = [5000]
    if depth > 6 or budget[0] <= 0:
        return
    if id(obj) in seen:
        return
    seen.add(id(obj))
    budget[0] -= 1

    if isinstance(obj, str):
        yield obj
        return
    if isinstance(obj, Path):
        yield str(obj)
        return
    if isinstance(obj, Enum):
        if isinstance(obj.value, str):
            yield obj.value
        return
    if isinstance(obj, (int, float, bool, bytes)) or obj is None:
        return
    if isinstance(obj, _OPAQUE_TYPES):
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _collect_strings(k, depth + 1, seen, budget)
            yield from _collect_strings(v, depth + 1, seen, budget)
        return
    if isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            yield from _collect_strings(v, depth + 1, seen, budget)
        return
    d = getattr(obj, "__dict__", None)
    if d is not None:
        for v in d.values():
            yield from _collect_strings(v, depth + 1, seen, budget)


def test_platform_has_no_path_to_business_storage(tmp_path):
    """Isolation in three layers: static source, declared Docker wiring, and the live assembled Platform."""
    # (a) Static: no file under src/** imports erp_service or embeds its storage DSN/path.
    offending = []
    for py_file in _SRC_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if re.search(r"^\s*(import\s+erp_service\b|from\s+erp_service\b)", text, re.MULTILINE):
            offending.append((str(py_file), "imports erp_service"))
        if "erp.sqlite" in text:
            offending.append((str(py_file), "embeds 'erp.sqlite'"))
        if "ERP_DATABASE_URL" in text:
            offending.append((str(py_file), "embeds 'ERP_DATABASE_URL'"))
    assert offending == [], f"src/** references ERP storage internals: {offending}"

    # (b) Compose: the 'aici' service has neither ERP_DATABASE_URL nor the ERP's data volume;
    # that volume is mounted by the 'erp' service alone.
    compose = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    assert "aici" in services and "erp" in services

    aici_env_text = json.dumps(services["aici"].get("environment", []))
    assert "ERP_DATABASE_URL" not in aici_env_text

    aici_volumes = services["aici"].get("volumes", [])
    assert not any("erp-data" in v for v in aici_volumes), aici_volumes

    services_mounting_erp_data = [
        name for name, svc in services.items() if any("erp-data" in v for v in svc.get("volumes", []))
    ]
    assert services_mounting_erp_data == ["erp"], services_mounting_erp_data

    # (c) Dynamic: the assembled Platform exposes no attribute/config carrying an ERP storage
    # path. The RestConnector is the one legitimate channel (HTTP/ASGI) -- inspected separately,
    # narrowly, to confirm it holds nothing beyond a base_url and its transport.
    platform, _db_path = _build_platform(tmp_path)

    connector = platform.connectors["rest"]
    assert isinstance(connector, RestConnector)
    assert set(vars(connector).keys()) <= {"_base_url", "_transport"}
    assert not connector._base_url.lower().endswith((".sqlite", ".db"))

    for f in dataclasses.fields(platform):
        if f.name == "connectors":
            continue  # the RestConnector's transport IS the one allowed HTTP/ASGI channel; checked above.
        value = getattr(platform, f.name)
        for s in _collect_strings(value):
            assert not _FORBIDDEN_ERP_STORAGE_RE.search(s), (
                f"Platform.{f.name} exposes what looks like an ERP storage path/DSN: {s!r}"
            )


# --- Test 2: compensation reaches persisted business state --------------------------------------

def test_compensation_updates_persisted_business_state(tmp_path):
    """A failing later step compensates an earlier SYSTEM_OF_RECORD write all the way to the DB row."""
    platform, db_path = _build_platform(tmp_path)
    plan = _load_boom_plan(tmp_path, register_compensate_intent="cancel request record")
    platform.orchestrator.plans[plan.id] = plan

    result = platform.orchestrator.handle({"plan": plan.id, "payload": {"email_id": "email-1"}})

    assert result.status == "compensated"
    assert result.rejection is not None
    assert result.rejection.code == "invariant_violated"

    rows = _service_requests(db_path)
    assert len(rows) == 1, rows
    _record_id, customer_email, status = rows[0]
    assert customer_email == "customer-email-1@example.com"  # gmail stub derives sender from email id
    # This is the point of the test: the compensating cancel_record call went through the same
    # RestConnector/HTTP path the real plan uses, against the persisted SQLite row -- a scenario
    # an in-memory stub could never distinguish from "nothing happened at all".
    assert status == "cancelled"

    # The successful compensation is itself audited (kind="compensation", action="result") --
    # not just its downstream effect on the ERP row asserted above.
    events = platform.audit.read_all()
    assert any(
        e["kind"] == "compensation" and e["detail"].get("action") == "result" and e["detail"].get("step") == "register"
        for e in events
    )


# --- Test 3: compensation failure is audited, alongside the orphan -----------------------------

def test_compensation_failure_is_audited_with_orphaned_state(tmp_path):
    """When the compensating call itself fails to select a capability, the ERP record is orphaned
    -- but that fact, and the record it left behind, are now reconstructable from the audit trail
    alone.

    FINDING (NOTES.md, "Persistence and real boundary"), gap closed 2026-07-27: compensation now
    has audit vocabulary (`AuditEvent.kind="compensation"`, actions "start"/"result"/"failed",
    documented in `core/ports.py`). The orphan row itself remains -- compensation failing still
    leaves the ERP record in `status='open'`, and nothing in this platform can change that without
    a human/operational remediation path -- but a reader no longer has to cross-reference persisted
    ERP state and infer a failure from a "proposal" event's `chosen="none"`; the "compensation"
    kind states directly that a compensation was attempted, for which step, and (on failure) what
    orphaned state it left behind.
    """
    platform, db_path = _build_platform(tmp_path)
    # register's compensate now carries an intent with no keyword match among erp-agent's
    # capabilities (register-request / cancel-request) -> its own capability selection fails.
    plan = _load_boom_plan(tmp_path, register_compensate_intent="do something unrelated")
    platform.orchestrator.plans[plan.id] = plan

    result = platform.orchestrator.handle({"plan": plan.id, "payload": {"email_id": "email-1"}})

    assert result.status == "compensation_failed"

    rows = _service_requests(db_path)
    assert len(rows) == 1, rows
    record_id, _customer_email, status = rows[0]
    # Orphan: the record was created (register succeeded) but never cancelled (its compensation
    # never reached the cancel_record tool) -- a genuine partial-failure state, not a rollback.
    # This does NOT change with the audit fix below: the row is still orphaned in the ERP's own
    # storage. What changes is that the orphan is now traceable from the audit trail alone.
    assert status == "open"

    events = platform.audit.read_all()

    create_events = [
        e for e in events if e["kind"] == "system_of_record" and e["detail"].get("tool") == "create_record"
    ]
    assert len(create_events) == 1, "the original create_record call must be audited as system_of_record"

    cancel_events = [
        e for e in events if e["kind"] == "system_of_record" and e["detail"].get("tool") == "cancel_record"
    ]
    assert cancel_events == [], "cancel_record's tool step was never reached -- selection failed first"

    # The failed compensation attempt still leaves the underlying capability-selection "proposal"
    # trace (chosen="none", since no erp-agent capability matched "do something unrelated") ...
    failed_selection_events = [
        e for e in events if e["kind"] == "proposal" and e["detail"].get("intent") == "do something unrelated"
    ]
    assert len(failed_selection_events) == 1
    assert failed_selection_events[0]["detail"].get("chosen") == "none"

    # ... but now that trace is bracketed by explicit "compensation" events for the 'register'
    # step's compensation, naming it as such instead of leaving it to be inferred.
    compensation_events = [e for e in events if e["kind"] == "compensation" and e["detail"].get("step") == "register"]
    actions = [e["detail"].get("action") for e in compensation_events]
    assert "start" in actions, "no 'compensation' audit event marks that register's compensation started"
    assert "failed" in actions, "no 'compensation' audit event marks that register's compensation failed"

    failed_event = next(e for e in compensation_events if e["detail"].get("action") == "failed")
    assert failed_event["step_id"] == "register:compensate"
    assert failed_event["principal_id"]
    # The orphaned state (the create_record result, whose record_id is the orphaned ERP row) is
    # carried directly on the failure event -- no cross-referencing the ERP's own storage required.
    assert failed_event["detail"]["orphaned_state"]["record_id"] == record_id


# --- Test 4: constraint violations reach the agent as a typed rejection -------------------------

def test_constraint_violation_reaches_agent_as_typed_rejection(tmp_path):
    # (a) Connector level: a direct RestConnector/ASGI double-booking of the same slot raises a
    # typed ConnectorRejection, not a generic exception.
    direct_db = tmp_path / "direct" / "erp.sqlite"
    direct_db.parent.mkdir(parents=True, exist_ok=True)
    erp_app = erp_api.create_app(f"sqlite:///{direct_db}")
    connector = RestConnector(transport=httpx.ASGITransport(app=erp_app))
    scheduling_target = ConnectorTarget(name="scheduling", kind="rest")
    principal = Principal(id="user-1", type="user", scopes=("scheduling:write",))

    first = connector.invoke(
        scheduling_target, "create_appointment",
        {"customer_id": "customer-1", "slot": "2026-09-10T09:00"}, principal,
    )
    assert first["status"] == "booked"

    with pytest.raises(ConnectorRejection) as exc_info:
        connector.invoke(
            scheduling_target, "create_appointment",
            {"customer_id": "customer-2", "slot": "2026-09-10T09:00"}, principal,
        )
    rejection = exc_info.value
    assert rejection.code == "invariant_violated"
    assert rejection.violation == "slot_taken"

    # (b) Plan level, scheduling: two schedule-appointment executions for the same slot -- the
    # second ends "compensated" and its Rejection is the same typed invariant_violated code.
    platform, _db_path = _build_platform(tmp_path)

    ok = platform.orchestrator.handle({
        "plan": "schedule-appointment",
        "payload": {"customer_id": "customer-3", "slot": "2026-09-11T09:00"},
        "principal": Principal(id="user-3", type="user", scopes=("scheduling:write", "whatsapp:send")),
    })
    assert ok.status == "completed"

    clash = platform.orchestrator.handle({
        "plan": "schedule-appointment",
        "payload": {"customer_id": "customer-4", "slot": "2026-09-11T09:00"},
        "principal": Principal(id="user-4", type="user", scopes=("scheduling:write", "whatsapp:send")),
    })
    assert clash.status == "failed_clean"
    assert clash.rejection is not None
    assert clash.rejection.code == "invariant_violated"

    # (b) Plan level, ERP: two inbound-email-to-erp executions for the SAME inbound email id --
    # the gmail stub derives the sender from the email id, so the same id means the same
    # customer, and the second run collides on the ERP's "one open request per customer"
    # invariant. The second execution is compensated with the same typed rejection code; the
    # *violation* itself (open_request_exists) is only visible in the human-readable reason text --
    # Rejection carries `code`/`reason` (str(exc)), not ConnectorRejection's structured `violation`
    # field, so the specific violation name does not survive into the plan-level result or audit.
    first_email = platform.orchestrator.handle({
        "plan": "inbound-email-to-erp", "payload": {"email_id": "email-open-1"},
    })
    assert first_email.status == "completed"

    second_email = platform.orchestrator.handle({
        "plan": "inbound-email-to-erp", "payload": {"email_id": "email-open-1"},
    })
    assert second_email.status == "compensated"
    assert second_email.rejection is not None
    assert second_email.rejection.code == "invariant_violated"
    assert "open request" in second_email.rejection.reason.lower()
