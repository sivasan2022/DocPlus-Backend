from __future__ import annotations

from backend.graph.schema import SourceType, normalize_source_type
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.observability import traced
from m2_agents.core.regulatory import normalize_framework, regulatory_label, regulatory_reference
from m2_agents.core.state import AuditFinding, GraphState
from m2_agents.tools import graph_tools


class AuditShadowAgent(BaseAgent):
    name = "audit_shadow"

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "run adversarial audit"):
            state.device_id = graph_tools.resolve_device_id(state.device_id)
            context = graph_tools.get_device_context(state.device_id)
            state.graph_context = context
            state.regulatory_framework = normalize_framework(state.regulatory_framework, context.get("standards"))
            state.regulatory_label = regulatory_label(state.regulatory_framework, context.get("standards"))
            readiness = graph_tools.get_readiness(state.device_id)
            requirement_sources = {
                item.get("id"): normalize_source_type(item.get("source_type"), SourceType.INFERRED)
                for item in context.get("requirements", [])
                if item.get("id")
            }
            findings: list[AuditFinding] = []
            counter = 1
            for req in readiness.get("requirements", []):
                if not req.get("evidence_exists"):
                    findings.append(
                        self._finding(
                            counter,
                            req["requirement_id"],
                            "Major",
                            "Requirement has no linked verification test evidence. Objective evidence is missing for the current audit scope.",
                            "Create or link verification evidence before submission.",
                            req.get("standard"),
                            state.regulatory_framework,
                            requirement_sources.get(req["requirement_id"], SourceType.INFERRED.value),
                        )
                    )
                    counter += 1
                elif not req.get("evidence_fresh"):
                    findings.append(
                        self._finding(
                            counter,
                            req["requirement_id"],
                            "Critical",
                            f"Linked test evidence was executed on {', '.join(req.get('tested_firmware_versions', []))}, but the device current firmware is {req.get('current_firmware')}.",
                            "Repeat verification on the current firmware or justify equivalence with approved rationale.",
                            req.get("standard"),
                            state.regulatory_framework,
                            requirement_sources.get(req["requirement_id"], SourceType.INFERRED.value),
                        )
                    )
                    counter += 1
                elif not req.get("acceptance_criteria_met"):
                    findings.append(
                        self._finding(
                            counter,
                            req["requirement_id"],
                            "Major",
                            "Linked test exists but acceptance criteria are not marked as met.",
                            "Update test report with explicit pass/fail evidence or rerun the protocol.",
                            req.get("standard"),
                            state.regulatory_framework,
                            requirement_sources.get(req["requirement_id"], SourceType.INFERRED.value),
                        )
                    )
                    counter += 1
                if req.get("open_capa_count", 0) > 0:
                    findings.append(
                        self._finding(
                            counter,
                            req["requirement_id"],
                            "Major",
                            f"Requirement is touched by {req['open_capa_count']} open CAPA record(s), so readiness cannot be treated as clean.",
                            "Close or justify open CAPA impact before audit claim.",
                            req.get("standard"),
                            state.regulatory_framework,
                            requirement_sources.get(req["requirement_id"], SourceType.INFERRED.value),
                        )
                    )
                    counter += 1
            state.audit_findings = findings
            state.status = "audit_completed"
            state.add_event(self.name, "audit summary", "info", findings=len(findings), readiness_score=readiness.get("score"))
            state.agent_debug[self.name] = {
                "outcome": {
                    "finding_count": len(state.audit_findings),
                    "findings": [finding.model_dump() for finding in state.audit_findings],
                    "readiness_score": readiness.get("score"),
                },
                "graph_fetches": [
                    {
                        "tool": "graph_tools.get_device_context",
                        "purpose": "Fetch full device context before audit scoring.",
                        "summary": {
                            "device_id": state.device_id,
                            "node_count": context.get("node_count"),
                            "edge_count": context.get("edge_count"),
                            "requirement_count": len(context.get("requirements", [])),
                            "stale_evidence_count": len(context.get("stale_evidence", [])),
                            "open_capa_count": context.get("open_capa_count"),
                        },
                    },
                    {
                        "tool": "graph_tools.get_readiness",
                        "purpose": "Fetch requirement readiness rows used to generate AuditShadow findings.",
                        "readiness": readiness,
                    },
                ],
            }
        return state

    def _finding(
        self,
        number: int,
        requirement_id: str,
        risk_level: str,
        finding: str,
        remediation: str,
        standard: str | None = None,
        framework: str | None = None,
        source_type: str = SourceType.INFERRED.value,
    ) -> AuditFinding:
        normalized_source_type = normalize_source_type(source_type, SourceType.INFERRED)
        return AuditFinding(
            id=f"FINDING-{number:03d}",
            requirement_id=requirement_id,
            regulatory_reference=regulatory_reference(framework, standard),
            finding=finding,
            risk_level=risk_level,
            citation=self._citation("M1 readiness score and traceability matrix", 0.96),
            remediation=remediation,
            source_type=normalized_source_type,
            source_types=[normalized_source_type],
            source_node_ids=[requirement_id],
        )
