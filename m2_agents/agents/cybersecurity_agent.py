from __future__ import annotations

from typing import Any

from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState
from m2_agents.tools import cybersecurity_tools, graph_tools


class CybersecurityAgent(BaseAgent):
    name = "cybersecurity"

    def run(
        self,
        state: GraphState,
        *,
        sbom_path: str | None = None,
        force_refresh: bool = False,
        max_components: int | None = None,
        max_cves_per_component: int = cybersecurity_tools.DEFAULT_MAX_CVES_PER_COMPONENT,
        delay_seconds: float | None = None,
    ) -> GraphState:
        with traced(state, self.name, "scan SBOM components against NVD"):
            state.device_id = graph_tools.resolve_device_id(state.device_id)
            state.graph_context = graph_tools.get_device_context(state.device_id)
            cached = None if force_refresh else cybersecurity_tools.load_cached_scan(state.device_id)
            if cached:
                self._apply_payload(state, cached, cache_status="cache_hit")
                return state

            components = cybersecurity_tools.load_sbom_components(sbom_path)
            if not components:
                state.errors.append("CybersecurityAgent found no SBOM components to scan.")
                state.sbom_components = []
                state.cybersecurity_findings = []
                state.cybersecurity_summary = {
                    "status": "no_sbom_components",
                    "source_type": "extracted",
                }
                return state

            scan = cybersecurity_tools.scan_components_against_nvd(
                components,
                max_components=max_components,
                max_cves_per_component=max_cves_per_component,
                delay_seconds=delay_seconds,
            )
            payload = {
                "device_id": state.device_id,
                "sbom_components": components,
                "cybersecurity_findings": scan["findings"],
                "cybersecurity_summary": {
                    key: value
                    for key, value in scan.items()
                    if key not in {"findings"}
                },
            }
            cybersecurity_tools.save_cached_scan(state.device_id, payload)
            self._apply_payload(state, payload, cache_status="refreshed")
        return state

    def _apply_payload(self, state: GraphState, payload: dict[str, Any], *, cache_status: str) -> None:
        state.sbom_components = payload.get("sbom_components", [])
        state.cybersecurity_findings = payload.get("cybersecurity_findings", [])
        state.cybersecurity_summary = payload.get("cybersecurity_summary", {})
        state.cybersecurity_summary["cache_status"] = cache_status
        state.status = "cybersecurity_scan_completed"
        state.agent_debug[self.name] = {
            "outcome": {
                "sbom_component_count": len(state.sbom_components),
                "finding_count": len(state.cybersecurity_findings),
                "severity_counts": state.cybersecurity_summary.get("severity_counts", {}),
                "cache_status": cache_status,
            },
            "graph_fetches": [
                {
                    "tool": "graph_tools.get_device_context",
                    "purpose": "Resolve the device context for a device-level SBOM/NVD scan.",
                    "device_id": state.device_id,
                    "node_count": state.graph_context.get("node_count"),
                    "edge_count": state.graph_context.get("edge_count"),
                }
            ],
            "external_queries": [
                {
                    "tool": "NVD CVE API 2.0",
                    "api_url": state.cybersecurity_summary.get("api_url"),
                    "request_count": state.cybersecurity_summary.get("request_count", 0),
                    "rate_limit_policy": state.cybersecurity_summary.get("rate_limit_policy", {}),
                    "source_type": state.cybersecurity_summary.get("source_type", "extracted"),
                }
            ],
        }
