"""RegisterRequest capability: registers a new customer request in the ERP.

Bundles the capability's declarative step together with the tool function it owns (`create_record`,
the only write path for this capability) and the connector target it invokes, defined inline as a
local frozen value object. The same `ERP` target is also defined, identically, in
`cancel_request.py`: both capability files are self-contained (no shared `targets.py` anymore), and
the duplication of this small frozen value object is an accepted tradeoff.
"""
from core.capabilities import Capability, Step, StepKind, ToolType, tool
from core.ports import ConnectorTarget

ERP = ConnectorTarget(name="erp", kind="rest")


class RegisterRequest(Capability):
    """System-of-record registration of a customer request."""
    id = "register-request"
    description = "Registers a new customer request in the ERP."
    steps = (
        Step(id="create", kind=StepKind.TOOL, tool="create_record"),
    )


@tool(ToolType.SYSTEM_OF_RECORD, ERP)
def create_record(ctx, customer_email: str, summary: str, request_type: str, **_kwargs) -> dict:
    """Creates the request record in the ERP; the ERP validates the business invariants."""
    return ctx.connectors["rest"].invoke(
        ERP, "create_record",
        {"customer_email": customer_email, "summary": summary, "request_type": request_type},
        ctx.principal,
    )
