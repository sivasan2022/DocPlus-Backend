from __future__ import annotations

from m2_agents.core.orchestrator import orchestrator
from m2_agents.core.state import GraphState


def run(
    raw_complaint: str,
    device_id: str | None = None,
    regulatory_framework: str = "AUTO",
    firmware_version: str | None = None,
    serial_number: str | None = None,
    lot: str | None = None,
) -> GraphState:
    return orchestrator.run_complaint_pipeline(
        raw_complaint,
        device_id,
        regulatory_framework,
        firmware_version=firmware_version,
        serial_number=serial_number,
        lot=lot,
    )
