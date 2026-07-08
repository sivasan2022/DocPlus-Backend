from __future__ import annotations

import re
from typing import Any

from backend.graph.schema import (
    CAPA_BLOCKING_EVIDENCE_CLASSES,
    CAPA_BLOCKING_SOURCE_TYPES,
    EvidenceClass,
    SourceType,
    normalize_evidence_class,
    normalize_source_type,
)
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.observability import traced
from m2_agents.core.regulatory import regulatory_label
from m2_agents.core.state import CapaSection, GraphState
from m2_agents.tools import graph_tools


CAPA_STATUS_CLOSED = "Closed"
CAPA_STATUS_SIMULATED_APPROVAL = "Pending Engineering Approval - Simulated Evidence Sufficient"
CAPA_STATUS_INSUFFICIENT = "Pending - Insufficient Evidence"
SIMULATED_EVIDENCE_DISCLAIMER = (
    "Hackathon prototype disclaimer: this AI-generated investigation is supported by digital-twin or firmware-traceability "
    "simulation, not real hardware verification. Simulated results may support investigation triage, but they require formal "
    "human engineering approval before any real-world quality, regulatory, release, or closure reliance."
)
STRICT_CAPA_BLOCKING_EVIDENCE_CLASSES = {
    EvidenceClass.HISTORICAL_CONTROLLED.value,
    EvidenceClass.CANDIDATE.value,
    EvidenceClass.NO_EVIDENCE.value,
}


class CapaAgent(BaseAgent):
    name = "capa"

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "draft CAPA with citations"):
            state.capa_sections = []
            state.investigation_scope = self._build_investigation_scope(state)
            self._apply_capa_closure_gate(state)
            scoped_requirement_ids = [
                item.get("requirement_id")
                for item in state.investigation_scope.get("requirements_scope", [])
                if item.get("requirement_id")
            ]
            capa_context = graph_tools.get_capa_context(state.device_id, scoped_requirement_ids)
            state.graph_context["capa_context"] = capa_context
            state.capa_sections = self._build_professional_capa_sections(state, capa_context)
            for section in state.capa_sections:
                section.source_types = state.evidence_chain_source_types
                section.evidence_classes = state.evidence_chain_classes
            self._apply_capa_sections_to_scope(state, capa_context)
            state.capa_draft = self._to_markdown(state)
            if not self._valid_citations(state.capa_draft):
                state.errors.append("CAPA citation validation failed")
            state.agent_debug[self.name] = {
                "outcome": {
                    "section_count": len(state.capa_sections),
                    "capa_sections": [section.model_dump() for section in state.capa_sections],
                    "investigation_scope": state.investigation_scope,
                    "capa_closure_status": state.capa_closure_status,
                    "closure_blocked_reason": state.closure_blocked_reason,
                    "capa_closure_tier": state.capa_closure_tier,
                    "capa_closure_rationale": state.capa_closure_rationale,
                    "capa_closure_required_action": state.capa_closure_required_action,
                    "evidence_chain_source_types": state.evidence_chain_source_types,
                    "evidence_chain_classes": state.evidence_chain_classes,
                    "evidence_chain_node_ids": state.evidence_chain_node_ids,
                    "evidence_chain_controlled_node_ids": state.evidence_chain_controlled_node_ids,
                    "evidence_chain_simulated_node_ids": state.evidence_chain_simulated_node_ids,
                    "evidence_chain_blocking_node_ids": state.evidence_chain_blocking_node_ids,
                    "evidence_chain_excluded_context_node_ids": state.evidence_chain_excluded_context_node_ids,
                },
                "graph_fetches": [
                    {
                        "tool": "graph_tools.get_audit_scope",
                        "purpose": "Select complaint-scoped requirements, tests, and risks for investigation and M4 handoff.",
                        "selected_requirement_count": len(scoped_requirement_ids),
                        "requirement_ids": scoped_requirement_ids,
                        "requirements_scope": state.investigation_scope.get("requirements_scope", []),
                        "verification_scope": state.investigation_scope.get("verification_scope", []),
                        "audit_shadow_findings": state.investigation_scope.get("audit_shadow_findings", []),
                    },
                    {
                        "tool": "graph_tools.get_capa_context",
                        "purpose": "Fetch scoped M1 CAPA/readiness context used to draft corrective and preventive action language.",
                        "requirement_ids": scoped_requirement_ids,
                        "context": capa_context,
                    },
                ],
            }
            state.status = "capa_drafted"
        return state

    def _best_citation(self, state: GraphState) -> str:
        if state.evidence_collected:
            return state.evidence_collected[0].citation
        return self._citation("M1 graph evidence", 0.7)

    def _to_markdown(self, state: GraphState) -> str:
        lines = [
            "# CAPA Draft",
            "",
            f"Device: `{state.device_id}`",
            f"Framework: `{state.regulatory_label or state.regulatory_framework}`",
            "",
        ]
        for section in state.capa_sections:
            lines.extend([f"## {section.title}", f"{section.body} {section.citation}", ""])
        return "\n".join(lines).strip()

    def _build_professional_capa_sections(self, state: GraphState, capa_context: dict[str, Any]) -> list[CapaSection]:
        complaint = state.structured_complaint
        device = state.graph_context.get("device", {})
        device_name = device.get("name", state.device_id)
        severity = complaint.severity if complaint else "Medium"
        framework = state.regulatory_label or regulatory_label(state.regulatory_framework, state.graph_context.get("standards"))
        risk = state.risk_assessment
        scope = state.investigation_scope
        requirement_count = capa_context.get("scoped_requirement_count", len(scope.get("requirements_scope", [])))
        verification_count = capa_context.get("verification_record_count", len(scope.get("verification_scope", [])))
        evidence_gap_count = capa_context.get("evidence_gap_count", 0)
        open_capa_count = capa_context.get("open_capa_count", scope.get("risk_and_capa_scope", {}).get("open_capa_count", 0))
        weak_items = [
            row
            for row in scope.get("verification_scope", [])
            if row.get("trace_decay_status") in {"missing", "stale", "usable with caution"}
        ]
        plain_context = capa_context.get("plain_language_summary", {}) if isinstance(capa_context.get("plain_language_summary"), dict) else {}
        review_scope = plain_context.get(
            "review_scope",
            "Relevant device requirements, verification records, risk controls, and open quality actions were reviewed for the complaint scope.",
        )
        evidence_status = plain_context.get(
            "evidence_status",
            "Some verification evidence still needs Quality review before CAPA closure.",
        )
        review_notes = []
        if weak_items:
            review_notes.append(self._count_phrase(len(weak_items), "verification record needs", "verification records need") + " Quality review")
        if evidence_gap_count:
            review_notes.append(self._count_phrase(evidence_gap_count, "evidence gap remains", "evidence gaps remain"))
        if open_capa_count:
            review_notes.append(self._count_phrase(open_capa_count, "related quality action remains", "related quality actions remain") + " open")
        evidence_review_status = (
            "; ".join(review_notes) + "."
            if review_notes
            else "No major evidence gap was identified in the current complaint scope."
        )
        root_causes = scope.get("root_cause_scope", [])
        primary_cause = root_causes[0] if root_causes else {}
        recurrence_window = "six months after the corrected release or field correction"
        issue = self._issue_statement(state)
        short_issue = self._short_issue(state)
        symptoms = self._symptom_phrase(complaint)
        affected_area = self._affected_area_phrase(state)
        trigger = self._trigger_condition(state.raw_complaint)
        impact = self._quality_impact(state)
        root_statement = self._plain_root_statement(state, primary_cause)
        test_focus = self._test_focus(state)
        evidence_needed = self._evidence_needed(state)
        owner = self._owner_for_issue(state)
        closure_focus = self._closure_focus(state)
        injury_statement = self._injury_statement(state.raw_complaint)
        gate_note = self._closure_gate_note(state)
        risk_confidence_note = self._risk_confidence_note(state)
        risk_confidence_basis = self._risk_confidence_basis(state)
        closure_status_narrative = self._closure_status_narrative(state, closure_focus)
        corrective_closure_text = self._corrective_closure_text(state)
        effectiveness_expectation = self._effectiveness_expectation(state, closure_focus, affected_area)
        effectiveness_criteria = self._effectiveness_criteria(state, closure_focus, recurrence_window)
        closure_decision = self._closure_decision_text(state)
        m1_citation = self._citation("M1 CAPA context, scoped requirements, risks, and verification links", 0.93)
        m2_citation = self._citation("M2 complaint scope, risk assessment, and CAPA reasoning", 0.91)
        m4_citation = self._best_citation(state)
        return [
            CapaSection(
                title="Problem Statement",
                body=(
                    f"A {severity.lower()} priority complaint was opened for {device_name}. The user reported: {issue} "
                    f"In practical terms, the concern is that {impact}. The complaint record confirms the reported condition; "
                    "the exact technical cause and final correction remain under investigation until objective evidence is reviewed."
                ),
                citation=m2_citation,
            ),
            CapaSection(
                title="Complaint Summary",
                body=(
                    f"Reported condition: {short_issue}. Affected area: {affected_area}. Reported timing or trigger: {trigger}. "
                    f"Observed symptom signals: {symptoms}. {injury_statement}"
                ),
                citation=m2_citation,
            ),
            CapaSection(
                title="Immediate Containment Actions",
                body=(
                    f"Preserve the complaint evidence for {affected_area}, including returned-unit information, event logs, service records, "
                    "photos, user statements, configuration, lot or serial traceability, and software or hardware version where applicable. "
                    "Screen recent complaints for similar symptoms and issue interim support, service, or use instructions if the risk review "
                    "shows potential user or patient impact."
                ),
                citation=m1_citation,
            ),
            CapaSection(
                title="Investigation",
                body=(
                    f"{review_scope} The investigation focused on the reported condition and the affected area: {affected_area}. "
                    f"For this complaint the scoped area is {affected_area}, with emphasis on {test_focus}. Engineering and Quality reviewed "
                    f"the available design expectations, test records, risk controls, event evidence, and related quality actions. "
                    f"{evidence_review_status} {evidence_status} {risk_confidence_basis}"
                ),
                citation=m1_citation,
            ),
            CapaSection(
                title="Root Cause Analysis",
                body=(
                    f"{root_statement} This is a probable cause, not a final confirmed cause, because the CAPA record still needs "
                    f"{evidence_needed} showing whether the suspected cause is truly responsible."
                ),
                citation=m2_citation,
            ),
            CapaSection(
                title="Why CAPA Remains Open",
                body=(
                    f"{closure_status_narrative} {risk_confidence_note} {gate_note}"
                ),
                citation=m2_citation,
            ),
            CapaSection(
                title="Risk Assessment",
                body=(
                    f"The main hazard is that {impact}. "
                    f"The current risk level is {risk.risk_level if risk else 'under review'} and the complaint should remain in active risk "
                    f"review under {framework} until patient impact, recurrence potential, and residual risk acceptability are documented. "
                    f"{risk_confidence_note}"
                ),
                citation=state.risk_assessment.citation if state.risk_assessment else m2_citation,
            ),
            CapaSection(
                title="Corrective Actions",
                body=(
                    f"Reproduce or simulate the reported condition for {affected_area} under the reported use conditions. "
                    f"{owner} should review {test_focus}, identify the confirmed failure mode, correct the issue or document why it cannot "
                    f"be reproduced. {corrective_closure_text}"
                ),
                citation=m4_citation,
            ),
            CapaSection(
                title="Preventive Actions",
                body=(
                    f"Update the design, service, or release checklist so future changes affecting {affected_area} include complaint-specific "
                    f"verification for {symptoms}. Add regression or trend monitoring where appropriate, and require evidence review before "
                    "Quality approves a similar change or field disposition."
                ),
                citation=self._citation("M2 Trace Decay and AuditShadow workflows", 0.9),
            ),
            CapaSection(
                title="Verification of Effectiveness",
                body=effectiveness_expectation,
                citation=self._citation("AuditShadow findings, M1 verification links, and CAPA closure criteria", 0.92),
            ),
            CapaSection(
                title="CAPA Effectiveness Criteria",
                body=effectiveness_criteria,
                citation=self._citation("AuditShadow findings, M1 verification links, and CAPA closure criteria", 0.92),
            ),
            CapaSection(
                title="Lessons Learned",
                body=(
                    f"Future complaint handling for {affected_area} should connect user-reported symptoms, objective evidence, risk controls, "
                    "and verification records early. Similar changes or service decisions should include test coverage for the reported condition "
                    "and review of whether existing controls remain effective."
                ),
                citation=m2_citation,
            ),
            CapaSection(
                title="CAPA Closure",
                body=closure_decision,
                citation=self._citation("CAPA closure criteria and quality approval workflow", 0.9),
            ),
        ]

    def _apply_capa_sections_to_scope(self, state: GraphState, capa_context: dict[str, Any]) -> None:
        risk_scope = state.investigation_scope.setdefault("risk_and_capa_scope", {})
        section_map = {section.title: section.body for section in state.capa_sections}
        risk_scope["professional_capa_plan"] = [
            {
                "section": section.title,
                "narrative": section.body,
                "citation": section.citation,
                "source_types": section.source_types,
                "evidence_classes": section.evidence_classes,
            }
            for section in state.capa_sections
        ]
        risk_scope["problem_statement"] = section_map.get("Problem Statement", "")
        risk_scope["complaint_summary"] = section_map.get("Complaint Summary", "")
        risk_scope["investigation_summary"] = section_map.get("Investigation", "")
        risk_scope["root_cause_status"] = section_map.get("Root Cause Analysis", "")
        risk_scope["risk_reportability"] = section_map.get("Risk Assessment", "")
        risk_scope["containment_actions"] = [section_map.get("Immediate Containment Actions", "")]
        risk_scope["corrective_actions"] = [section_map.get("Corrective Actions", "")]
        risk_scope["preventive_actions"] = [section_map.get("Preventive Actions", "")]
        risk_scope["effectiveness_checks"] = [section_map.get("Verification of Effectiveness", "")]
        risk_scope["effectiveness_criteria"] = section_map.get("CAPA Effectiveness Criteria", "")
        risk_scope["lessons_learned"] = section_map.get("Lessons Learned", "")
        risk_scope["closure_summary"] = section_map.get("CAPA Closure", "")
        risk_scope["capa_closure_status"] = state.capa_closure_status
        risk_scope["capa_closure_tier"] = state.capa_closure_tier
        risk_scope["capa_closure_rationale"] = state.capa_closure_rationale
        risk_scope["capa_closure_required_action"] = state.capa_closure_required_action
        risk_scope["capa_closure_disclaimer"] = state.capa_closure_disclaimer
        risk_scope["closure_blocked_reason"] = state.closure_blocked_reason
        risk_scope["evidence_chain_source_types"] = state.evidence_chain_source_types
        risk_scope["evidence_chain_classes"] = state.evidence_chain_classes
        risk_scope["evidence_chain_node_ids"] = state.evidence_chain_node_ids
        risk_scope["evidence_chain_controlled_node_ids"] = state.evidence_chain_controlled_node_ids
        risk_scope["evidence_chain_simulated_node_ids"] = state.evidence_chain_simulated_node_ids
        risk_scope["evidence_chain_blocking_node_ids"] = state.evidence_chain_blocking_node_ids
        risk_scope["evidence_chain_excluded_context_node_ids"] = state.evidence_chain_excluded_context_node_ids
        risk_scope["risk_evidence_confidence"] = self._risk_evidence_confidence_payload(state)
        risk_scope["m1_capa_context"] = {
            "scoped_requirement_count": capa_context.get("scoped_requirement_count", 0),
            "verification_record_count": capa_context.get("verification_record_count", 0),
            "evidence_gap_count": capa_context.get("evidence_gap_count", 0),
            "open_capa_count": capa_context.get("open_capa_count", 0),
        }
        handoff = state.investigation_scope.setdefault("m4_handoff", {})
        report_sections = handoff.setdefault("report_sections", [])
        if "Professional CAPA Plan" not in report_sections:
            report_sections.append("Professional CAPA Plan")

    def _apply_capa_closure_gate(self, state: GraphState) -> None:
        chain = self._evidence_chain(state)
        source_types = sorted({item["source_type"] for item in chain if item.get("source_type")})
        evidence_classes = sorted({item["evidence_class"] for item in chain if item.get("evidence_class")})
        source_blockers = [
            item
            for item in chain
            if item.get("source_type") in CAPA_BLOCKING_SOURCE_TYPES and item.get("node_id")
        ]
        class_blockers = [
            item
            for item in chain
            if item.get("evidence_class") in STRICT_CAPA_BLOCKING_EVIDENCE_CLASSES and item.get("node_id")
        ]
        controlled_items = [
            item
            for item in chain
            if item.get("evidence_class") == EvidenceClass.CONTROLLED_VERIFICATION.value and item.get("node_id")
        ]
        simulated_items = [
            item
            for item in chain
            if item.get("evidence_class") == EvidenceClass.SIMULATED.value and item.get("node_id")
        ]
        blocked_ids = self._unique_ids([*source_blockers, *class_blockers])
        state.evidence_chain_source_types = source_types
        state.evidence_chain_classes = evidence_classes
        state.evidence_chain_node_ids = self._unique_ids(chain)
        state.evidence_chain_controlled_node_ids = self._unique_ids(controlled_items)
        state.evidence_chain_simulated_node_ids = self._unique_ids(simulated_items)
        state.evidence_chain_blocking_node_ids = blocked_ids

        total_items = len(chain)
        blocking_count = len(blocked_ids)
        blocking_ratio = (blocking_count / total_items) if total_items else 1.0
        materiality_threshold = 0.15
        hard_block = bool(source_blockers) or (
            bool(class_blockers) and blocking_ratio > materiality_threshold
        )

        if hard_block:
            reasons = []
            if source_blockers:
                reasons.append(
                    "evidence chain includes synthetic node(s): "
                    + ", ".join(self._unique_ids(source_blockers)[:12])
                )
            if class_blockers:
                reasons.append(
                    f"evidence class blockers exceed materiality threshold "
                    f"({blocking_count}/{total_items} = {blocking_ratio:.0%}): "
                    + ", ".join(self._unique_ids(class_blockers)[:12])
                )
            state.capa_closure_status = CAPA_STATUS_INSUFFICIENT
            state.capa_closure_tier = "insufficient_evidence"
            state.capa_closure_rationale = (
                "The scoped evidence chain still contains synthetic source data, or a material share of "
                "candidate/historical/no-evidence items, that cannot support closure."
            )
            state.capa_closure_required_action = (
                "Attach current controlled verification evidence, resolve synthetic/candidate/historical evidence gaps, "
                "and obtain Quality/Regulatory approval before closure."
            )
            state.capa_closure_disclaimer = ""
            state.closure_blocked_reason = "Closure blocked: " + "; ".join(reasons) + "."
            return

        if not chain:
            state.capa_closure_status = CAPA_STATUS_INSUFFICIENT
            state.capa_closure_tier = "insufficient_evidence"
            state.capa_closure_rationale = "No current-complaint evidence items were available in the scoped CAPA closure chain."
            state.capa_closure_required_action = "Collect and attach current controlled verification evidence before closure."
            state.capa_closure_disclaimer = ""
            state.closure_blocked_reason = "Closure blocked: no current-complaint evidence items were available."
            return

        if simulated_items or class_blockers:
            controlled_count = len(controlled_items)
            simulated_count = len(simulated_items)
            state.capa_closure_status = CAPA_STATUS_SIMULATED_APPROVAL
            state.capa_closure_tier = "simulated_evidence_approval"
            state.capa_closure_rationale = (
                f"This CAPA is supported by {controlled_count} controlled verification record(s); "
                f"{simulated_count} simulated result(s) and {len(class_blockers)} minor evidence-class gap(s) remain "
                f"below the materiality threshold ({blocking_ratio:.0%})."
            )
            state.capa_closure_required_action = (
                "Formal Engineering review must approve the simulated/minor-gap findings and decide whether "
                "additional physical verification is required."
            )
            state.capa_closure_disclaimer = SIMULATED_EVIDENCE_DISCLAIMER
            state.closure_blocked_reason = ""
            return

        state.capa_closure_status = CAPA_STATUS_CLOSED
        state.capa_closure_tier = "closed_controlled_verification"
        state.capa_closure_rationale = (
            f"The scoped evidence chain contains {len(controlled_items)} current controlled verification record(s) and no simulated, "
            "synthetic, candidate, historical, or no-evidence items."
        )
        state.capa_closure_required_action = "Maintain the approved closure package and reviewer signatures in the controlled quality record."
        state.capa_closure_disclaimer = ""
        state.closure_blocked_reason = ""

    def _evidence_chain(self, state: GraphState) -> list[dict[str, str]]:
        chain: list[dict[str, str]] = []
        similar_context_ids = self._similar_incident_node_ids(state)
        excluded_context: list[dict[str, str]] = []
        for item in state.evidence_collected:
            node_id = item.source_node_id or item.id
            if self._is_similar_incident_context(node_id, similar_context_ids):
                excluded_context.append(
                    {
                        "node_id": node_id,
                        "source_type": normalize_source_type(item.source_type, SourceType.INFERRED),
                        "evidence_class": normalize_evidence_class(item.evidence_class),
                        "claim": "Similar/prior complaint context excluded from CAPA closure gate",
                    }
                )
                continue
            chain.append(
                {
                    "node_id": node_id,
                    "source_type": normalize_source_type(item.source_type, SourceType.INFERRED),
                    "evidence_class": normalize_evidence_class(item.evidence_class),
                    "claim": f"Evidence item for {item.hypothesis_id or 'complaint'}",
                }
            )
        for hypothesis in state.hypotheses:
            for fact in hypothesis.supporting_facts:
                node_id = str(fact.get("node_id", ""))
                if self._is_similar_incident_context(node_id, similar_context_ids) or "prior complaint" in str(fact.get("claim", "")).lower():
                    excluded_context.append(
                        {
                            "node_id": node_id,
                            "source_type": normalize_source_type(fact.get("source_type"), SourceType.INFERRED),
                            "evidence_class": "",
                            "claim": "Root-cause similar/prior complaint context excluded from CAPA closure gate",
                        }
                    )
                    continue
        state.evidence_chain_excluded_context_node_ids = self._unique_ids(excluded_context)
        return chain

    def _unique_ids(self, chain: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in chain:
            node_id = str(item.get("node_id") or item.get("id") or "").strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            result.append(node_id)
        return result

    def _similar_incident_node_ids(self, state: GraphState) -> set[str]:
        incidents: list[dict[str, Any]] = []
        if state.structured_complaint and state.structured_complaint.similar_incidents:
            incidents.extend(state.structured_complaint.similar_incidents)
        incidents.extend(state.similar_incidents or [])
        ids: set[str] = set()
        for incident in incidents:
            if not isinstance(incident, dict):
                continue
            for key in ["source_node_id", "node_id", "id", "complaint_id"]:
                value = str(incident.get(key) or "").strip()
                if value:
                    ids.add(value)
        return ids

    def _is_similar_incident_context(self, node_id: str, similar_context_ids: set[str]) -> bool:
        text = str(node_id or "").strip()
        return bool(text and text in similar_context_ids)

    def _closure_status_narrative(self, state: GraphState, closure_focus: str) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return (
                "The CAPA closure gate is closed because all scoped evidence items are current controlled verification records. "
                "Quality and Regulatory reviewers should preserve the signed closure package and continue normal complaint monitoring."
            )
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                "The CAPA is not blocked for lack of evidence. The scoped evidence chain has controlled verification plus "
                "digital-twin or firmware-traceability simulation support, so the remaining gate is formal Engineering approval. "
                f"Engineering must review the simulated findings against {closure_focus}, approve or reject their applicability, "
                "and record whether physical/controlled verification is still required."
            )
        return (
            "The CAPA status is open because the available information is enough to justify corrective action, but not enough to "
            "close the quality record. Closure requires missing decisions or evidence: confirm or rule out the root cause, complete "
            f"the correction or documented non-reproducibility rationale, verify {closure_focus} with approved testing, and obtain "
            "Quality/Regulatory approval of the residual risk decision."
        )

    def _corrective_closure_text(self, state: GraphState) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return "Maintain the controlled evidence package and closure approvals in the quality record."
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                "Engineering must review and approve the simulated findings before closure; if Engineering rejects the simulation "
                "or deems it insufficient, open targeted physical or controlled verification before final approval."
            )
        return "Rerun targeted verification, attach approved objective evidence, and resolve evidence gaps before release or closure."

    def _effectiveness_expectation(self, state: GraphState, closure_focus: str, affected_area: str) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return (
                f"Effectiveness is supported by current controlled verification of {closure_focus}; Quality should confirm the "
                f"affected area ({affected_area}) remains compliant through complaint monitoring and approved record retention."
            )
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                f"Effectiveness is supported by controlled verification records plus simulated analysis of {closure_focus}. "
                "Before closure, an Engineering reviewer must approve the digital-twin or firmware-traceability simulation result, "
                "document why it is sufficient for this complaint, or require additional physical verification."
            )
        return (
            f"Effectiveness must be verified by successful targeted testing of {closure_focus}, review of complaint or service trends, "
            f"and independent Quality review confirming that the affected area ({affected_area}) meets approved design and risk-control requirements. "
            "The effectiveness package must replace candidate, historical, synthetic, or no-evidence gaps with approved objective evidence."
        )

    def _effectiveness_criteria(self, state: GraphState, closure_focus: str, recurrence_window: str) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return (
                f"The CAPA is effective when controlled evidence shows {closure_focus} meets the approved specification, the closure package "
                f"is signed, and no related complaint recurs during {recurrence_window}."
            )
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                f"The CAPA is effective when the behavior for {closure_focus} is supported by current controlled records and the digital-twin "
                "or firmware-traceability simulation is formally approved by Engineering. Closure requires documented Engineering sign-off; "
                "additional physical verification is required only if Engineering does not accept the simulated support."
            )
        return (
            f"The CAPA is effective when the reported condition no longer occurs or is formally ruled out, the behavior for {closure_focus} is verified "
            f"against the approved specification, all targeted verification activities pass, sufficient objective evidence is attached, "
            f"and no related complaint recurs during {recurrence_window}."
        )

    def _closure_decision_text(self, state: GraphState) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return (
                f"Closure decision: closed. {state.capa_closure_rationale} The CAPA record should retain the controlled evidence chain, "
                "reviewer signatures, and complaint monitoring plan."
            )
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                f"Closure decision: pending Engineering approval. {state.capa_closure_rationale} "
                f"{state.capa_closure_required_action} {state.capa_closure_disclaimer}"
            )
        return (
            "Closure decision: keep the CAPA open for insufficient evidence. The issue may warrant corrective action, but the scoped "
            "evidence chain still contains evidence that cannot support closure. "
            f"{state.capa_closure_required_action or 'Attach current controlled verification evidence before closure.'}"
        )

    def _closure_gate_note(self, state: GraphState) -> str:
        if state.capa_closure_tier == "closed_controlled_verification":
            return (
                "Closure gate result: closed. The scoped evidence chain is entirely current controlled verification evidence, "
                "with no simulated, synthetic, candidate, historical, or no-evidence items."
            )
        if state.capa_closure_tier == "simulated_evidence_approval":
            return (
                f"{state.capa_closure_rationale} It remains pending formal Engineering approval before closure; "
                "simulated results support the investigation but do not substitute for physical or controlled verification. "
                f"{state.capa_closure_disclaimer}"
            )
        if state.closure_blocked_reason:
            return (
                "This CAPA remains open because the scoped evidence chain is insufficient for closure; "
                f"{state.closure_blocked_reason}"
            )
        return "CAPA closure gate has not identified a final closure disposition; normal Quality/Regulatory review is still required."

    def _risk_confidence_note(self, state: GraphState) -> str:
        risk = state.risk_assessment
        if not risk:
            return "Risk evidence confidence has not yet been calculated."
        score = getattr(risk, "evidence_confidence_score", 0.0)
        uncertainty = risk.uncertainty_flag or "Evidence confidence requires Quality review."
        return (
            f"Risk evidence confidence is {risk.confidence_in_evidence or 'not assessed'} "
            f"(score {score:.2f}); {uncertainty}"
        )

    def _risk_confidence_basis(self, state: GraphState) -> str:
        risk = state.risk_assessment
        if not risk:
            return "Risk evidence confidence was not available for CAPA drafting."
        basis = getattr(risk, "evidence_confidence_basis", "") or risk.uncertainty_flag
        return f"Risk evidence basis: {basis}"

    def _risk_evidence_confidence_payload(self, state: GraphState) -> dict[str, Any]:
        risk = state.risk_assessment
        if not risk:
            return {}
        return {
            "confidence": risk.confidence_in_evidence,
            "score": risk.evidence_confidence_score,
            "uncertainty": risk.uncertainty_flag,
            "basis": risk.evidence_confidence_basis,
            "rule": risk.evidence_confidence_rule,
            "class_breakdown": risk.evidence_class_breakdown,
            "drivers": risk.evidence_confidence_drivers,
        }

    def _evidence_titles(self, state: GraphState) -> str:
        titles = []
        seen = set()
        for item in state.evidence_collected:
            title = str(item.source or item.id)
            if title in seen:
                continue
            seen.add(title)
            titles.append(title)
            if len(titles) == 3:
                break
        return ", ".join(titles) if titles else "no controlled evidence retrieved yet"

    def _count_phrase(self, count: int, singular: str, plural: str) -> str:
        return f"{count} {singular if count == 1 else plural}"

    def _issue_statement(self, state: GraphState, limit: int = 260) -> str:
        text = re.sub(r"\s+", " ", state.raw_complaint or "").strip()
        if not text:
            return "a complaint condition that requires investigation."
        text = text.rstrip(".")
        if len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        return f"{text}."

    def _short_issue(self, state: GraphState) -> str:
        complaint = state.structured_complaint
        if complaint and complaint.raw_summary:
            return self._issue_statement(state, 180).rstrip(".")
        symptoms = self._symptom_phrase(complaint)
        return f"User-reported {symptoms}"

    def _symptom_phrase(self, complaint: Any) -> str:
        if not complaint or not complaint.symptom_codes:
            return "reported device behavior"
        summary = str(getattr(complaint, "raw_summary", "") or "").lower()
        if any(term in summary for term in ["battery", "charge", "charging"]) and any(term in summary for term in ["drain", "drains", "shutdown", "shut", "power"]):
            return "battery drain or power shutdown"
        if any(term in summary for term in ["reading", "readings", "accuracy", "inaccurate", "inconsistent", "spo2", "pulse"]):
            return "inconsistent or inaccurate measurement readings"
        if any(term in summary for term in ["bluetooth", "pair", "connect", "sync", "app", "data"]):
            return "connectivity, pairing, or data-transfer failure"
        if any(term in summary for term in ["display", "screen", "freeze", "blank", "flicker"]):
            return "display or user-interface malfunction"
        if any(term in summary for term in ["alarm", "alert", "notification"]):
            return "alarm or notification behavior"
        if any(term in summary for term in ["sensor", "probe", "clip", "fit", "finger", "pediatric", "uncomfortable"]):
            return "sensor, accessory, fit, or usability concern"
        if any(term in summary for term in ["water", "dust", "drop", "crack", "damage", "cleaning", "disinfect"]):
            return "environmental, mechanical, or cleaning-related concern"
        stop_terms = {"device", "patient", "reported", "unusually", "during", "after", "before", "with", "without", "compared", "bedside"}
        terms = []
        for code in complaint.symptom_codes[:5]:
            term = str(code).replace("SYMPTOM_", "").replace("_", " ").strip().lower()
            if term and term not in terms and term not in stop_terms:
                terms.append(term)
        return ", ".join(terms) if terms else "reported device behavior"

    def _affected_area_phrase(self, state: GraphState) -> str:
        complaint = state.structured_complaint
        function_scope = [
            item.get("category", "")
            for item in state.investigation_scope.get("functional_scope", [])
            if item.get("category")
        ]
        if function_scope:
            return ", ".join(dict.fromkeys(function_scope))
        if complaint and complaint.affected_component_name:
            return complaint.affected_component_name
        return state.device_id or "the affected device function"

    def _quality_impact(self, state: GraphState) -> str:
        text = (state.raw_complaint or "").lower()
        if any(term in text for term in ["alarm", "alert", "notification"]):
            return "a user may not receive a safety or status notification at the expected time"
        if any(term in text for term in ["incorrect", "inaccurate", "wrong", "drift", "unstable", "inconsistent", "measurement", "reading"]):
            return "the device may provide information that is inaccurate, unstable, or difficult to rely on"
        if any(term in text for term in ["battery", "power", "shutdown", "restart", "charge", "charging", "won't turn on", "will not turn on"]):
            return "the device may become unavailable or lose power during intended use"
        if any(term in text for term in ["connect", "bluetooth", "pair", "app", "sync", "upload", "data transfer"]):
            return "device data or connected-use functions may be unavailable or delayed"
        if any(term in text for term in ["sensor", "probe", "cable", "clip", "fit", "finger", "pediatric", "uncomfortable"]):
            return "the device may not capture a reliable signal or may create a usability concern"
        if any(term in text for term in ["water", "dust", "drop", "crack", "damage", "cleaning", "disinfect"]):
            return "device reliability or performance may be reduced after the reported environmental or handling condition"
        return "the device may not perform as expected and the user may need additional support or alternate monitoring"

    def _plain_root_statement(self, state: GraphState, primary_cause: dict[str, Any]) -> str:
        cause = str(primary_cause.get("cause_statement") or "").strip()
        if cause and "graph shows" not in cause.lower():
            return f"The current root-cause hypothesis is: {cause}"
        affected_area = self._affected_area_phrase(state)
        trigger = self._trigger_condition(state.raw_complaint)
        return (
            f"The current root-cause hypothesis is that a design, process, service, use, or evidence-control issue related to "
            f"{affected_area} may have contributed to the reported condition under the trigger/timing: {trigger}."
        )

    def _test_focus(self, state: GraphState) -> str:
        text = (state.raw_complaint or "").lower()
        if any(term in text for term in ["alarm", "alert", "notification"]):
            return "alarm or notification timing, event logs, and risk-control behavior"
        if any(term in text for term in ["display", "screen", "ui", "freeze", "blank", "flicker"]):
            return "user-interface response, display behavior, event logs, and usability evidence"
        if any(term in text for term in ["battery", "power", "shutdown", "restart", "charge", "charging"]):
            return "power-path behavior, battery/charger characterization, service history, and safety-state evidence"
        if any(term in text for term in ["measurement", "reading", "accuracy", "spo2", "pulse", "sensor", "signal"]):
            return "measurement accuracy, sensor signal quality, calibration, and verification evidence"
        if any(term in text for term in ["connect", "bluetooth", "pair", "app", "sync", "upload", "data"]):
            return "connectivity behavior, data transfer logs, app/device compatibility, and workflow verification"
        if any(term in text for term in ["water", "dust", "drop", "damage", "cleaning", "disinfect"]):
            return "environmental exposure, mechanical condition, cleaning/service records, and performance verification"
        return "returned-unit evidence, complaint history, applicable requirements, risk controls, and verification records"

    def _evidence_needed(self, state: GraphState) -> str:
        text = (state.raw_complaint or "").lower()
        if any(term in text for term in ["software", "firmware", "update", "app", "bluetooth", "sync"]):
            return "engineering reproduction results, configuration or software-version review, logs, and approved verification evidence"
        if any(term in text for term in ["battery", "power", "charge", "shutdown", "restart"]):
            return "returned-unit evaluation, power or battery characterization, service history, and approved verification evidence"
        if any(term in text for term in ["sensor", "measurement", "reading", "accuracy", "fit", "finger"]):
            return "returned-unit or accessory review, measurement/signal testing, use-condition review, and approved verification evidence"
        return "returned-unit review, complaint evidence, trend review, and approved verification evidence"

    def _owner_for_issue(self, state: GraphState) -> str:
        text = (state.raw_complaint or "").lower()
        if any(term in text for term in ["software", "firmware", "app", "bluetooth", "sync", "display", "alarm"]):
            return "Software/System Engineering"
        if any(term in text for term in ["battery", "power", "charge", "shutdown", "restart"]):
            return "Electrical/Power Engineering"
        if any(term in text for term in ["sensor", "measurement", "accuracy", "fit", "finger", "pediatric"]):
            return "Systems/Verification Engineering"
        if any(term in text for term in ["water", "dust", "drop", "cleaning", "damage"]):
            return "Reliability/Mechanical Engineering"
        return "Engineering and Quality"

    def _closure_focus(self, state: GraphState) -> str:
        text = (state.raw_complaint or "").lower()
        if any(term in text for term in ["alarm", "alert", "notification"]):
            return "notification timing and user-visible response"
        if any(term in text for term in ["display", "screen", "ui", "freeze", "blank"]):
            return "display/user-interface behavior"
        if any(term in text for term in ["battery", "power", "charge", "shutdown", "restart"]):
            return "power availability and safe-state behavior"
        if any(term in text for term in ["measurement", "reading", "accuracy", "spo2", "pulse", "sensor"]):
            return "measurement accuracy and signal reliability"
        if any(term in text for term in ["connect", "bluetooth", "pair", "app", "sync", "upload", "data"]):
            return "connectivity and data-transfer behavior"
        return "the affected function under the reported use condition"

    def _injury_statement(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(term in lowered for term in ["death", "serious injury", "injury", "harm", "hospitalized"]):
            return "The complaint text indicates possible patient or user harm, so escalation and reportability review are required."
        return "No patient or user injury is stated in the complaint text; the risk review must still confirm potential safety impact."

    def _valid_citations(self, text: str) -> bool:
        cited_lines = [line for line in text.splitlines() if line and not line.startswith("#") and not line.startswith("Device") and not line.startswith("Framework")]
        return all(re.search(r"\[Source: .+?, Confidence: (0\.\d+|1\.00)\]", line) for line in cited_lines)

    def _build_investigation_scope(self, state: GraphState) -> dict[str, Any]:
        complaint = state.structured_complaint
        device = state.graph_context.get("device", {})
        graph_current_fw = str(device.get("current_firmware", ""))
        complaint_fw = self._complaint_firmware(state, graph_current_fw)
        audit_scope = graph_tools.get_audit_scope(state.device_id)
        readiness_by_req = {
            item.get("requirement_id"): item
            for item in state.graph_context.get("readiness", {}).get("requirements", [])
        }
        selected = self._select_requirements(state, audit_scope, readiness_by_req)
        verification_scope = self._verification_scope(selected, complaint_fw)
        audit_findings = self._scope_findings(selected, verification_scope, readiness_by_req)
        risks = self._risk_items(selected)
        affected_area = self._affected_area_phrase(state)
        test_focus = self._test_focus(state)
        closure_focus = self._closure_focus(state)
        owner = self._owner_for_issue(state)
        return {
            "m2_scope_version": "DocPlus-M2-v1",
            "complaint_summary": {
                "normalized_complaint": complaint.raw_summary if complaint else state.raw_complaint[:240],
                "affected_function": self._affected_function(state),
                "trigger_condition": self._trigger_condition(state.raw_complaint),
                "firmware_version": complaint_fw,
                "serial_number": complaint.serial_number if complaint else state.serial_number,
                "lot": complaint.lot if complaint else state.lot,
                "patient_or_user_impact": self._quality_impact(state),
                "severity_assessment": complaint.severity if complaint else "Medium",
                "confidence": round(min(0.95, 0.55 + (complaint.component_match_score if complaint else 0.0) * 0.1), 2),
            },
            "functional_scope": self._functional_scope(state),
            "requirements_scope": [item["requirement_scope"] for item in selected],
            "verification_scope": verification_scope,
            "audit_shadow_findings": audit_findings,
            "root_cause_scope": self._root_cause_scope(state),
            "risk_and_capa_scope": {
                "hazards": risks,
                "risk_controls": sorted({risk.get("risk_control") or risk.get("hazard") for risk in risks if risk.get("risk_control") or risk.get("hazard")}),
                "containment_actions": [
                    f"Preserve complaint evidence for {affected_area}, including returned unit, logs, photos, service records, and customer statements.",
                    "Confirm serial/lot traceability, configuration, software or hardware version, and whether similar complaints are trending.",
                ],
                "corrective_actions": [
                    f"{owner} should reproduce or simulate the reported condition, determine the confirmed failure mode, correct or justify it, and attach approved objective evidence before closure."
                ],
                "preventive_actions": [
                    f"Update release, service, or design review checks so future changes affecting {affected_area} include verification of {closure_focus}."
                ],
                "verification_actions": [
                    f"Rerun or justify stale, missing, or weak verification evidence for the complaint-scoped requirements tied to {affected_area}.",
                    "Attach controlled test report, raw log, reviewer approval, and firmware version metadata to each verification record.",
                ],
                "effectiveness_checks": [
                    f"CAPA may close only when scoped audit findings are resolved, objective evidence is current, and {closure_focus} meets approved requirements."
                ],
            },
            "m4_handoff": {
                "include_in_report": True,
                "report_sections": [
                    "Complaint Summary",
                    "Functional Scope",
                    "Requirement Traceability",
                    "Verification and Trace Decay",
                    "AuditShadow Findings",
                    "Root Cause Scope",
                    "Risk and CAPA Direction",
                ],
                "key_evidence": self._key_evidence(state, verification_scope),
                "open_gaps": [finding["missing_or_weak_evidence"] for finding in audit_findings],
                "recommended_conclusion": self._recommended_conclusion(audit_findings),
            },
        }

    def _functional_scope(self, state: GraphState) -> list[dict[str, Any]]:
        text = state.raw_complaint.lower()
        complaint = state.structured_complaint
        rules = [
            ("Alarm Notification", ["alarm", "alert", "notification"], ["alarm_manager", "alarm_queue"]),
            ("Display and UI", ["display", "screen", "freeze", "blank", "flicker", "ui"], ["display_task"]),
            ("Firmware Update and Identity", ["firmware", "update", "version", "rollback"], ["update_identity_manager", "system_supervisor"]),
            ("Power and Battery", ["power", "battery", "shutdown", "charge"], ["power_manager"]),
            ("Measurement and Sensor Performance", ["spo2", "oxygen", "pulse", "measurement", "reading", "accuracy", "sensor", "probe", "signal"], ["measurement_core"]),
            ("Connectivity and Data Transfer", ["bluetooth", "pair", "connect", "sync", "app", "upload", "data", "wireless"], ["connectivity_manager"]),
            ("Use, Fit, and Labeling", ["fit", "finger", "pediatric", "uncomfortable", "label", "instruction", "user", "use"], ["usability_controls"]),
            ("Environmental and Mechanical Reliability", ["water", "dust", "drop", "crack", "damage", "cleaning", "disinfect", "enclosure"], ["reliability_controls"]),
            ("System Reliability", ["watchdog", "reset", "hang", "freeze", "crash"], ["system_supervisor"]),
        ]
        scope = []
        for category, terms, modules in rules:
            matched = [term for term in terms if term in text]
            if matched:
                scope.append(
                    {
                        "category": category,
                        "in_scope_reason": f"Complaint text or extracted symptoms mention: {', '.join(matched)}.",
                        "affected_modules": modules,
                        "scope_confidence": 0.85 if complaint and complaint.affected_component else 0.65,
                    }
                )
        if not scope:
            scope.append(
                {
                    "category": "System Reliability",
                    "in_scope_reason": "No narrower function was explicit; scope remains broad until M1 evidence narrows the affected component.",
                    "affected_modules": [complaint.affected_component_name if complaint else state.device_id],
                    "scope_confidence": 0.45,
                }
            )
        return scope

    def _select_requirements(
        self,
        state: GraphState,
        audit_scope: list[dict[str, Any]],
        readiness_by_req: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        text = f"{state.raw_complaint} {' '.join(state.structured_complaint.symptom_codes if state.structured_complaint else [])}".lower()
        component_id = state.structured_complaint.affected_component if state.structured_complaint else ""
        scored = []
        for item in audit_scope:
            req = item.get("requirement", {})
            haystack = " ".join(str(req.get(key, "")) for key in ["id", "text", "category", "module", "component_id", "source_artifact"]).lower()
            score = sum(1 for term in re.findall(r"[a-z0-9]+", text) if len(term) > 3 and term in haystack)
            if component_id and req.get("component_id") == component_id:
                score += 4
            if readiness_by_req.get(req.get("id"), {}).get("score", 100) < 80:
                score += 1
            if score > 0:
                status = self._evidence_status(readiness_by_req.get(req.get("id"), {}))
                scored.append(
                    {
                        "score": score,
                        "audit_item": item,
                        "requirement_scope": {
                            "requirement_id": req.get("id", ""),
                            "requirement_text": req.get("text") or req.get("acceptance_criteria") or "",
                            "source_artifact": req.get("source_artifact", ""),
                            "source_type": normalize_source_type(req.get("source_type"), SourceType.INFERRED),
                            "relevance_reason": self._relevance_reason(score, req),
                            "link_strength": "strong" if score >= 5 else "medium" if score >= 3 else "weak",
                            "evidence_status": status,
                        },
                    }
                )
        if not scored:
            for item in audit_scope[:8]:
                req = item.get("requirement", {})
                scored.append(
                    {
                        "score": 0,
                        "audit_item": item,
                        "requirement_scope": {
                            "requirement_id": req.get("id", ""),
                            "requirement_text": req.get("text") or req.get("acceptance_criteria") or "",
                            "source_artifact": req.get("source_artifact", ""),
                            "source_type": normalize_source_type(req.get("source_type"), SourceType.INFERRED),
                            "relevance_reason": "Candidate requirement retained because no stronger complaint-specific requirement was found.",
                            "link_strength": "weak",
                            "evidence_status": self._evidence_status(readiness_by_req.get(req.get("id"), {})),
                        },
                    }
                )
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:12]

    def _verification_scope(self, selected: list[dict[str, Any]], complaint_fw: str) -> list[dict[str, Any]]:
        rows = []
        for item in selected:
            req_id = item["requirement_scope"]["requirement_id"]
            tests = item["audit_item"].get("tests", [])
            if not tests:
                rows.append(
                    {
                        "test_case_id": "",
                        "linked_requirement_id": req_id,
                        "tested_firmware": "",
                        "complaint_firmware": complaint_fw,
                        "result": "missing",
                        "evidence_artifact": "",
                        "source_type": item["requirement_scope"].get("source_type", SourceType.INFERRED.value),
                        "source_types": [item["requirement_scope"].get("source_type", SourceType.INFERRED.value)],
                        "evidence_class": EvidenceClass.NO_EVIDENCE.value,
                        "trace_decay_status": "missing",
                        "trace_decay_reason": "No linked verification test exists for the scoped requirement.",
                    }
                )
                continue
            for test in tests:
                tested_fw = str(test.get("firmware_tested", ""))
                result = str(test.get("result", test.get("test_result", "")))
                test_source_type = normalize_source_type(test.get("source_type"), SourceType.INFERRED)
                status = "current and usable"
                reason = "Test firmware matches the complaint firmware and acceptance criteria are marked met."
                if test_source_type == SourceType.SYNTHETIC.value:
                    status = "usable with caution"
                    evidence_class = EvidenceClass.CANDIDATE.value
                    reason = "Test is synthetic/demo backfill and cannot be treated as controlled verification evidence."
                elif tested_fw and complaint_fw and tested_fw != complaint_fw:
                    status = "stale"
                    evidence_class = EvidenceClass.HISTORICAL_CONTROLLED.value
                    reason = f"Test was executed on {tested_fw}, while complaint firmware is {complaint_fw}."
                elif result and result.lower() not in {"pass", "passed"}:
                    status = "usable with caution"
                    evidence_class = EvidenceClass.CANDIDATE.value
                    reason = f"Test result is {result}; quality review is required before closure."
                else:
                    evidence_class = EvidenceClass.CONTROLLED_VERIFICATION.value
                rows.append(
                    {
                        "test_case_id": test.get("id", ""),
                        "linked_requirement_id": req_id,
                        "tested_firmware": tested_fw,
                        "complaint_firmware": complaint_fw,
                        "result": result,
                        "evidence_artifact": test.get("source_artifact") or test.get("vector_ref_id") or "",
                        "source_type": normalize_source_type(test.get("source_type"), SourceType.INFERRED),
                        "source_types": sorted(
                            {
                                item["requirement_scope"].get("source_type", SourceType.INFERRED.value),
                                test_source_type,
                            }
                        ),
                        "evidence_class": evidence_class,
                        "trace_decay_status": status,
                        "trace_decay_reason": reason,
                    }
                )
        return rows

    def _scope_findings(
        self,
        selected: list[dict[str, Any]],
        verification_scope: list[dict[str, Any]],
        readiness_by_req: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        findings = []
        for row in verification_scope:
            if row["trace_decay_status"] in {"missing", "stale", "usable with caution"}:
                findings.append(
                    {
                        "finding_type": "trace_decay" if row["trace_decay_status"] == "stale" else "missing_or_weak_verification",
                        "missing_or_weak_evidence": row["trace_decay_reason"],
                        "impact": f"Requirement {row['linked_requirement_id']} cannot be treated as fully verified for complaint scope.",
                        "recommended_action": "Link current controlled evidence, rerun verification, or add approved equivalence rationale.",
                    }
                )
        for item in selected:
            req_id = item["requirement_scope"]["requirement_id"]
            readiness = readiness_by_req.get(req_id, {})
            if readiness.get("open_capa_count", 0) > 0:
                findings.append(
                    {
                        "finding_type": "open_capa",
                        "missing_or_weak_evidence": f"{readiness['open_capa_count']} open CAPA record(s) touch {req_id}.",
                        "impact": "Audit readiness cannot be considered clean while open CAPA impact remains unresolved.",
                        "recommended_action": "Close, justify, or explicitly scope the CAPA impact before final report conclusion.",
                    }
                )
        return findings[:20]

    def _root_cause_scope(self, state: GraphState) -> list[dict[str, Any]]:
        scope = []
        for hypothesis in state.hypotheses:
            evidence = [item for item in state.evidence_collected if item.hypothesis_id == hypothesis.id]
            avg = sum(item.confidence for item in evidence) / len(evidence) if evidence else 0.0
            scope.append(
                {
                    "classification": "probable_root_cause" if avg >= 0.7 else "hypothesis" if evidence else "unknown_due_to_missing_evidence",
                    "cause_statement": hypothesis.description,
                    "supporting_evidence": [item.citation for item in evidence],
                    "contradicting_evidence": [],
                    "missing_evidence": [] if evidence else ["No current-firmware objective evidence retrieved for this hypothesis."],
                    "confidence_score": round(max(avg, hypothesis.base_probability), 2),
                }
            )
        return scope

    def _risk_items(self, selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        risks: dict[str, dict[str, Any]] = {}
        for item in selected:
            for risk in item["audit_item"].get("risks", []):
                risk_id = risk.get("id")
                if risk_id:
                    risks[risk_id] = {
                        "risk_id": risk_id,
                        "hazard": risk.get("hazard") or risk.get("hazardous_situation") or "",
                        "severity": risk.get("severity", ""),
                        "probability": risk.get("probability") or risk.get("residual_probability") or "",
                        "risk_control": risk.get("risk_controls") or risk.get("mitigation") or "",
                    }
        return list(risks.values())

    def _key_evidence(self, state: GraphState, verification_scope: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence = [
            {"id": item.id, "source": item.source, "confidence": item.confidence, "citation": item.citation}
            for item in state.evidence_collected[:10]
        ]
        evidence.extend(
            {
                "id": row["test_case_id"],
                "source": row["evidence_artifact"],
                "confidence": 0.9 if row["trace_decay_status"] == "current and usable" else 0.55,
                "citation": f"[Source: {row['evidence_artifact'] or row['test_case_id']}, Confidence: 0.90]",
            }
            for row in verification_scope[:10]
            if row.get("test_case_id")
        )
        return evidence

    def _evidence_status(self, readiness: dict[str, Any]) -> str:
        if not readiness:
            return "candidate"
        if not readiness.get("evidence_exists"):
            return "missing"
        if not readiness.get("evidence_fresh"):
            return "stale"
        if not readiness.get("acceptance_criteria_met"):
            return "candidate"
        return "verified_current"

    def _complaint_firmware(self, state: GraphState, fallback: str) -> str:
        complaint = state.structured_complaint
        return (
            (complaint.firmware_version if complaint else "")
            or state.complaint_firmware_version
            or fallback
        )

    def _affected_function(self, state: GraphState) -> str:
        scope = self._functional_scope(state)
        return ", ".join(item["category"] for item in scope)

    def _trigger_condition(self, text: str) -> str:
        lowered = text.lower()
        if any(term in lowered for term in ["after", "following", "post", "since"]):
            if any(term in lowered for term in ["update", "firmware", "software", "release"]):
                return "After a software or firmware change"
            if any(term in lowered for term in ["clean", "disinfect", "wash"]):
                return "After cleaning or reprocessing"
            if any(term in lowered for term in ["drop", "impact", "fall"]):
                return "After physical impact or handling event"
            if any(term in lowered for term in ["charge", "charger", "battery"]):
                return "After charging or power-related use"
            return "After a reported change or preceding event"
        if any(term in lowered for term in ["during", "while", "when"]):
            return "During active use or monitoring"
        if any(term in lowered for term in ["repeated", "recurring", "intermittent", "sometimes"]):
            return "Recurring or intermittent event"
        return "Not fully specified by complaint text"

    def _relevance_reason(self, score: int, req: dict[str, Any]) -> str:
        module = req.get("module") or req.get("component_id") or req.get("category") or "requirement text"
        return f"Matched complaint terms against {module}; relevance score {score}."

    def _recommended_conclusion(self, findings: list[dict[str, Any]]) -> str:
        if findings:
            return "Investigation should remain open until the listed evidence gaps are resolved or formally justified."
        return "Scoped evidence is sufficient for M4 to draft the complaint/CAPA report with controlled citations."
