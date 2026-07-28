"""SQLAlchemy 2.0 models for the ERP service's business entities.

These entities exist only here (outside `src/core` and `src/infrastructure`): the
platform's connector sends and receives plain dicts/JSON, never these classes. The
business invariants that matter for the study are expressed as **database
constraints**, not as in-memory/Python-level checks, so that the decisive validation
happens against the persisted, transactional state:

- `ServiceRequest`: at most one row with `status='open'` per `customer_email`
  (partial unique index -- a customer cannot have two open requests at once).
- `Appointment`: at most one row with `status='booked'` per `slot` (partial unique
  index -- two bookings for the same slot cannot both succeed).

Both are SQLite **partial** unique indexes (`sqlite_where=...`): the constraint only
applies to rows matching the predicate, so a cancelled request/appointment does not
block a new one from reusing the same email/slot.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ServiceRequest(Base):
    """A customer request registered in the ERP (support/order/cancellation)."""

    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Public identifier, "record-<id>"; filled in after the row gets its autoincrement id
    # (see api.py::create_record) and used as the id the platform/tests address the row by.
    record_id: Mapped[str] = mapped_column(String, unique=True, nullable=True)
    customer_email: Mapped[str] = mapped_column(String, nullable=False)
    request_type: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")  # "open" | "cancelled"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        # Decisive invariant: at most one OPEN request per customer, enforced by SQLite itself.
        Index(
            "ix_service_requests_open_per_customer",
            "customer_email",
            unique=True,
            sqlite_where=text("status = 'open'"),
        ),
    )


class Appointment(Base):
    """A scheduling appointment booked against a customer."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Public identifier, "appointment-<id>"; filled in after the row gets its autoincrement id
    # (see api.py::create_appointment).
    appointment_id: Mapped[str] = mapped_column(String, unique=True, nullable=True)
    customer_id: Mapped[str] = mapped_column(String, nullable=False)
    slot: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="booked")  # "booked" | "cancelled"
    previous_appointment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        # Decisive invariant: at most one BOOKED appointment per slot, enforced by SQLite itself --
        # two concurrent create_appointment calls for the same slot cannot both succeed.
        Index(
            "ix_appointments_booked_slot",
            "slot",
            unique=True,
            sqlite_where=text("status = 'booked'"),
        ),
    )
