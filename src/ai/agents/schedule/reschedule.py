"""Reschedule capability: reschedules an existing appointment, reusing the physical tools defined in book.py.

There is no code duplication: `check_availability`, `create_appointment` and `send_confirmation` are
defined only once (in `book.py`, this agent's sibling capability file) and get registered into the
agent's ToolRegistry when `ToolRegistry.load_capability_tools(spec_dir)` scans every `*.py` file
under this agent's data folder. This module only references those tool names via `Step.tool`; it
declares no tool functions and no connector targets of its own.
"""
from core.capabilities import Capability, Step, StepKind


class Reschedule(Capability):
    """Reschedule: availability -> recreate the appointment (with `previous_appointment_id`) -> confirmation."""
    id = "reschedule"
    description = "Checks availability and recreates an existing appointment, confirming to the customer."
    steps = (
        Step(id="availability", kind=StepKind.TOOL, tool="check_availability"),
        Step(id="create", kind=StepKind.TOOL, tool="create_appointment", depends_on=("availability",)),
        Step(id="confirm", kind=StepKind.TOOL, tool="send_confirmation", depends_on=("create",)),
    )
