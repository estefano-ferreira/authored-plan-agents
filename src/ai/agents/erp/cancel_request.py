"""CancelRequest capability: cancels a customer request already registered in the ERP.

Bundles the capability's declarative step together with the tool function it owns
(`cancel_record`) and the connector target it invokes, defined inline (see the note in
`register_request.py` about the duplicated `ERP` target: both are frozen value objects, and this
small duplication keeps each capability file self-contained).
"""
from core.capabilities import Capability, Step, StepKind, ToolType, tool
from core.ports import ConnectorTarget

ERP = ConnectorTarget(name="erp", kind="rest")


class CancelRequest(Capability):
    """System-of-record cancellation of an already-registered customer request."""
    id = "cancel-request"
    description = "Cancels a customer request already registered in the ERP."
    steps = (
        Step(id="cancel", kind=StepKind.TOOL, tool="cancel_record"),
    )


@tool(ToolType.SYSTEM_OF_RECORD, ERP)
def cancel_record(ctx, record_id: str, **_kwargs) -> dict:
    """Cancels record `record_id` in the ERP."""
    return ctx.connectors["rest"].invoke(ERP, "cancel_record", {"record_id": record_id}, ctx.principal)
