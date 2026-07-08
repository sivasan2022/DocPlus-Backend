from __future__ import annotations

from typing import Any

from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.llm import llm
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState


class OpenAIReasoningAgent(BaseAgent):
    name = "openai_reasoning"

    def run(self, state: GraphState, workflow: str = "complaint") -> GraphState:
        with traced(state, self.name, f"final {workflow} reasoning"):
            payload = self._payload(state, workflow)
            if workflow == "audit":
                result = llm.final_audit_reasoning(payload)
            elif workflow == "trace_decay":
                result = llm.final_trace_reasoning(payload)
            else:
                result = llm.final_complaint_reasoning(payload)

            state.ai_reasoning[workflow] = result
            if result.get("error"):
                state.add_event(self.name, "OpenAI reasoning fallback", "warning", error=result["error"])
            elif result.get("enabled"):
                state.add_event(
                    self.name,
                    "OpenAI reasoning completed",
                    "completed",
                    model=result.get("model"),
                    mode=result.get("mode"),
                )
            else:
                state.add_event(self.name, "OpenAI reasoning disabled", "info", mode=result.get("mode"))
            state.agent_debug[self.name] = {
                "workflow": workflow,
                "outcome": result,
                "input_summary": {
                    "device_id": state.device_id,
                    "hypothesis_count": len(state.hypotheses),
                    "evidence_count": len(state.evidence_collected),
                    "audit_finding_count": len(state.audit_findings),
                    "trace_decay_alert_count": len(state.trace_decay_alerts),
                    "has_risk_assessment": state.risk_assessment is not None,
                    "capa_section_count": len(state.capa_sections),
                },
            }
        return state

    def _payload(self, state: GraphState, workflow: str) -> dict[str, Any]:
        if workflow == "audit":
            return {
                "device_id": state.device_id,
                "regulatory_framework": state.regulatory_label or state.regulatory_framework,
                "readiness": state.graph_context.get("readiness"),
                "finding_count": len(state.audit_findings),
                "findings": [finding.model_dump() for finding in state.audit_findings[:20]],
            }
        if workflow == "trace_decay":
            return {
                "device_id": state.device_id,
                "alerts_count": len(state.trace_decay_alerts),
                "trace_decay_alerts": state.trace_decay_alerts[:20],
            }
        return {
            "device_id": state.device_id,
            "regulatory_framework": state.regulatory_label or state.regulatory_framework,
            "structured_complaint": state.structured_complaint.model_dump() if state.structured_complaint else None,
            "similar_incident_count": len(state.similar_incidents),
            "hypotheses": [hypothesis.model_dump() for hypothesis in state.hypotheses],
            "evidence": [item.model_dump() for item in state.evidence_collected[:8]],
            "risk_assessment": state.risk_assessment.model_dump() if state.risk_assessment else None,
            "capa_sections": [section.model_dump() for section in state.capa_sections],
            "investigation_scope": state.investigation_scope,
            "graph_signals": {
                "node_count": state.graph_context.get("node_count"),
                "edge_count": state.graph_context.get("edge_count"),
                "stale_evidence_count": len(state.graph_context.get("stale_evidence", [])),
                "open_capa_count": state.graph_context.get("open_capa_count"),
            },
        }
