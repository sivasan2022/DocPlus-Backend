from __future__ import annotations

from collections import Counter
from typing import Any

from backend.graph.schema import CAPA_BLOCKING_EVIDENCE_CLASSES, EvidenceClass, evidence_class_label, normalize_evidence_class
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState, RiskAssessment
from m2_agents.tools import graph_tools


EVIDENCE_CONFIDENCE_WEIGHTS: dict[str, float] = {
    EvidenceClass.CONTROLLED_VERIFICATION.value: 1.0,
    EvidenceClass.HISTORICAL_CONTROLLED.value: 0.7,
    EvidenceClass.SIMULATED.value: 0.45,
    EvidenceClass.CANDIDATE.value: 0.25,
    EvidenceClass.NO_EVIDENCE.value: 0.0,
}

EVIDENCE_CONFIDENCE_RULE = (
    "Risk evidence confidence is the average of per-item evidence-class weights "
    "(controlled_verification=1.00, historical_controlled=0.70, simulated=0.45, "
    "candidate=0.25, no_evidence=0.00). The final label is capped below high when "
    "any CAPA-closure-blocking evidence class is present, because those items cannot "
    "support final closure until controlled verification is attached."
)


class RiskAgent(BaseAgent):
    name = "risk"

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "calculate ISO 14971 risk"):
            complaint = state.structured_complaint
            severity = {"Low": 2, "Medium": 3, "High": 4, "Critical": 5}.get(complaint.severity if complaint else "Medium", 3)
            context = state.graph_context or graph_tools.get_device_context(state.device_id)
            stale_count = len(context.get("stale_evidence", []))
            open_capas = int(context.get("open_capa_count", graph_tools.open_capa_count(state.device_id)))
            similar_count = len(state.similar_incidents)
            confidence = self._evidence_confidence_scorecard(state)
            evidence_classes = confidence["evidence_classes"]
            source_types = sorted({item.source_type for item in state.evidence_collected})
            controlled_support = confidence["controlled_supporting_count"]
            evidence_support = sum(1 for item in state.evidence_collected if item.supports)
            uncertainty_flag = confidence["uncertainty_flag"]
            confidence_in_evidence = confidence["confidence_in_evidence"]
            probability = min(5, 1 + min(2, stale_count) + min(1, open_capas) + min(1, similar_count // 2))
            if evidence_support == 0 or confidence_in_evidence == "low":
                probability = min(5, probability + 1)
            rpn = severity * probability
            if rpn >= 20:
                level = "Critical"
            elif rpn >= 12:
                level = "High"
            elif rpn >= 6:
                level = "Medium"
            else:
                level = "Low"
            state.risk_assessment = RiskAssessment(
                severity=severity,
                probability=probability,
                rpn=rpn,
                risk_level=level,
                reportable=severity >= 4 and probability >= 3,
                rationale=f"Severity came from complaint classification; probability used graph signals: stale evidence={stale_count}, open CAPAs={open_capas}, similar incidents={similar_count}, supporting evidence={evidence_support}, controlled current evidence={controlled_support}, evidence classes={', '.join(evidence_classes) or 'none'}.",
                citation=self._citation("M1 graph readiness, complaint history, and evidence retrieval", 0.92),
                uncertainty_flag=uncertainty_flag,
                confidence_in_evidence=confidence_in_evidence,
                evidence_confidence_score=confidence["score"],
                evidence_confidence_rule=EVIDENCE_CONFIDENCE_RULE,
                evidence_confidence_basis=confidence["basis"],
                evidence_class_breakdown=confidence["class_breakdown"],
                evidence_confidence_drivers=confidence["drivers"],
                evidence_classes=evidence_classes,
                source_types=source_types,
            )
            state.agent_debug[self.name] = {
                "outcome": state.risk_assessment.model_dump(),
                "graph_signals_used": {
                    "stale_evidence_count": stale_count,
                    "open_capa_count": open_capas,
                    "similar_incident_count": similar_count,
                    "supporting_evidence_count": evidence_support,
                    "controlled_supporting_evidence_count": controlled_support,
                    "evidence_classes": evidence_classes,
                    "evidence_class_breakdown": confidence["class_breakdown"],
                    "source_types": source_types,
                    "uncertainty_flag": uncertainty_flag,
                    "confidence_in_evidence": confidence_in_evidence,
                    "evidence_confidence_score": confidence["score"],
                    "evidence_confidence_rule": EVIDENCE_CONFIDENCE_RULE,
                    "evidence_confidence_basis": confidence["basis"],
                    "evidence_confidence_drivers": confidence["drivers"],
                    "severity_score": severity,
                    "probability_score": probability,
                    "rpn": rpn,
                },
                "graph_source": "M1 graph_context from complaint intake, with open CAPA fallback through graph_tools.open_capa_count",
            }
            state.status = "risk_assessed"
        return state

    def _evidence_confidence_scorecard(self, state: GraphState) -> dict[str, Any]:
        items = list(state.evidence_collected)
        if not items:
            return {
                "score": 0.0,
                "confidence_in_evidence": "low",
                "uncertainty_flag": "High uncertainty: no investigation evidence items were retrieved.",
                "basis": "No evidence items were available for risk confidence scoring.",
                "class_breakdown": {},
                "evidence_classes": [],
                "drivers": [],
                "controlled_supporting_count": 0,
            }

        normalized_classes = [normalize_evidence_class(item.evidence_class) for item in items]
        class_counts = dict(sorted(Counter(normalized_classes).items()))
        score = round(
            sum(EVIDENCE_CONFIDENCE_WEIGHTS[class_name] for class_name in normalized_classes) / len(normalized_classes),
            2,
        )
        controlled_count = class_counts.get(EvidenceClass.CONTROLLED_VERIFICATION.value, 0)
        controlled_support = sum(
            1
            for item in items
            if item.supports and normalize_evidence_class(item.evidence_class) == EvidenceClass.CONTROLLED_VERIFICATION.value
        )
        blocking_counts = {
            class_name: count
            for class_name, count in class_counts.items()
            if class_name in CAPA_BLOCKING_EVIDENCE_CLASSES and count
        }
        blocking_total = sum(blocking_counts.values())

        if controlled_count == len(items) and score >= 0.85:
            label = "high"
            uncertainty = (
                f"Low uncertainty: all {len(items)} evidence item(s) are current controlled verification evidence."
            )
        elif controlled_count > 0:
            label = "medium"
            uncertainty = (
                f"Medium uncertainty: {controlled_count} of {len(items)} evidence item(s) are current controlled verification, "
                f"but {blocking_total} item(s) remain historical, simulated, candidate, or no-evidence class and cannot support CAPA closure."
            )
        elif class_counts.get(EvidenceClass.HISTORICAL_CONTROLLED.value, 0) > 0:
            label = "medium"
            uncertainty = (
                "Medium uncertainty: controlled evidence exists, but it is historical or tied to a prior version and requires "
                "current-firmware equivalence or repeat verification."
            )
        else:
            label = "low"
            uncertainty = (
                "High uncertainty: available evidence is simulated, candidate, or absent, so objective controlled verification "
                "is still required before the risk conclusion can be finalized."
            )

        breakdown_text = ", ".join(
            f"{evidence_class_label(class_name)}={count}" for class_name, count in class_counts.items()
        )
        drivers = [
            f"{item.source_node_id or item.id}: {normalize_evidence_class(item.evidence_class)}"
            for item in items[:20]
        ]
        if len(items) > 20:
            drivers.append(f"{len(items) - 20} additional evidence item(s) retained in agent debug output")

        basis = (
            f"Weighted evidence-class score {score:.2f} from {len(items)} item(s): {breakdown_text}. "
            f"CAPA-blocking evidence-class item count: {blocking_total}."
        )
        return {
            "score": score,
            "confidence_in_evidence": label,
            "uncertainty_flag": uncertainty,
            "basis": basis,
            "class_breakdown": class_counts,
            "evidence_classes": sorted(class_counts),
            "drivers": drivers,
            "controlled_supporting_count": controlled_support,
        }
