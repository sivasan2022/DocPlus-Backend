from __future__ import annotations

from m2_agents.core.orchestrator import orchestrator
from m2_agents.core.state import GraphState


def run(device_id: str | None = None, regulatory_framework: str = "AUTO") -> GraphState:
    return orchestrator.run_audit_shadow(device_id, regulatory_framework)
