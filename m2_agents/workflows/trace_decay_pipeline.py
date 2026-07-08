from __future__ import annotations

from m2_agents.core.orchestrator import orchestrator
from m2_agents.core.state import GraphState


def run(device_id: str | None = None, new_firmware: str | None = None, changed_components: list[str] | None = None) -> GraphState:
    return orchestrator.run_trace_decay(device_id, new_firmware, changed_components)
