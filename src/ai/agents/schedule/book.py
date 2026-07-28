"""Book capability: checks availability and policy, creates the appointment and confirms to the customer.

Bundles the capability's declarative steps together with the tool functions it owns
(`check_availability`, `retrieve_policy`, `create_appointment`, `send_confirmation`) and the
connector targets those tools invoke, defined inline as local frozen value objects (no shared
`targets.py` anymore). `reschedule.py` (this agent's other capability file) reuses
`check_availability`, `create_appointment` and `send_confirmation` by referencing their names in
`Step.tool` -- their code stays defined here, once, and is registered into the agent's
ToolRegistry when `ToolRegistry.load_capability_tools(spec_dir)` scans every `*.py` file in this
agent's data folder.
"""
from core.capabilities import Capability, Step, StepKind, ToolType, tool
from core.ports import ConnectorTarget

SCHEDULING = ConnectorTarget(name="scheduling", kind="rest")
WHATSAPP = ConnectorTarget(name="whatsapp", kind="mcp")


class Book(Capability):
    """Full new-appointment flow: availability -> policy -> creation -> confirmation."""
    id = "book"
    description = "Checks availability and policy, creates a new appointment and confirms to the customer."
    steps = (
        Step(id="availability", kind=StepKind.TOOL, tool="check_availability"),
        Step(id="policy", kind=StepKind.TOOL, tool="retrieve_policy", depends_on=("availability",)),
        Step(id="create", kind=StepKind.TOOL, tool="create_appointment", depends_on=("policy",)),
        Step(id="confirm", kind=StepKind.TOOL, tool="send_confirmation", depends_on=("create",)),
    )


@tool(ToolType.READ_ONLY, SCHEDULING)
def check_availability(ctx, slot: str, **_kwargs) -> dict:
    """Checks whether the `slot` time is available for booking."""
    return ctx.connectors["rest"].invoke(SCHEDULING, "check_availability", {"slot": slot}, ctx.principal)


@tool(ToolType.READ_ONLY, SCHEDULING)
def retrieve_policy(ctx, **_kwargs) -> dict:
    """Retrieves the scheduling policy (allowed windows, minimum notice, etc.)."""
    return ctx.connectors["rest"].invoke(SCHEDULING, "retrieve_policy", {}, ctx.principal)


@tool(ToolType.SYSTEM_OF_RECORD, SCHEDULING)
def create_appointment(ctx, customer_id: str, slot: str, previous_appointment_id: str | None = None, **_kwargs) -> dict:
    """Creates the appointment in the scheduling system; reused by Reschedule with `previous_appointment_id`."""
    payload = {"customer_id": customer_id, "slot": slot}
    if previous_appointment_id is not None:
        payload["previous_appointment_id"] = previous_appointment_id
    return ctx.connectors["rest"].invoke(SCHEDULING, "create_appointment", payload, ctx.principal)


@tool(ToolType.IRREVERSIBLE, WHATSAPP)
def send_confirmation(ctx, customer_id: str, results: dict | None = None, **_kwargs) -> dict:
    """Sends the confirmation for the appointment created in the previous step (`results['create']`)."""
    appointment_id = (results or {}).get("create", {}).get("appointment_id")
    return ctx.connectors["mcp"].invoke(
        WHATSAPP, "send_confirmation",
        {"customer_id": customer_id, "appointment_id": appointment_id}, ctx.principal,
    )
