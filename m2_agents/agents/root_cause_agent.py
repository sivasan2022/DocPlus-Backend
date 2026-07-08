from __future__ import annotations

from typing import Any

from backend.graph.schema import SourceType, normalize_source_type
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.llm import llm
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState, Hypothesis
from m2_agents.tools import graph_tools


class RootCauseAgent(BaseAgent):
    name = "root_cause"

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "generate complaint-specific failure hypotheses"):
            complaint = state.structured_complaint
            if complaint is None:
                state.errors.append("RootCauseAgent requires structured_complaint")
                return state
            neighborhood = graph_tools.get_graph_neighborhood(complaint.affected_component, depth=2)
            state.graph_context["affected_component_neighborhood"] = neighborhood
            component_name = self._component_name(complaint.affected_component)
            state.hypotheses = self._build_contextual_hypotheses(state, component_name, neighborhood)
            state.agent_debug[self.name] = {
                "outcome": {
                    "hypotheses": [item.model_dump() for item in state.hypotheses],
                    "similar_incidents_seen": state.similar_incidents,
                    "llm_root_cause_reasoning": state.ai_reasoning.get("root_cause", {}),
                },
                "graph_fetches": [
                    {
                        "tool": "graph_tools.get_graph_neighborhood",
                        "purpose": "Fetch the affected component topology used to frame root-cause hypotheses.",
                        "origin_node_id": complaint.affected_component,
                        "depth": 2,
                        "node_count": len(neighborhood.get("nodes", [])),
                        "relationship_count": len(neighborhood.get("edges", [])),
                        "nodes": neighborhood.get("nodes", []),
                        "relationships": neighborhood.get("edges", []),
                    }
                ],
            }
            state.status = "root_causes_generated"
        return state

    def _build_contextual_hypotheses(
        self,
        state: GraphState,
        component_name: str,
        neighborhood: dict[str, Any],
    ) -> list[Hypothesis]:
        complaint = state.structured_complaint
        support_facts = self._supporting_facts(state)
        payload = self._root_cause_payload(state, component_name, neighborhood, support_facts)
        result = llm.root_cause_hypotheses(payload)
        state.ai_reasoning["root_cause"] = {
            "source": result.get("source", ""),
            "enabled": result.get("enabled", False),
            "model": result.get("model", ""),
            "mode": result.get("mode", ""),
            "confidence": result.get("confidence"),
            "ranking_rationale": result.get("ranking_rationale", []),
            "fallback_reason": result.get("fallback_reason", ""),
            "error": result.get("error", ""),
        }
        hypotheses = self._hypotheses_from_result(result, state, component_name, support_facts)
        if hypotheses:
            return hypotheses

        fallback = llm.root_cause_hypotheses({**payload, "_force_fallback": True})
        return self._hypotheses_from_result(fallback, state, component_name, support_facts)

    def _hypotheses_from_result(
        self,
        result: dict[str, Any],
        state: GraphState,
        component_name: str,
        support_facts: list[dict[str, Any]],
    ) -> list[Hypothesis]:
        complaint = state.structured_complaint
        raw_items = [item for item in result.get("hypotheses", []) if isinstance(item, dict)][:5]
        if len(raw_items) < 3:
            return []

        weights = [self._clean_probability(item.get("base_probability")) for item in raw_items]
        total = sum(weights) or 1.0
        fact_by_id = {str(fact.get("node_id", "")): fact for fact in support_facts if fact.get("node_id")}
        hypotheses: list[Hypothesis] = []
        for index, item in enumerate(raw_items, start=1):
            fact_ids = [str(value) for value in item.get("supporting_fact_ids", []) if value]
            item_facts = [fact_by_id[fact_id] for fact_id in fact_ids if fact_id in fact_by_id]
            if not item_facts:
                item_facts = support_facts[:6]
            source_types = sorted({fact["source_type"] for fact in item_facts if fact.get("source_type")})
            evidence_for, evidence_against = self._clean_evidence_balance(
                self._as_lines(item.get("evidence_for"))[:6],
                self._as_lines(item.get("evidence_against"))[:6],
                state,
            )
            hypotheses.append(
                Hypothesis(
                    id=f"HYP-{index:03d}",
                    title=self._prefix_component(component_name, str(item.get("title") or f"Candidate failure mode {index}")),
                    description=str(item.get("description") or "No description returned by root-cause reasoning."),
                    affected_component=str(item.get("affected_component") or (complaint.affected_component if complaint else state.device_id)),
                    base_probability=round(weights[index - 1] / total, 2),
                    why_chain=self._as_lines(item.get("why_chain"))[:5],
                    evidence_for=evidence_for,
                    evidence_against=evidence_against,
                    similar_incident_analysis=self._as_lines(item.get("similar_incident_analysis"))[:6],
                    probability_rationale=str(item.get("probability_rationale") or ""),
                    citation=self._citation(
                        "M2 root-cause LLM over graph context" if result.get("enabled") else "M2 root-cause deterministic fallback",
                        float(result.get("confidence") or 0.74),
                    ),
                    supporting_facts=item_facts,
                    source_types=source_types or [SourceType.INTERNAL.value],
                    source_node_ids=[fact["node_id"] for fact in item_facts if fact.get("node_id")],
                )
            )
        return hypotheses

    def _root_cause_payload(
        self,
        state: GraphState,
        component_name: str,
        neighborhood: dict[str, Any],
        support_facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        complaint = state.structured_complaint
        component = self._component_context(state, component_name)
        return {
            "complaint": {
                "raw_text": state.raw_complaint,
                "summary": complaint.raw_summary if complaint else "",
                "severity": complaint.severity if complaint else "",
                "symptom_codes": complaint.symptom_codes if complaint else [],
                "affected_component": complaint.affected_component if complaint else "",
                "affected_component_name": complaint.affected_component_name if complaint else "",
                "component_match_score": complaint.component_match_score if complaint else 0,
                "component_match_terms": complaint.component_match_terms if complaint else [],
                "timeline": complaint.timeline if complaint else "",
            },
            "component": component,
            "similar_incidents": [self._compact_incident(item) for item in state.similar_incidents[:5]],
            "requirement_test_context": self._requirement_test_context(neighborhood),
            "graph_neighborhood": {
                "nodes": [self._compact_node(item) for item in neighborhood.get("nodes", [])[:25]],
                "relationships": [self._compact_edge(item) for item in neighborhood.get("edges", [])[:35]],
            },
            "existing_evidence_items": [item.model_dump() for item in state.evidence_collected[:10]],
            "readiness_gaps": self._readiness_gaps(state),
            "stale_evidence": state.graph_context.get("stale_evidence", [])[:5],
            "open_capa_count": state.graph_context.get("open_capa_count", 0),
            "supporting_facts": support_facts[:20],
            "instructions": [
                "Generate failure-mode hypotheses specific to the reported symptom and component.",
                "Use similar_incidents by ID inside each hypothesis analysis.",
                "List evidence_for and evidence_against from provided facts only.",
                "Say explicitly when no current evidence confirms or rules out a hypothesis.",
                "Rank only after hypothesis/evidence/cross-check reasoning is complete.",
            ],
        }

    def _supporting_facts(self, state: GraphState) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        complaint = state.structured_complaint
        if complaint and complaint.affected_component:
            component = next(
                (
                    item
                    for item in state.graph_context.get("components", [])
                    if item.get("id") == complaint.affected_component
                ),
                {},
            )
            facts.append(
                {
                    "claim": "Affected component selected during complaint intake.",
                    "node_id": complaint.affected_component,
                    "source_type": normalize_source_type(component.get("source_type"), SourceType.INFERRED),
                    "summary": component.get("name", complaint.affected_component),
                }
            )
        for item in state.similar_incidents[:5]:
            facts.append(
                {
                    "claim": "Prior complaint with overlapping complaint terms was found.",
                    "node_id": item.get("id", ""),
                    "source_type": normalize_source_type(item.get("source_type"), SourceType.INFERRED),
                    "summary": item.get("summary") or item.get("description") or item.get("id", ""),
                    "score": item.get("score"),
                }
            )
        neighborhood = state.graph_context.get("affected_component_neighborhood", {})
        for node in neighborhood.get("nodes", [])[:40]:
            labels = set(node.get("labels", []))
            if not labels & {"Requirement", "TestCase", "Test", "TestRun", "Risk", "Evidence", "CAPA"}:
                continue
            props = node.get("properties", {})
            summary = (
                props.get("text")
                or props.get("title")
                or props.get("description")
                or props.get("acceptance_criteria")
                or props.get("result")
                or node.get("id", "")
            )
            facts.append(
                {
                    "claim": f"Graph {', '.join(sorted(labels))} node linked to affected component neighborhood.",
                    "node_id": node.get("id", ""),
                    "source_type": normalize_source_type(props.get("source_type"), SourceType.INFERRED),
                    "summary": str(summary)[:500],
                }
            )
        if not state.similar_incidents:
            facts.append(
                {
                    "claim": "No prior complaint precedent was found in the current graph search.",
                    "node_id": "",
                    "source_type": SourceType.INTERNAL.value,
                    "summary": "Historical-context search returned no matching complaint nodes.",
                }
            )
        return facts

    def _component_context(self, state: GraphState, component_name: str) -> dict[str, Any]:
        complaint = state.structured_complaint
        component_id = complaint.affected_component if complaint else ""
        component = next(
            (item for item in state.graph_context.get("components", []) if item.get("id") == component_id),
            {},
        )
        return {
            "id": component_id,
            "name": component.get("name") or component_name,
            "module": component.get("module", ""),
            "part_type": component.get("part_type", ""),
            "safety_relevance": component.get("safety_relevance", ""),
            "source_type": component.get("source_type", ""),
        }

    def _requirement_test_context(self, neighborhood: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for node in neighborhood.get("nodes", [])[:50]:
            labels = set(node.get("labels", []))
            if not labels & {"Requirement", "TestCase", "Test", "TestRun", "Risk", "Evidence"}:
                continue
            props = node.get("properties", {})
            rows.append(
                {
                    "id": node.get("id", ""),
                    "labels": sorted(labels),
                    "text": props.get("text") or props.get("acceptance_criteria") or props.get("title") or props.get("description") or "",
                    "result": props.get("result") or props.get("test_result") or "",
                    "firmware": props.get("firmware") or props.get("firmware_tested") or "",
                    "source_type": props.get("source_type", ""),
                    "review_status": props.get("review_status", ""),
                    "controlled_status": props.get("controlled_status", ""),
                }
            )
        return rows[:16]

    def _readiness_gaps(self, state: GraphState) -> list[dict[str, Any]]:
        gaps = []
        for item in state.graph_context.get("readiness", {}).get("requirements", []):
            if item.get("score", 100) >= 80:
                continue
            gaps.append(
                {
                    "requirement_id": item.get("requirement_id") or item.get("id", ""),
                    "score": item.get("score"),
                    "status": item.get("status", ""),
                    "reason": item.get("reason", ""),
                }
            )
        return gaps[:12]

    def _compact_incident(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id", ""),
            "score": item.get("score", 0),
            "description": item.get("description") or item.get("summary") or "",
            "root_cause": item.get("root_cause", ""),
            "closure_status": item.get("closure_status", ""),
            "investigation_scope": str(item.get("investigation_scope", ""))[:900],
            "source_type": item.get("source_type", ""),
        }

    def _compact_node(self, node: dict[str, Any]) -> dict[str, Any]:
        props = node.get("properties", {})
        return {
            "id": node.get("id", ""),
            "labels": node.get("labels", []),
            "text": props.get("text") or props.get("acceptance_criteria") or props.get("title") or props.get("description") or "",
            "module": props.get("module", ""),
            "component_id": props.get("component_id", ""),
            "source_type": props.get("source_type", ""),
            "result": props.get("result") or props.get("test_result") or "",
        }

    def _compact_edge(self, edge: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": edge.get("source", ""),
            "target": edge.get("target", ""),
            "type": edge.get("type", ""),
            "rationale": (edge.get("properties", {}) or {}).get("rationale", ""),
        }

    def _as_lines(self, value: Any) -> list[str]:
        if isinstance(value, list):
            lines = [str(item).strip() for item in value if str(item).strip()]
        elif value:
            lines = [str(value).strip()]
        else:
            lines = []
        return lines or ["No current graph evidence was provided for this field."]

    def _clean_evidence_balance(
        self,
        evidence_for: list[str],
        evidence_against: list[str],
        state: GraphState,
    ) -> tuple[list[str], list[str]]:
        gap_terms = (
            "no evidence",
            "no current evidence",
            "no direct evidence",
            "no specific evidence",
            "no specific",
            "no concrete",
            "no documented",
            "not available",
            "insufficient",
            "missing",
            "lack of",
            "does not confirm",
        )
        cleaned_for: list[str] = []
        cleaned_against = list(evidence_against)
        for line in evidence_for:
            lower = line.lower()
            if any(term in lower for term in gap_terms):
                cleaned_against.append(line)
            else:
                cleaned_for.append(line)

        if not cleaned_for:
            complaint_text = state.raw_complaint or (state.structured_complaint.raw_summary if state.structured_complaint else "")
            if complaint_text:
                cleaned_for.append(f"Complaint report states the observed failure condition: {complaint_text}")
            elif state.structured_complaint:
                cleaned_for.append(f"Complaint intake selected affected component {state.structured_complaint.affected_component}.")
        if not cleaned_against:
            cleaned_against.append("No current returned-unit inspection, bench test, or engineering analysis is available to rule this hypothesis out.")
        return cleaned_for[:6], cleaned_against[:6]

    def _clean_probability(self, value: Any) -> float:
        try:
            probability = float(value)
        except (TypeError, ValueError):
            return 0.2
        if probability > 1:
            probability /= 100
        return min(0.95, max(0.03, probability))

    def _prefix_component(self, component_name: str, title: str) -> str:
        clean_title = " ".join(title.split())
        if component_name and component_name.lower() not in clean_title.lower():
            return f"{component_name}: {clean_title}"
        return clean_title

    def _with_history_note(self, why: list[str], similar_incidents: list[dict[str, Any]]) -> list[str]:
        if similar_incidents:
            ids = ", ".join(str(item.get("id", "")) for item in similar_incidents[:3] if item.get("id"))
            return [
                f"Historical context found {len(similar_incidents)} similar complaint record(s): {ids}.",
                *why,
            ]
        return ["No historical complaint precedent was found in the current graph.", *why]

    def _component_name(self, component_id: str) -> str:
        node = graph_tools.get_graph_neighborhood(component_id, 1).get("nodes", [])
        if node:
            return node[0].get("properties", {}).get("name", component_id)
        return component_id
