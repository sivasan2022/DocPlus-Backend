from __future__ import annotations

from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.dynamic import next_version
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState
from m2_agents.tools import graph_tools


class TraceDecayAgent(BaseAgent):
    name = "trace_decay"

    def run(self, state: GraphState, new_firmware: str | None = None, changed_components: list[str] | None = None) -> GraphState:
        with traced(state, self.name, "detect trace decay"):
            state.device_id = graph_tools.resolve_device_id(state.device_id)
            context = graph_tools.get_device_context(state.device_id)
            current_fw = context.get("device", {}).get("current_firmware")
            inferred_version = new_firmware or next_version(current_fw)
            components = changed_components or [component["id"] for component in context.get("components", [])]
            result = graph_tools.run_ripple(state.device_id, inferred_version, components)
            state.trace_decay_alerts = result.get("stale_tests", [])
            state.status = "trace_decay_checked"
            state.add_event(
                self.name,
                "trace decay summary",
                "info",
                stale_test_count=result.get("stale_test_count", 0),
                new_firmware=inferred_version,
                changed_components=components,
            )
            state.agent_debug[self.name] = {
                "outcome": {
                    "new_firmware": inferred_version,
                    "changed_components": components,
                    "trace_decay_alerts": state.trace_decay_alerts,
                    "stale_test_count": result.get("stale_test_count", 0),
                },
                "graph_fetches": [
                    {
                        "tool": "graph_tools.get_device_context",
                        "purpose": "Resolve current firmware and selectable components.",
                        "summary": {
                            "device_id": state.device_id,
                            "current_firmware": current_fw,
                            "component_count": len(context.get("components", [])),
                            "node_count": context.get("node_count"),
                            "edge_count": context.get("edge_count"),
                        },
                        "components": context.get("components", []),
                    },
                    {
                        "tool": "graph_tools.run_ripple",
                        "purpose": "Propagate proposed firmware/component change through M1 traceability links.",
                        "result": result,
                    },
                ],
            }
        return state
