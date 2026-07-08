from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from backend.graph.schema import SourceType, normalize_source_type
from backend.graph.store import JsonGraphStore, store
from backend.models.schemas import GraphPayload


def get_neighborhood(node_id: str, depth: int = 2, graph_store: JsonGraphStore = store) -> GraphPayload:
    return graph_store.neighborhood(node_id, depth=depth)


def get_audit_scope(device_id: str, graph_store: JsonGraphStore = store) -> list[dict[str, Any]]:
    payload = graph_store.all()
    nodes = {node.id: node for node in payload.nodes}
    requirements = [
        edge.target
        for edge in payload.edges
        if edge.source == device_id and edge.type == "CONTAINS" and edge.target in nodes
    ]
    scope = []
    for req_id in requirements:
        req = nodes[req_id]
        tests = [
            nodes[edge.target]
            for edge in payload.edges
            if edge.source == req_id and edge.type == "VERIFIED_BY" and edge.target in nodes
        ]
        risks = [
            nodes[edge.target]
            for edge in payload.edges
            if edge.source == req_id and edge.type in {"MITIGATED_BY", "MITIGATES"} and edge.target in nodes
        ]
        deduped_risks = {risk.id: risk for risk in risks}
        scope.append(
            {
                "requirement": {"id": req.id, **req.properties},
                "tests": [{"id": test.id, **test.properties} for test in tests],
                "risks": [{"id": risk.id, **risk.properties} for risk in deduped_risks.values()],
            }
        )
    return scope


def component_scoped_evidence(
    device_id: str,
    component_id: str,
    component_name: str = "",
    query_terms: list[str] | None = None,
    limit: int = 12,
    graph_store: JsonGraphStore = store,
) -> dict[str, Any]:
    payload = graph_store.all()
    nodes = {node.id: node for node in payload.nodes}
    outgoing: dict[str, list[Any]] = defaultdict(list)
    incoming: dict[str, list[Any]] = defaultdict(list)
    for edge in payload.edges:
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    matched_components = _matched_components(device_id, component_id, component_name, nodes, payload.edges)
    requirement_ids = _component_requirement_ids(device_id, matched_components, component_name, nodes, payload.edges)
    terms = _normalized_terms(" ".join(query_terms or []))
    rows: list[dict[str, Any]] = []

    for req_id in requirement_ids:
        req = nodes.get(req_id)
        if not req:
            continue
        tests = [nodes[edge.target] for edge in outgoing.get(req_id, []) if edge.type == "VERIFIED_BY" and edge.target in nodes]
        risks = [
            nodes[edge.target]
            for edge in outgoing.get(req_id, [])
            if edge.type in {"MITIGATED_BY", "MITIGATES"} and edge.target in nodes
        ]
        if tests:
            for test in tests:
                runs = [nodes[edge.target] for edge in outgoing.get(test.id, []) if edge.type == "EXECUTED_AS" and edge.target in nodes]
                produced = [nodes[edge.target] for run in runs for edge in outgoing.get(run.id, []) if edge.type == "PRODUCED" and edge.target in nodes]
                supported = [nodes[edge.target] for edge in outgoing.get(test.id, []) if edge.type == "SUPPORTED_BY" and edge.target in nodes]
                if runs:
                    for run in runs:
                        rows.append(
                            _component_evidence_row(
                                evidence_node=run,
                                requirement=req,
                                test=test,
                                linked_nodes=[*produced, *supported, *risks],
                                component_ids=matched_components,
                                terms=terms,
                                stage_rank=1,
                            )
                        )
                else:
                    rows.append(
                        _component_evidence_row(
                            evidence_node=test,
                            requirement=req,
                            test=test,
                            linked_nodes=[*supported, *risks],
                            component_ids=matched_components,
                            terms=terms,
                            stage_rank=2,
                        )
                    )
        else:
            rows.append(
                _component_evidence_row(
                    evidence_node=req,
                    requirement=req,
                    test=None,
                    linked_nodes=risks,
                    component_ids=matched_components,
                    terms=terms,
                    stage_rank=3,
                )
            )

        for risk in risks:
            controls = [nodes[edge.target] for edge in outgoing.get(risk.id, []) if edge.type == "CONTROLLED_BY" and edge.target in nodes]
            capas = [nodes[edge.source] for edge in incoming.get(risk.id, []) if edge.type == "ADDRESSES" and edge.source in nodes]
            for node in [*controls, *capas]:
                rows.append(
                    _component_evidence_row(
                        evidence_node=node,
                        requirement=req,
                        test=None,
                        linked_nodes=[risk],
                        component_ids=matched_components,
                        terms=terms,
                        stage_rank=4,
                    )
                )

    deduped = {}
    for row in rows:
        deduped.setdefault(row["id"], row)
    ranked = sorted(deduped.values(), key=lambda item: (-item["direct_match_score"], item["stage_rank"], item["id"]))
    selected = ranked[:limit]
    return {
        "device_id": device_id,
        "component_id": component_id,
        "component_name": component_name,
        "matched_components": matched_components,
        "requirement_ids": requirement_ids,
        "query_terms": sorted(terms),
        "returned_count": len(selected),
        "documents": selected,
    }


def get_audit_trail(node_id: str, graph_store: JsonGraphStore = store) -> dict[str, Any]:
    node = graph_store.get_node(node_id)
    if not node:
        return {"node_id": node_id, "events": []}
    incoming = graph_store.incoming(node_id)
    outgoing = graph_store.outgoing(node_id)
    events = []
    for edge in incoming + outgoing:
        events.append(
            {
                "relationship": edge.type,
                "source": edge.source,
                "target": edge.target,
                "rationale": edge.properties.get("rationale", "No rationale recorded"),
                "valid_from": edge.properties.get("valid_from", "2026-06-01"),
                "valid_to": edge.properties.get("valid_to"),
            }
        )
    return {"node_id": node_id, "labels": node.labels, "properties": node.properties, "events": events}


def _matched_components(
    device_id: str,
    component_id: str,
    component_name: str,
    nodes: dict[str, Any],
    edges: list[Any],
) -> list[str]:
    component_ids = {
        edge.target
        for edge in edges
        if edge.source == device_id and edge.type == "HAS_COMPONENT" and edge.target in nodes
    }
    matches: list[str] = []
    if component_id and component_id in nodes:
        matches.append(component_id)
    normalized_name = _normalize_token(component_name)
    for candidate_id in sorted(component_ids):
        node = nodes[candidate_id]
        values = " ".join(
            str(node.properties.get(key, ""))
            for key in ["name", "module", "part_type", "category", "description"]
        )
        if normalized_name and normalized_name in {_normalize_token(value) for value in re.findall(r"[a-zA-Z0-9_+-]+", values)}:
            matches.append(candidate_id)
        elif normalized_name and normalized_name in _normalize_token(values):
            matches.append(candidate_id)
    return list(dict.fromkeys(matches))


def _component_requirement_ids(
    device_id: str,
    component_ids: list[str],
    component_name: str,
    nodes: dict[str, Any],
    edges: list[Any],
) -> list[str]:
    ids: list[str] = []
    for edge in edges:
        if edge.source in component_ids and edge.type == "AFFECTS" and edge.target in nodes and "Requirement" in nodes[edge.target].labels:
            ids.append(edge.target)
    component_name_norm = _normalize_token(component_name)
    for edge in edges:
        if edge.source != device_id or edge.type != "CONTAINS" or edge.target not in nodes:
            continue
        req = nodes[edge.target]
        if "Requirement" not in req.labels:
            continue
        req_component = str(req.properties.get("component_id", ""))
        req_module = str(req.properties.get("module", ""))
        if req_component in component_ids:
            ids.append(req.id)
        elif component_name_norm and component_name_norm in _normalize_token(req_module):
            ids.append(req.id)
    return list(dict.fromkeys(ids))


def _component_evidence_row(
    evidence_node: Any,
    requirement: Any,
    test: Any | None,
    linked_nodes: list[Any],
    component_ids: list[str],
    terms: set[str],
    stage_rank: int,
) -> dict[str, Any]:
    evidence_props = evidence_node.properties
    requirement_props = requirement.properties
    test_props = test.properties if test else {}
    linked_text = " ".join(_node_text(node) for node in linked_nodes[:4])
    haystack = " ".join([_node_text(requirement), _node_text(test) if test else "", _node_text(evidence_node), linked_text])
    overlap = len(terms & _normalized_terms(haystack)) if terms else 0
    firmware = (
        evidence_props.get("firmware_version")
        or evidence_props.get("firmware_tested")
        or test_props.get("firmware_tested")
        or ""
    )
    result = str(evidence_props.get("result") or evidence_props.get("test_result") or test_props.get("result") or "")
    source_type = normalize_source_type(evidence_props.get("source_type") or test_props.get("source_type") or requirement_props.get("source_type"), SourceType.INFERRED)
    source_title = evidence_props.get("title") or evidence_props.get("name") or evidence_props.get("source_artifact") or evidence_node.id
    req_text = requirement_props.get("text") or requirement_props.get("acceptance_criteria") or ""
    test_name = test_props.get("name") or test.id if test else ""
    confidence = _direct_confidence(stage_rank, source_type, result, bool(firmware), overlap)
    node_ids = [evidence_node.id, requirement.id]
    if test:
        node_ids.append(test.id)
    node_ids.extend(node.id for node in linked_nodes[:5])
    return {
        "id": evidence_node.id,
        "source_node_id": evidence_node.id,
        "source": f"{requirement.id} / {test.id if test else evidence_node.id} / {source_title}",
        "title": f"{requirement.id} evidence for {test.id if test else evidence_node.id}",
        "snippet": _row_snippet(requirement.id, req_text, test, evidence_node, linked_nodes),
        "confidence": confidence,
        "category": evidence_props.get("category", evidence_props.get("doc_type", _primary_label(evidence_node))),
        "doc_type": _primary_label(evidence_node),
        "firmware_version": str(firmware),
        "source_type": source_type,
        "review_status": evidence_props.get("review_status") or test_props.get("review_status") or requirement_props.get("review_status", ""),
        "controlled_status": evidence_props.get("controlled_status") or test_props.get("controlled_status") or requirement_props.get("controlled_status", ""),
        "objective_evidence": bool(evidence_props.get("objective_evidence") or test_props.get("objective_evidence", False)),
        "labels": evidence_node.labels,
        "page_number": evidence_props.get("page_number", 1),
        "metadata": {
            "retrieval_stage": "component_graph",
            "component_ids": component_ids,
            "requirement_id": requirement.id,
            "test_id": test.id if test else "",
            "linked_node_ids": list(dict.fromkeys(node_ids)),
            "source_type": source_type,
            "review_status": evidence_props.get("review_status") or test_props.get("review_status") or requirement_props.get("review_status", ""),
            "controlled_status": evidence_props.get("controlled_status") or test_props.get("controlled_status") or requirement_props.get("controlled_status", ""),
        },
        "citation": f"[Source: {requirement.id}/{test.id if test else evidence_node.id}, Confidence: {confidence}]",
        "relevance_score": confidence,
        "retrieval_backend": "component_graph",
        "direct_match_score": 100 - (stage_rank * 10) + overlap,
        "stage_rank": stage_rank,
    }


def _row_snippet(requirement_id: str, req_text: str, test: Any | None, evidence_node: Any, linked_nodes: list[Any]) -> str:
    test_part = ""
    if test:
        test_part = (
            f" Test {test.id}: {test.properties.get('name', '')}; "
            f"result={test.properties.get('result', test.properties.get('test_result', ''))}; "
            f"firmware={test.properties.get('firmware_tested', '')}."
        )
    evidence_part = (
        f" Evidence {evidence_node.id}: "
        f"result={evidence_node.properties.get('result', evidence_node.properties.get('test_result', ''))}; "
        f"firmware={evidence_node.properties.get('firmware_version', evidence_node.properties.get('firmware_tested', ''))}."
    )
    linked = "; ".join(
        f"{node.id}: {_node_text(node)[:120]}"
        for node in linked_nodes[:2]
        if node
    )
    return " ".join(part for part in [f"{requirement_id}: {req_text}", test_part, evidence_part, linked] if part).strip()


def _direct_confidence(stage_rank: int, source_type: str, result: str, has_firmware: bool, overlap: int) -> float:
    base = {1: 0.9, 2: 0.84, 3: 0.78, 4: 0.72}.get(stage_rank, 0.68)
    if source_type == SourceType.SYNTHETIC.value:
        base -= 0.18
    elif source_type == SourceType.EXTRACTED.value:
        base -= 0.02
    if result.lower() in {"pass", "passed"}:
        base += 0.04
    if has_firmware:
        base += 0.02
    base += min(overlap, 3) * 0.01
    return round(max(0.5, min(0.96, base)), 2)


def _primary_label(node: Any) -> str:
    for label in node.labels:
        if label != "MedTraceNode":
            return label
    return node.labels[0] if node.labels else "Evidence"


def _node_text(node: Any | None) -> str:
    if not node:
        return ""
    return " ".join(
        str(node.properties.get(key, ""))
        for key in [
            "title",
            "name",
            "category",
            "doc_type",
            "description",
            "text",
            "acceptance_criteria",
            "hazard",
            "risk_control",
            "effectiveness_evidence",
            "root_cause",
            "action",
            "summary",
            "snippet",
            "source_path",
            "source_artifact",
            "module",
            "component_id",
            "firmware_tested",
            "result",
            "evidence_summary",
        ]
        if node.properties.get(key)
    )


def _normalized_terms(text: str) -> set[str]:
    return {
        _normalize_token(term)
        for term in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if len(term) > 2 and term not in {"device", "reported", "shows", "after", "with", "during", "wrong", "requirement", "evidence"}
    }


def _normalize_token(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    for suffix in ("ing", "ed", "es", "s"):
        if len(text) > len(suffix) + 3 and text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def check_evidence_freshness(device_id: str, graph_store: JsonGraphStore = store) -> list[dict[str, Any]]:
    device = graph_store.get_node(device_id)
    if not device:
        return []
    current_firmware = device.properties.get("current_firmware")
    stale = []
    for item in get_audit_scope(device_id, graph_store):
        for test in item["tests"]:
            firmware_tested = test.get("firmware_tested")
            if firmware_tested and firmware_tested != current_firmware:
                stale.append(
                    {
                        "requirement_id": item["requirement"]["id"],
                        "test_id": test["id"],
                        "firmware_tested": firmware_tested,
                        "current_firmware": current_firmware,
                        "status": "STALE",
                    }
                )
    return stale


def find_affected_nodes(origin_id: str, depth: int = 3, graph_store: JsonGraphStore = store) -> dict[str, Any]:
    allowed_edges = {"AFFECTS", "VERIFIED_BY", "MITIGATED_BY", "SUPPORTED_BY", "ADDRESSES", "TRIGGERS"}
    payload = graph_store.neighborhood(origin_id, depth=depth, allowed_edge_types=allowed_edges)
    return {
        "origin_id": origin_id,
        "depth": depth,
        "nodes": [node.model_dump() for node in payload.nodes],
        "edges": [edge.model_dump() for edge in payload.edges],
    }


def find_similar_complaints(term: str, limit: int = 5, graph_store: JsonGraphStore = store) -> list[dict[str, Any]]:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", term.lower())
        if len(token) > 3 and token not in {"device", "reported", "shows", "after", "with", "during", "wrong"}
    ]
    matches = []
    for complaint in graph_store.nodes_by_label("Complaint"):
        description = str(complaint.properties.get("description", ""))
        score = sum(1 for token in tokens if token in description.lower())
        if score:
            matches.append({"id": complaint.id, "score": score, **complaint.properties})
    return sorted(matches, key=lambda item: item["score"], reverse=True)[:limit]


def requirements_matrix(device_id: str, graph_store: JsonGraphStore = store) -> list[dict[str, Any]]:
    rows = []
    for item in get_audit_scope(device_id, graph_store):
        requirement = item["requirement"]
        tests = item["tests"] or [{"id": None, "result": "Missing", "firmware_tested": None}]
        risks = item["risks"] or [{"id": None, "hazard": "No risk linked"}]
        for test in tests:
            rows.append(
                {
                    "requirement_id": requirement["id"],
                    "standard": requirement.get("standard"),
                    "requirement": requirement.get("text"),
                    "test_id": test.get("id"),
                    "test_result": test.get("result"),
                    "firmware_tested": test.get("firmware_tested"),
                    "risk_ids": [risk.get("id") for risk in risks if risk.get("id")],
                }
            )
    return rows


def capa_context(
    device_id: str,
    requirement_ids: list[str] | None = None,
    graph_store: JsonGraphStore = store,
) -> dict[str, Any]:
    scoped_ids = set(requirement_ids or [])
    audit_scope = [
        item
        for item in get_audit_scope(device_id, graph_store)
        if not scoped_ids or item["requirement"]["id"] in scoped_ids
    ]
    requirements = []
    open_capas: dict[str, dict[str, Any]] = {}
    evidence_gaps = 0
    stale_or_review_tests = 0
    for item in audit_scope:
        requirement = item["requirement"]
        tests = item["tests"]
        risks = item["risks"]
        if not tests:
            evidence_gaps += 1
        for test in tests:
            result = str(test.get("result", test.get("test_result", ""))).lower()
            if result and result not in {"pass", "passed"}:
                stale_or_review_tests += 1
        for risk in risks:
            for edge in graph_store.incoming(risk.get("id", ""), "ADDRESSES"):
                capa = graph_store.get_node(edge.source)
                if capa and capa.properties.get("status") == "Open":
                    open_capas[capa.id] = {"id": capa.id, **capa.properties}
        requirements.append(
            {
                "requirement_id": requirement.get("id"),
                "requirement_text": requirement.get("text") or requirement.get("acceptance_criteria"),
                "source_artifact": requirement.get("source_artifact"),
                "tests": tests,
                "risks": risks,
            }
        )
    return {
        "device_id": device_id,
        "scoped_requirement_count": len(requirements),
        "verification_record_count": sum(len(item["tests"]) for item in requirements),
        "risk_count": sum(len(item["risks"]) for item in requirements),
        "evidence_gap_count": evidence_gaps,
        "stale_or_review_test_count": stale_or_review_tests,
        "open_capas": list(open_capas.values()),
        "open_capa_count": len(open_capas),
        "plain_language_summary": {
            "review_scope": "Relevant device requirements, verification records, risk controls, and open quality actions were reviewed for the complaint scope.",
            "evidence_status": (
                "Some verification evidence still needs Quality review before CAPA closure."
                if stale_or_review_tests or evidence_gaps
                else "The scoped verification evidence is available for Quality review."
            ),
            "closure_focus": "Closure should focus on confirming the root cause, proving the correction works, preventing recurrence, and documenting Quality/Regulatory approval.",
        },
        "requirements": requirements,
    }


def graph_kpis(device_id: str, graph_store: JsonGraphStore = store) -> dict[str, Any]:
    payload = graph_store.all()
    label_counts: dict[str, int] = defaultdict(int)
    for node in payload.nodes:
        for label in node.labels:
            label_counts[label] += 1
    stale = check_evidence_freshness(device_id, graph_store)
    open_capas = [node for node in graph_store.nodes_by_label("CAPA") if node.properties.get("status") == "Open"]
    return {
        "device_id": device_id,
        "node_count": len(payload.nodes),
        "edge_count": len(payload.edges),
        "label_counts": dict(sorted(label_counts.items())),
        "stale_test_count": len(stale),
        "open_capa_count": len(open_capas),
        "orphan_count": len(graph_store.validate_no_orphans()),
    }
