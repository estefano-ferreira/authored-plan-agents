"""ERP + scheduling service: FastAPI with database-enforced business invariants.

Real internal service (not part of the platform -- `infrastructure` consumes it via
`RestConnector`, either a real `base_url` or, for tests, an in-process
`httpx.ASGITransport`). Business entities live in `erp_service.models`, never in
`src/core` or `src/infrastructure`: the platform sends/receives plain dicts/JSON; the
entity is born and enforced entirely inside this service.

Validation happens in two layers, both surfacing as a typed 422
`{code: "invariant_violated", violation, detail}`:

1. **Pre-validation** -- cheap, stateless shape checks (email format, `request_type`
   membership, ISO slot format) that reject obviously malformed input before touching
   the database.
2. **Database constraint** -- the decisive check. `ServiceRequest`/`Appointment` carry
   partial unique indexes (see `erp_service.models`); a conflicting insert raises
   `sqlalchemy.exc.IntegrityError` inside the transaction, which this module catches
   and translates into a 422 (`violation="open_request_exists"` /
   `violation="slot_taken"`). This is deliberately *not* pre-checked in memory first:
   the persisted, transactional state is the source of truth, not a Python-side guess
   about it.
"""
import os
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from erp_service.models import Appointment, Base, ServiceRequest

# Module-level on purpose (read via the module's global namespace at call time, not captured in a
# closure): tests/test_end_to_end.py monkeypatches this exact name to force every create_record
# call to violate the invariant. Keep the name unchanged.
_VALID_REQUEST_TYPES = {"support", "order", "cancellation"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")

# Module-level, mirroring the legacy mock: the last principal received by *any* route on *any*
# app instance (module-level default or one built via create_app()), used by
# tests/test_restrictions.py to verify identity propagation (restriction 5). A debugging/testing
# aid, not business state.
last_principal: dict | None = None


def _violation(violation: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"code": "invariant_violated", "violation": violation, "detail": detail},
    )


def _read_principal(request: Request) -> dict:
    global last_principal
    principal = {
        "id": request.headers.get("X-Principal-Id", ""),
        "type": request.headers.get("X-Principal-Type", ""),
        "scopes": request.headers.get("X-Principal-Scopes", "").split(),
    }
    last_principal = principal
    return principal


def create_app(database_url: str | None = None) -> FastAPI:
    """Factory: a fresh FastAPI app with its own SQLAlchemy engine + schema.

    `database_url` defaults to an in-memory SQLite database on a `StaticPool` (a single
    connection kept alive for the app's lifetime, so all requests see the same in-memory
    DB) -- fast and disk-free, meant for unit tests. Any SQLite *file* URL (`sqlite:///...`
    or `sqlite:////...`) gets its parent directory created up front, since SQLite itself
    will not create missing directories.
    """
    if database_url is None:
        database_url = "sqlite://"

    url = make_url(database_url)
    connect_args: dict = {}
    engine_kwargs: dict = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database in (None, "", ":memory:"):
            engine_kwargs["poolclass"] = StaticPool
        else:
            Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    application = FastAPI(title="erp-service")
    application.state.engine = engine
    application.state.session_factory = session_factory

    @application.get("/debug/last-principal")
    def debug_last_principal():
        """Exposes the last principal received by any route, to verify identity propagation."""
        return {"last_principal": last_principal}

    @application.post("/records")
    async def create_record(request: Request):
        principal = _read_principal(request)
        body = await request.json()
        customer_email = body.get("customer_email", "")
        request_type = body.get("request_type", "")
        summary = body.get("summary", "")

        if not _EMAIL_RE.match(customer_email):
            return _violation("invalid_customer_email", f"invalid customer_email: {customer_email!r}")
        if request_type not in _VALID_REQUEST_TYPES:
            return _violation(
                "invalid_request_type",
                f"request_type must be one of {sorted(_VALID_REQUEST_TYPES)}, received {request_type!r}",
            )

        with request.app.state.session_factory() as session:
            row = ServiceRequest(
                customer_email=customer_email, request_type=request_type, summary=summary, status="open",
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Decisive check: the partial unique index (customer_email, status='open') rejected
                # this insert at the database level -- the customer already has an open request.
                session.rollback()
                return _violation(
                    "open_request_exists",
                    f"customer {customer_email!r} already has an open request",
                )
            row.record_id = f"record-{row.id}"
            session.commit()
            record_id = row.record_id

        return {"record_id": record_id, "status": "registered", "validated_principal": principal}

    @application.post("/records/{record_id}/cancel")
    async def cancel_record(record_id: str, request: Request):
        _read_principal(request)
        with request.app.state.session_factory() as session:
            row = session.execute(
                select(ServiceRequest).where(ServiceRequest.record_id == record_id)
            ).scalar_one_or_none()
            if row is None:
                return JSONResponse(status_code=404, content={"detail": f"record {record_id!r} not found"})
            # Marks cancelled, never deletes: the row stays as a compensation trail for the study.
            row.status = "cancelled"
            session.commit()
        return {"record_id": record_id, "status": "cancelled"}

    @application.post("/appointments")
    async def create_appointment(request: Request):
        principal = _read_principal(request)
        body = await request.json()
        slot = body.get("slot", "")
        customer_id = body.get("customer_id", "")
        previous_appointment_id = body.get("previous_appointment_id")

        if not _SLOT_RE.match(slot):
            return _violation("invalid_slot_format", f"slot must be ISO 8601, received {slot!r}")

        with request.app.state.session_factory() as session:
            row = Appointment(
                customer_id=customer_id, slot=slot, status="booked",
                previous_appointment_id=previous_appointment_id,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Decisive check: the partial unique index (slot, status='booked') rejected this
                # insert at the database level -- two bookings for the same slot cannot both succeed.
                session.rollback()
                return _violation("slot_taken", f"slot {slot!r} is already booked")
            row.appointment_id = f"appointment-{row.id}"
            session.commit()
            appointment_id = row.appointment_id

        return {"appointment_id": appointment_id, "status": "booked", "validated_principal": principal}

    @application.get("/appointments/availability")
    def check_availability(slot: str, request: Request):
        with request.app.state.session_factory() as session:
            booked = session.execute(
                select(Appointment.id).where(Appointment.slot == slot, Appointment.status == "booked")
            ).first()
        return {"slot": slot, "available": booked is None}

    @application.get("/policies/scheduling")
    def retrieve_policy():
        return {
            "policy": "cancellations up to 24h before the slot; reschedules allowed once per appointment",
        }

    return application


# Module-level default app, used directly by tests (`from erp_service import api as erp_app_module`)
# and by anything that imports `erp_service.api:app` (e.g. the Dockerfile's uvicorn entrypoint).
# `ERP_DATABASE_URL` unset -> in-memory SQLite on a StaticPool (fast, disk-free unit tests).
app = create_app(os.environ.get("ERP_DATABASE_URL"))


def reset() -> None:
    """Restores the module-level app's schema to its initial (empty) state. Used between tests."""
    global last_principal
    Base.metadata.drop_all(app.state.engine)
    Base.metadata.create_all(app.state.engine)
    last_principal = None


def __getattr__(name: str):
    """Module-level compatibility shim (PEP 562).

    `tests/test_end_to_end.py::test_inbound_email_compensates_when_erp_rejects_invariant` reads
    `erp_app_module._records` as a coarse "nothing was ever persisted" sanity check (the ERP
    invariant made every create_record call fail, so cancel_record is never reached and no record
    should exist at all). Unlike the legacy in-memory mock, `_records` here is a read-only view
    derived from the module-level app's SQLite database on each access -- the decisive state is
    the database (see the module docstring); this exists only so that one assertion keeps working
    unchanged.
    """
    if name == "_records":
        with app.state.session_factory() as session:
            rows = session.execute(select(ServiceRequest)).scalars().all()
            return {
                row.record_id: {
                    "record_id": row.record_id,
                    "customer_email": row.customer_email,
                    "request_type": row.request_type,
                    "summary": row.summary,
                    "status": row.status,
                }
                for row in rows
            }
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
