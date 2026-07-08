from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - deterministic fallback for minimal installs.
    END = START = None
    StateGraph = None

from m2_agents.agents.audit_shadow import AuditShadowAgent
from m2_agents.agents.capa_agent import CapaAgent
from m2_agents.agents.complaint_agent import ComplaintIntakeAgent
from m2_agents.agents.cybersecurity_agent import CybersecurityAgent
from m2_agents.agents.digital_twin_agent import FirmwareTraceabilityRippleCheckAgent
from m2_agents.agents.evidence_agent import EvidenceAgent
from m2_agents.agents.openai_reasoning import OpenAIReasoningAgent
from m2_agents.agents.risk_agent import RiskAgent
from m2_agents.agents.root_cause_agent import RootCauseAgent
from m2_agents.agents.trace_decay import TraceDecayAgent
from m2_agents.core.memory import JsonMemorySaver
from m2_agents.core.state import GraphState
from m2_agents.tools import graph_tools


class MedTraceOrchestrator:
    """Small supervisor-worker runtime mirroring the M2 LangGraph design."""

    def __init__(self) -> None:
        self.memory = JsonMemorySaver()
        self.complaint_agent = ComplaintIntakeAgent()
        self.root_cause_agent = RootCauseAgent()
        self.digital_twin_agent = FirmwareTraceabilityRippleCheckAgent()
        self.evidence_agent = EvidenceAgent()
        self.risk_agent = RiskAgent()
        self.capa_agent = CapaAgent()
        self.openai_reasoning_agent = OpenAIReasoningAgent()
        self.audit_shadow_agent = AuditShadowAgent()
        self.trace_decay_agent = TraceDecayAgent()
        self.cybersecurity_agent = CybersecurityAgent()
        self.langgraph_available = StateGraph is not None
        self.complaint_graph = self._build_complaint_graph() if self.langgraph_available else None
        self.audit_graph = self._build_audit_graph() if self.langgraph_available else None
        self.trace_graph = self._build_trace_graph() if self.langgraph_available else None

    def run_complaint_pipeline(
        self,
        raw_complaint: str,
        device_id: str | None = None,
        regulatory_framework: str = "AUTO",
        firmware_version: str | None = None,
        serial_number: str | None = None,
        lot: str | None = None,
        thread_id: str | None = None,
        include_openai: bool = True,
    ) -> GraphState:
        thread_id = thread_id or f"complaint-{uuid4().hex[:8]}"
        state = GraphState(
            raw_complaint=raw_complaint,
            device_id=graph_tools.resolve_device_id(device_id),
            complaint_firmware_version=firmware_version or "",
            serial_number=serial_number or "",
            lot=lot or "",
            regulatory_framework=regulatory_framework,
        )
        if self.complaint_graph is not None and include_openai:
            result = self.complaint_graph.invoke({"state": state, "thread_id": thread_id})
            state = result["state"]
            state.status = "completed"
            self.memory.save(thread_id, "completed", state)
            return state

        steps = [
            ("complaint_intake", self.complaint_agent.run),
            ("root_cause", self.root_cause_agent.run),
            ("firmware_traceability_ripple_check", self.digital_twin_agent.run),
            ("evidence", self.evidence_agent.run),
            ("risk", self.risk_agent.run),
            ("capa", self.capa_agent.run),
        ]
        if include_openai:
            steps.append(("openai_reasoning", lambda state: self.openai_reasoning_agent.run(state, "complaint")))
        for step, fn in steps:
            state.add_event("supervisor", f"route to {step}", "info")
            state = fn(state)
            if step == "complaint_intake":
                state.add_event(
                    "supervisor",
                    "handoff check: similar incidents preserved for root cause",
                    "info",
                    similar_incident_count=len(state.similar_incidents),
                    similar_incident_ids=[str(item.get("id", "")) for item in state.similar_incidents[:5]],
                )
            self.memory.save(thread_id, step, state)
        state.status = "completed"
        self.memory.save(thread_id, "completed", state)
        return state

    def stream_complaint_pipeline(
        self,
        raw_complaint: str,
        device_id: str | None,
        regulatory_framework: str,
        firmware_version: str | None = None,
        serial_number: str | None = None,
        lot: str | None = None,
    ) -> Iterator[dict]:
        state = GraphState(
            raw_complaint=raw_complaint,
            device_id=graph_tools.resolve_device_id(device_id),
            complaint_firmware_version=firmware_version or "",
            serial_number=serial_number or "",
            lot=lot or "",
            regulatory_framework=regulatory_framework,
        )
        steps = [
            ("Complaint Intake", self.complaint_agent.run),
            ("Root Cause", self.root_cause_agent.run),
            ("Firmware Traceability Ripple Check", self.digital_twin_agent.run),
            ("Evidence", self.evidence_agent.run),
            ("Risk", self.risk_agent.run),
            ("CAPA", self.capa_agent.run),
            ("OpenAI Reasoning", lambda state: self.openai_reasoning_agent.run(state, "complaint")),
        ]
        for label, fn in steps:
            yield {"agent": label, "status": "started", "data": None}
            state = fn(state)
            yield {"agent": label, "status": "completed", "data": _step_payload(label, state)}
        yield {"agent": "Supervisor", "status": "completed", "data": state.model_dump()}

    def run_audit_shadow(self, device_id: str | None = None, regulatory_framework: str = "AUTO") -> GraphState:
        state = GraphState(device_id=graph_tools.resolve_device_id(device_id), regulatory_framework=regulatory_framework)
        if self.audit_graph is not None:
            result = self.audit_graph.invoke({"state": state, "thread_id": f"audit-{device_id}"})
            return result["state"]

        state.add_event("supervisor", "route to AuditShadow", "info")
        state = self.audit_shadow_agent.run(state)
        state = self.openai_reasoning_agent.run(state, "audit")
        self.memory.save(f"audit-{device_id}", "audit_shadow", state)
        return state

    def stream_audit_shadow(self, device_id: str | None, regulatory_framework: str) -> Iterator[dict]:
        resolved_device_id = graph_tools.resolve_device_id(device_id)
        state = GraphState(device_id=resolved_device_id, regulatory_framework=regulatory_framework)
        yield {"agent": "AuditShadow", "status": "started", "data": {"device_id": resolved_device_id}}
        state = self.audit_shadow_agent.run(state)
        state = self.openai_reasoning_agent.run(state, "audit")
        for finding in state.audit_findings:
            yield {"agent": "AuditShadow", "status": "finding", "data": finding.model_dump()}
        yield {"agent": "AuditShadow", "status": "completed", "data": state.model_dump()}

    def run_trace_decay(
        self,
        device_id: str | None = None,
        new_firmware: str | None = None,
        changed_components: list[str] | None = None,
    ) -> GraphState:
        resolved_device_id = graph_tools.resolve_device_id(device_id)
        state = GraphState(device_id=resolved_device_id)
        if self.trace_graph is not None:
            result = self.trace_graph.invoke(
                {
                    "state": state,
                    "thread_id": f"trace-{resolved_device_id}",
                    "new_firmware": new_firmware,
                    "changed_components": changed_components or [],
                }
            )
            return result["state"]

        state = self.trace_decay_agent.run(state, new_firmware, changed_components)
        state = self.openai_reasoning_agent.run(state, "trace_decay")
        self.memory.save(f"trace-{resolved_device_id}", "trace_decay", state)
        return state

    def run_cybersecurity_scan(
        self,
        device_id: str | None = None,
        sbom_path: str | None = None,
        force_refresh: bool = False,
        max_components: int | None = None,
        max_cves_per_component: int = 5,
        delay_seconds: float | None = None,
    ) -> GraphState:
        resolved_device_id = graph_tools.resolve_device_id(device_id)
        state = GraphState(device_id=resolved_device_id)
        state.add_event("supervisor", "route to cybersecurity", "info")
        state = self.cybersecurity_agent.run(
            state,
            sbom_path=sbom_path,
            force_refresh=force_refresh,
            max_components=max_components,
            max_cves_per_component=max_cves_per_component,
            delay_seconds=delay_seconds,
        )
        self.memory.save(f"cybersecurity-{resolved_device_id}", "cybersecurity", state)
        return state

    def _build_complaint_graph(self):
        workflow = StateGraph(dict)
        workflow.add_node("complaint_intake", self._node("complaint_intake", self.complaint_agent.run))
        workflow.add_node("root_cause", self._node("root_cause", self.root_cause_agent.run))
        workflow.add_node(
            "firmware_traceability_ripple_check",
            self._node("firmware_traceability_ripple_check", self.digital_twin_agent.run),
        )
        workflow.add_node("evidence", self._node("evidence", self.evidence_agent.run))
        workflow.add_node("risk", self._node("risk", self.risk_agent.run))
        workflow.add_node("capa", self._node("capa", self.capa_agent.run))
        workflow.add_node(
            "openai_reasoning",
            self._node("openai_reasoning", lambda state: self.openai_reasoning_agent.run(state, "complaint")),
        )
        workflow.add_edge(START, "complaint_intake")
        workflow.add_edge("complaint_intake", "root_cause")
        workflow.add_edge("root_cause", "firmware_traceability_ripple_check")
        workflow.add_edge("firmware_traceability_ripple_check", "evidence")
        workflow.add_edge("evidence", "risk")
        workflow.add_edge("risk", "capa")
        workflow.add_edge("capa", "openai_reasoning")
        workflow.add_edge("openai_reasoning", END)
        return workflow.compile()

    def _build_audit_graph(self):
        workflow = StateGraph(dict)
        workflow.add_node("audit_shadow", self._node("audit_shadow", self.audit_shadow_agent.run))
        workflow.add_node("openai_reasoning", self._node("openai_reasoning", lambda state: self.openai_reasoning_agent.run(state, "audit")))
        workflow.add_edge(START, "audit_shadow")
        workflow.add_edge("audit_shadow", "openai_reasoning")
        workflow.add_edge("openai_reasoning", END)
        return workflow.compile()

    def _build_trace_graph(self):
        workflow = StateGraph(dict)

        def trace_node(payload: dict) -> dict:
            state = payload["state"]
            state.add_event("langgraph_supervisor", "route to trace_decay", "info")
            state = self.trace_decay_agent.run(
                state,
                payload.get("new_firmware"),
                payload.get("changed_components") or [],
            )
            state = self.openai_reasoning_agent.run(state, "trace_decay")
            self.memory.save(payload.get("thread_id", "trace-default"), "trace_decay", state)
            return {**payload, "state": state}

        workflow.add_node("trace_decay", trace_node)
        workflow.add_edge(START, "trace_decay")
        workflow.add_edge("trace_decay", END)
        return workflow.compile()

    def _node(self, step: str, fn):
        def runner(payload: dict) -> dict:
            state = payload["state"]
            state.add_event("langgraph_supervisor", f"route to {step}", "info")
            state = fn(state)
            if step == "complaint_intake":
                state.add_event(
                    "langgraph_supervisor",
                    "handoff check: similar incidents preserved for root cause",
                    "info",
                    similar_incident_count=len(state.similar_incidents),
                    similar_incident_ids=[str(item.get("id", "")) for item in state.similar_incidents[:5]],
                )
            self.memory.save(payload.get("thread_id", "m2-default"), step, state)
            return {**payload, "state": state}

        return runner


def _step_payload(label: str, state: GraphState) -> dict:
    if label == "Complaint Intake":
        return state.structured_complaint.model_dump() if state.structured_complaint else {}
    if label == "Root Cause":
        return {"hypotheses": [item.model_dump() for item in state.hypotheses]}
    if label == "Firmware Traceability Ripple Check":
        return {"digital_twin_results": state.digital_twin_results}
    if label == "Evidence":
        return {"evidence": [item.model_dump() for item in state.evidence_collected]}
    if label == "Risk":
        return state.risk_assessment.model_dump() if state.risk_assessment else {}
    if label == "CAPA":
        return {"capa_draft": state.capa_draft, "investigation_scope": state.investigation_scope}
    if label == "OpenAI Reasoning":
        return {"ai_reasoning": state.ai_reasoning}
    return {}


orchestrator = MedTraceOrchestrator()
