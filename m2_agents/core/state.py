from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    agent: str
    action: str
    status: Literal["started", "completed", "warning", "error", "info"] = "info"
    data: dict[str, Any] = Field(default_factory=dict)


class StructuredComplaint(BaseModel):
    device_id: str = ""
    firmware_version: str = ""
    serial_number: str = ""
    lot: str = ""
    severity: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    symptom_codes: list[str] = Field(default_factory=list)
    affected_component: str = ""
    affected_component_name: str = ""
    component_match_score: float = 0.0
    component_match_terms: list[str] = Field(default_factory=list)
    timeline: str = "Not specified"
    raw_summary: str = ""
    similar_incidents: list[dict[str, Any]] = Field(default_factory=list)
    source_type: str = "internal"


class Hypothesis(BaseModel):
    id: str
    title: str
    description: str
    affected_component: str
    base_probability: float = Field(ge=0, le=1)
    why_chain: list[str] = Field(default_factory=list)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    similar_incident_analysis: list[str] = Field(default_factory=list)
    probability_rationale: str = ""
    citation: str = "[Source: M2 deterministic reasoning, Confidence: 0.78]"
    supporting_facts: list[dict[str, Any]] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    id: str
    hypothesis_id: str | None = None
    source: str
    snippet: str
    confidence: float = Field(ge=0, le=1)
    supports: bool = True
    citation: str
    source_type: str = "inferred"
    source_node_id: str = ""
    evidence_class: str = "candidate"
    review_status: str = ""
    controlled_status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    severity: int = Field(ge=1, le=5)
    probability: int = Field(ge=1, le=5)
    rpn: int
    risk_level: Literal["Low", "Medium", "High", "Critical"]
    reportable: bool
    rationale: str
    citation: str
    uncertainty_flag: str = ""
    confidence_in_evidence: str = ""
    evidence_confidence_score: float = 0.0
    evidence_confidence_rule: str = ""
    evidence_confidence_basis: str = ""
    evidence_class_breakdown: dict[str, int] = Field(default_factory=dict)
    evidence_confidence_drivers: list[str] = Field(default_factory=list)
    evidence_classes: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)


class CapaSection(BaseModel):
    title: str
    body: str
    citation: str
    source_types: list[str] = Field(default_factory=list)
    evidence_classes: list[str] = Field(default_factory=list)


class AuditFinding(BaseModel):
    id: str
    requirement_id: str
    regulatory_reference: str
    finding: str
    risk_level: Literal["Minor", "Major", "Critical"]
    citation: str
    remediation: str
    source_type: str = "inferred"
    source_types: list[str] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)


class GraphState(BaseModel):
    raw_complaint: str = ""
    device_id: str = ""
    complaint_firmware_version: str = ""
    serial_number: str = ""
    lot: str = ""
    regulatory_framework: str = "AUTO"
    regulatory_label: str = ""
    structured_complaint: StructuredComplaint | None = None
    similar_incidents: list[dict[str, Any]] = Field(default_factory=list)
    graph_context: dict[str, Any] = Field(default_factory=dict)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    digital_twin_results: list[dict[str, Any]] = Field(default_factory=list)
    sbom_components: list[dict[str, Any]] = Field(default_factory=list)
    cybersecurity_findings: list[dict[str, Any]] = Field(default_factory=list)
    cybersecurity_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_collected: list[EvidenceItem] = Field(default_factory=list)
    risk_assessment: RiskAssessment | None = None
    capa_sections: list[CapaSection] = Field(default_factory=list)
    capa_draft: str = ""
    capa_closure_status: str = ""
    capa_closure_tier: str = ""
    capa_closure_rationale: str = ""
    capa_closure_required_action: str = ""
    capa_closure_disclaimer: str = ""
    closure_blocked_reason: str = ""
    evidence_chain_source_types: list[str] = Field(default_factory=list)
    evidence_chain_node_ids: list[str] = Field(default_factory=list)
    evidence_chain_classes: list[str] = Field(default_factory=list)
    evidence_chain_controlled_node_ids: list[str] = Field(default_factory=list)
    evidence_chain_simulated_node_ids: list[str] = Field(default_factory=list)
    evidence_chain_blocking_node_ids: list[str] = Field(default_factory=list)
    evidence_chain_excluded_context_node_ids: list[str] = Field(default_factory=list)
    investigation_scope: dict[str, Any] = Field(default_factory=dict)
    audit_findings: list[AuditFinding] = Field(default_factory=list)
    trace_decay_alerts: list[dict[str, Any]] = Field(default_factory=list)
    ai_reasoning: dict[str, Any] = Field(default_factory=dict)
    agent_debug: dict[str, Any] = Field(default_factory=dict)
    trace_ai: dict[str, Any] = Field(default_factory=dict)
    status: str = "initialized"
    errors: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)

    def add_event(self, agent: str, action: str, status: str = "info", **data: Any) -> None:
        self.trace.append(TraceEvent(agent=agent, action=action, status=status, data=data))
