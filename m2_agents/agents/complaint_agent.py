from __future__ import annotations

from typing import Any

from backend.graph.schema import SourceType, normalize_source_type
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core import dynamic
from m2_agents.core.llm import llm
from m2_agents.core.observability import traced
from m2_agents.core.regulatory import normalize_framework, regulatory_label
from m2_agents.core.state import GraphState, StructuredComplaint
from m2_agents.tools import graph_tools


class ComplaintIntakeAgent(BaseAgent):
    name = "complaint_intake"

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "extract structured complaint"):
            text = state.raw_complaint or ""
            state.device_id = graph_tools.resolve_device_id(state.device_id)
            state.graph_context = graph_tools.get_device_context(state.device_id)
            state.regulatory_framework = normalize_framework(state.regulatory_framework, state.graph_context.get("standards"))
            state.regulatory_label = regulatory_label(state.regulatory_framework, state.graph_context.get("standards"))
            base_severity = dynamic.severity_from_text(text)
            symptom_result = dynamic.dynamic_symptom_classification(text)
            symptom_codes = symptom_result["symptom_codes"]
            search_term = " ".join([text, *symptom_codes]).strip()
            component = dynamic.infer_component(
                search_term or text,
                state.graph_context.get("components", []),
                state.device_id,
                state.graph_context.get("requirements", []),
            )
            state.similar_incidents = self._normalize_similar_incidents(
                graph_tools.find_similar_complaints(search_term or text, limit=5)
            )
            classification_result = self._classify_component_if_needed(
                text=text,
                search_term=search_term or text,
                component=component,
                components=state.graph_context.get("components", []),
                requirements=state.graph_context.get("requirements", []),
                similar_incidents=state.similar_incidents,
            )
            if classification_result.get("component"):
                component = classification_result["component"]
            severity, severity_rule = dynamic.severity_with_context(
                text,
                symptom_codes,
                component,
                base_severity=base_severity,
            )
            state.structured_complaint = StructuredComplaint(
                device_id=state.device_id,
                firmware_version=state.complaint_firmware_version or dynamic.infer_firmware_version(text),
                serial_number=state.serial_number or dynamic.infer_serial_or_lot(text, "serial"),
                lot=state.lot or dynamic.infer_serial_or_lot(text, "lot"),
                severity=severity,
                symptom_codes=symptom_codes,
                affected_component=component["id"],
                affected_component_name=component.get("name", component["id"]),
                component_match_score=component.get("match_score", 0.0),
                component_match_terms=component.get("match_terms", []),
                timeline=dynamic.infer_timeline(text),
                raw_summary=text[:240],
                similar_incidents=state.similar_incidents,
                source_type=SourceType.INTERNAL.value,
            )
            state.agent_debug[self.name] = {
                "outcome": {
                    **state.structured_complaint.model_dump(),
                    "symptom_classification": symptom_result.get("classification", {}),
                },
                "graph_fetches": [
                    {
                        "tool": "graph_tools.get_device_context",
                        "purpose": "Resolve the selected device, available components, requirements, risks, prior complaints, readiness, stale evidence, and open CAPA signals.",
                        "summary": {
                            "device_id": state.device_id,
                            "node_count": state.graph_context.get("node_count"),
                            "edge_count": state.graph_context.get("edge_count"),
                            "component_count": len(state.graph_context.get("components", [])),
                            "requirement_count": len(state.graph_context.get("requirements", [])),
                            "risk_count": len(state.graph_context.get("risks", [])),
                            "similar_complaint_count": len(state.similar_incidents),
                            "stale_evidence_count": len(state.graph_context.get("stale_evidence", [])),
                            "open_capa_count": state.graph_context.get("open_capa_count"),
                        },
                        "nodes": {
                            "device": state.graph_context.get("device"),
                            "matched_component": component,
                            "components": state.graph_context.get("components", [])[:30],
                            "requirements_sample": state.graph_context.get("requirements", [])[:30],
                            "risks_sample": state.graph_context.get("risks", [])[:30],
                            "complaints_sample": state.graph_context.get("complaints", [])[:10],
                        },
                        "relationships": [],
                    },
                    {
                        "tool": "graph_tools.find_similar_complaints",
                        "purpose": "Find prior complaint nodes with overlapping symptom terms.",
                        "query": search_term,
                        "nodes": state.similar_incidents,
                        "relationships": [],
                    },
                    {
                        "tool": "llm.classify_complaint_intake",
                        "purpose": "When deterministic graph matching is weak, ask OpenAI or the deterministic fallback to arbitrate between graph-backed component candidates.",
                        "summary": {
                            "review_needed": classification_result.get("review_needed", False),
                            "source": classification_result.get("source", ""),
                            "selected_component": component.get("id"),
                            "confidence": classification_result.get("classification_confidence"),
                            "matched_requirement_ids": classification_result.get("matched_requirement_ids", []),
                            "matched_similar_incident_ids": classification_result.get("matched_similar_incident_ids", []),
                            "symptom_classification": symptom_result.get("classification", {}),
                            "semantic_symptom_codes": symptom_result.get("semantic_symptom_codes", []),
                            "keyword_symptom_codes": symptom_result.get("keyword_symptom_codes", []),
                            "severity_rule": severity_rule,
                            "symptom_taxonomy": dynamic.symptom_taxonomy(),
                            "symptom_taxonomy_details": dynamic.symptom_taxonomy_details(),
                        },
                        "nodes": {
                            "selected_component": component,
                            "classification": classification_result.get("result", {}),
                        },
                        "relationships": [],
                    },
                ],
            }
            state.status = "complaint_structured"
        return state

    def _normalize_similar_incidents(self, incidents: list[dict]) -> list[dict]:
        normalized = []
        for item in incidents:
            normalized.append(
                {
                    **item,
                    "source_type": normalize_source_type(item.get("source_type"), SourceType.INFERRED),
                    "source_node_id": item.get("id", ""),
                    "summary": item.get("description") or item.get("summary") or item.get("title") or item.get("id", ""),
                }
            )
        return normalized

    def _classify_component_if_needed(
        self,
        text: str,
        search_term: str,
        component: dict[str, Any],
        components: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
        similar_incidents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        review_needed = self._needs_component_review(component)
        if not review_needed:
            return {"review_needed": False, "component": component, "source": component.get("match_source", "")}

        payload = self._classification_payload(
            text=text,
            search_term=search_term,
            component=component,
            components=components,
            requirements=requirements,
            similar_incidents=similar_incidents,
        )
        result = llm.classify_complaint_intake(payload)
        selected = self._component_from_classification(result, components)
        if not selected:
            return {
                "review_needed": True,
                "component": component,
                "result": result,
                "source": result.get("source", "classification_unavailable"),
                "classification_confidence": result.get("classification_confidence"),
            }

        selected = {
            **selected,
            "match_score": round(float(result.get("classification_confidence") or 0) * 10, 3),
            "match_terms": self._classification_terms(result),
            "match_source": result.get("source", "openai_structured_output"),
            "matched_requirement": {
                "id": (result.get("matched_requirement_ids") or [""])[0],
                "score": result.get("classification_confidence"),
                "source_type": result.get("source", ""),
                "text": result.get("classification_reason", ""),
            },
            "classification_reason": result.get("classification_reason", ""),
            "issue_type": result.get("issue_type", ""),
            "use_condition": result.get("use_condition", ""),
        }
        return {
            "review_needed": True,
            "component": selected,
            "result": result,
            "source": result.get("source", ""),
            "classification_confidence": result.get("classification_confidence"),
            "matched_requirement_ids": result.get("matched_requirement_ids", []),
            "matched_similar_incident_ids": result.get("matched_similar_incident_ids", []),
        }

    def _needs_component_review(self, component: dict[str, Any]) -> bool:
        if component.get("match_source") == "requirement_scope" and float(component.get("match_score") or 0) >= 3:
            return False
        if not component.get("match_terms"):
            return True
        return component.get("match_source") == "component_text" and float(component.get("match_score") or 0) < 1

    def _classification_payload(
        self,
        text: str,
        search_term: str,
        component: dict[str, Any],
        components: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
        similar_incidents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        requirement_candidates = []
        for match in dynamic.match_requirements_by_relevance(search_term, requirements, limit=16):
            requirement = match["requirement"]
            requirement_candidates.append(
                {
                    "requirement_id": requirement.get("id", ""),
                    "requirement_text": requirement.get("text") or requirement.get("acceptance_criteria") or "",
                    "component_id": requirement.get("component_id", ""),
                    "module": requirement.get("module", ""),
                    "category": requirement.get("category", ""),
                    "source_type": requirement.get("source_type", ""),
                    "score": match.get("score", 0),
                    "match_terms": match.get("match_terms", []),
                }
            )
        return {
            "complaint_text": text,
            "search_text": search_term,
            "initial_component": self._compact_component(component),
            "allowed_components": [self._compact_component(item) for item in components],
            "requirement_candidates": requirement_candidates,
            "similar_incidents": [self._compact_incident(item) for item in similar_incidents[:8]],
            "instructions": [
                "Choose only an allowed component ID.",
                "Override the initial component when it has no match terms or only graph-degree support.",
                "Do not classify a high reading as an alarm issue unless the complaint says alarm/notification failed.",
            ],
        }

    def _component_from_classification(
        self,
        result: dict[str, Any],
        components: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        selected_id = str(result.get("affected_component_id") or "")
        if not selected_id:
            return None
        by_id = {str(component.get("id")): component for component in components}
        return by_id.get(selected_id)

    def _classification_terms(self, result: dict[str, Any]) -> list[str]:
        terms = []
        for key in ["issue_type", "use_condition"]:
            terms.extend(dynamic.tokens(str(result.get(key, ""))))
        terms.extend(str(item).lower() for item in result.get("matched_requirement_ids", []) if item)
        return sorted(set(terms))[:8]

    def _compact_component(self, component: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": component.get("id", ""),
            "name": component.get("name", ""),
            "module": component.get("module", ""),
            "part_type": component.get("part_type", ""),
            "safety_relevance": component.get("safety_relevance", ""),
            "match_source": component.get("match_source", ""),
            "match_score": component.get("match_score", 0),
            "match_terms": component.get("match_terms", []),
        }

    def _compact_incident(self, incident: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": incident.get("id", ""),
            "score": incident.get("score", 0),
            "description": incident.get("description") or incident.get("summary") or "",
            "root_cause": incident.get("root_cause", ""),
            "investigation_scope": incident.get("investigation_scope", "")[:800],
            "source_type": incident.get("source_type", ""),
        }
