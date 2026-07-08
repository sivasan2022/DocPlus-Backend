from __future__ import annotations

import re
from typing import Any

from backend.graph.schema import EvidenceClass, SourceType, normalize_source_type
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core.observability import traced
from m2_agents.core.state import EvidenceItem, GraphState
from m2_agents.tools import graph_tools
from m2_agents.tools.evidence_classification import classify_evidence
from m2_agents.tools.vector_tools import retrieve_documents_debug


class EvidenceAgent(BaseAgent):
    name = "evidence"
    GRAPH_EVIDENCE_MINIMUM = 3
    GRAPH_EVIDENCE_LIMIT = 12
    VECTOR_SUPPLEMENT_LIMIT = 3

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "retrieve and validate evidence"):
            state.evidence_collected = []
            current_fw = state.graph_context.get("device", {}).get("current_firmware")
            complaint_fw = (
                state.structured_complaint.firmware_version
                if state.structured_complaint and state.structured_complaint.firmware_version
                else current_fw
            )
            complaint = state.structured_complaint
            query_terms = self._query_terms(state)
            graph_retrieval = {"status": "not_run", "documents": []}
            stage_a_docs: list[dict[str, Any]] = []
            if complaint and complaint.affected_component:
                graph_retrieval = graph_tools.get_component_scoped_evidence(
                    state.device_id,
                    complaint.affected_component,
                    complaint.affected_component_name,
                    query_terms=query_terms,
                    limit=self.GRAPH_EVIDENCE_LIMIT,
                )
                graph_docs = graph_retrieval.get("documents", [])
                primary_graph_docs = [doc for doc in graph_docs if int(doc.get("stage_rank", 99)) <= 2]
                stage_a_docs = primary_graph_docs if len(primary_graph_docs) >= self.GRAPH_EVIDENCE_MINIMUM else graph_docs

            vector_retrievals = []
            stage_b_docs: list[dict[str, Any]] = []
            stage_b_query = self._stage_b_query(state, query_terms)
            vector_retrieval = retrieve_documents_debug(stage_b_query, state.device_id, top_k=8, firmware_filter=current_fw)
            vector_retrievals.append({"purpose": "stage_b_supplement", **vector_retrieval})
            reranked_vector_docs = self._rerank_vector_docs(vector_retrieval.get("documents", []), state)
            if len(stage_a_docs) < self.GRAPH_EVIDENCE_MINIMUM:
                stage_b_docs = reranked_vector_docs[: self.VECTOR_SUPPLEMENT_LIMIT]
            else:
                stage_b_docs = [
                    doc
                    for doc in reranked_vector_docs
                    if str(doc.get("doc_type", "")).lower() in {"complaint", "capa"}
                ][:1]

            final_docs = self._dedupe_docs([*stage_a_docs, *stage_b_docs])
            primary_hypothesis_id = state.hypotheses[0].id if state.hypotheses else None
            for doc in final_docs:
                state.evidence_collected.append(
                    self._to_evidence_item(
                        doc,
                        primary_hypothesis_id,
                        {"current_firmware": current_fw, "complaint_firmware": complaint_fw},
                    )
                )
            traceability_items = self._firmware_traceability_signal_items(state)
            state.evidence_collected.extend(traceability_items)
            if not final_docs and not traceability_items and state.hypotheses:
                for hypothesis in state.hypotheses:
                    state.evidence_collected.append(self._no_evidence_item(hypothesis.id))
            if not state.evidence_collected or all(item.evidence_class == EvidenceClass.NO_EVIDENCE.value for item in state.evidence_collected):
                state.add_event(self.name, "no evidence found", "warning")
            state.add_event(
                self.name,
                "component-scoped evidence retrieval",
                "info",
                affected_component=complaint.affected_component if complaint else "",
                affected_component_name=complaint.affected_component_name if complaint else "",
                component_match_score=complaint.component_match_score if complaint else 0,
                component_match_terms=complaint.component_match_terms if complaint else [],
                graph_evidence_count=len(stage_a_docs),
                vector_supplement_count=len(stage_b_docs),
                firmware_traceability_signal_count=len(traceability_items),
            )
            state.agent_debug[self.name] = {
                "outcome": {
                    "evidence_count": len(state.evidence_collected),
                    "evidence_collected": [item.model_dump() for item in state.evidence_collected],
                    "firmware_traceability_signal_count": len(traceability_items),
                },
                "intake_context_received": {
                    "affected_component": complaint.affected_component if complaint else "",
                    "affected_component_name": complaint.affected_component_name if complaint else "",
                    "component_match_score": complaint.component_match_score if complaint else 0,
                    "component_match_terms": complaint.component_match_terms if complaint else [],
                    "symptom_codes": complaint.symptom_codes if complaint else [],
                },
                "stage_a_component_graph": graph_retrieval,
                "stage_b_vector_retrievals": vector_retrievals,
                "stage_b_used_documents": stage_b_docs,
                "graph_source": "Stage A uses direct M1 component->requirement->test/test-run traversal; Stage B uses constrained Chroma/lexical retrieval only as supplement.",
            }
            state.status = "evidence_collected"
        return state

    def _firmware_traceability_signal_items(self, state: GraphState) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for result in state.digital_twin_results:
            if result.get("status") != "executed":
                continue
            evidence_id = str(result.get("id") or f"DTWIN-{len(items) + 1:03d}")
            check_result = str(result.get("simulated_result") or "inconclusive")
            stale_count = int(result.get("stale_test_count") or 0)
            affected_count = int(result.get("affected_requirement_count") or 0)
            snippet = (
                f"Firmware traceability ripple check: {result.get('scenario', '')}. "
                f"Result={check_result}; affected requirements={affected_count}; stale linked tests={stale_count}. "
                f"{result.get('interpretation', '')}"
            )
            confidence = float(result.get("confidence") or 0.0)
            items.append(
                EvidenceItem(
                    id=evidence_id,
                    hypothesis_id=result.get("hypothesis_id") or None,
                    source=f"Firmware Traceability Ripple Check / {self._traceability_source_label(result)}",
                    snippet=snippet,
                    confidence=confidence,
                    supports=check_result in {"fail", "inconclusive"},
                    citation=self._citation("M2 firmware traceability ripple check", confidence or 0.5),
                    source_type=result.get("source_type") or SourceType.INTERNAL.value,
                    source_node_id=evidence_id,
                    evidence_class=EvidenceClass.SIMULATED.value,
                    review_status="traceability_signal_not_quality_approved",
                    controlled_status="not_controlled",
                    metadata={"firmware_traceability_result": result},
                )
            )
        return items

    def _traceability_source_label(self, result: dict[str, Any]) -> str:
        capability = str(result.get("capability") or "")
        if capability == "firmware_ripple_graph_twin":
            return "firmware-to-test traceability"
        if capability.startswith("unsupported_by_current_twin"):
            return "outside firmware traceability scope"
        return "firmware traceability signal"

    def _to_evidence_item(self, doc: dict[str, Any], hypothesis_id: str | None, complaint_context: dict[str, Any]) -> EvidenceItem:
        evidence_class = classify_evidence(doc, complaint_context)
        source_type = normalize_source_type(doc.get("source_type"), SourceType.INFERRED)
        retrieval_stage = str((doc.get("metadata") or {}).get("retrieval_stage") or doc.get("retrieval_backend") or "")
        direct_graph = retrieval_stage == "component_graph" or doc.get("retrieval_backend") == "component_graph"
        support_threshold = 0.75 if direct_graph else 0.6
        return EvidenceItem(
            id=doc["id"],
            hypothesis_id=hypothesis_id,
            source=doc["source"],
            snippet=doc["snippet"],
            confidence=doc["confidence"],
            supports=doc["confidence"] >= support_threshold and evidence_class != EvidenceClass.NO_EVIDENCE.value,
            citation=doc["citation"],
            source_type=source_type,
            source_node_id=doc.get("source_node_id") or doc["id"],
            evidence_class=evidence_class,
            review_status=doc.get("review_status", ""),
            controlled_status=doc.get("controlled_status", ""),
            metadata=doc.get("metadata", {}),
        )

    def _no_evidence_item(self, hypothesis_id: str) -> EvidenceItem:
        return EvidenceItem(
            id=f"NO-EVIDENCE-{hypothesis_id}",
            hypothesis_id=hypothesis_id,
            source="No direct controlled evidence retrieved",
            snippet="No retrieved graph/vector evidence directly supported this hypothesis.",
            confidence=0.0,
            supports=False,
            citation=self._citation("M2 evidence retrieval absence", 0.6),
            source_type=SourceType.INTERNAL.value,
            source_node_id="",
            evidence_class=EvidenceClass.NO_EVIDENCE.value,
            metadata={"query": hypothesis_id},
        )

    def _query_terms(self, state: GraphState) -> list[str]:
        complaint = state.structured_complaint
        values = [state.raw_complaint or ""]
        if complaint:
            values.extend(
                [
                    complaint.affected_component,
                    complaint.affected_component_name,
                    *complaint.component_match_terms,
                    *complaint.symptom_codes,
                ]
            )
        values.extend(hypothesis.description for hypothesis in state.hypotheses[:3])
        return [
            token
            for token in re.findall(r"[a-zA-Z0-9]+", " ".join(values).lower())
            if len(token) > 2 and token not in {"device", "reported", "symptom", "complaint", "after", "with"}
        ]

    def _stage_b_query(self, state: GraphState, query_terms: list[str]) -> str:
        complaint = state.structured_complaint
        component_context = ""
        if complaint:
            component_context = " ".join(
                [
                    complaint.affected_component,
                    complaint.affected_component_name,
                    *complaint.component_match_terms,
                    *complaint.symptom_codes,
                ]
            )
        hypothesis_context = " ".join(hypothesis.description for hypothesis in state.hypotheses[:2])
        return " ".join([state.raw_complaint or "", component_context, hypothesis_context, " ".join(query_terms[:12])]).strip()

    def _rerank_vector_docs(self, docs: list[dict[str, Any]], state: GraphState) -> list[dict[str, Any]]:
        terms = set(self._query_terms(state))
        reranked = []
        for doc in docs:
            haystack = " ".join(
                str(doc.get(key, ""))
                for key in ["id", "source", "title", "snippet", "category", "doc_type", "firmware_version"]
            ).lower()
            overlap = sum(1 for term in terms if term in haystack)
            doc_type = str(doc.get("doc_type", "")).lower()
            if overlap <= 0 and doc_type not in {"complaint", "capa"}:
                continue
            adjusted = dict(doc)
            adjusted["confidence"] = round(min(0.78, max(float(doc.get("confidence", 0.55)), 0.55 + overlap * 0.04)), 2)
            adjusted.setdefault("metadata", {})
            adjusted["metadata"] = {**adjusted["metadata"], "retrieval_stage": "vector_supplement", "component_overlap_terms": overlap}
            adjusted["stage_b_overlap"] = overlap
            reranked.append(adjusted)
        return sorted(reranked, key=lambda item: (-item.get("stage_b_overlap", 0), -item.get("confidence", 0), str(item.get("id", ""))))

    def _dedupe_docs(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        deduped = []
        for doc in docs:
            key = str(doc.get("id", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(doc)
        return deduped
