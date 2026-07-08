from __future__ import annotations

from statistics import mean
from typing import Any

from backend.graph.queries import get_audit_scope
from backend.graph.schema import SourceType
from backend.graph.store import JsonGraphStore, store


WEIGHTS = {"evidence": 40, "freshness": 30, "criteria": 30, "open_capa": 20}


def calculate_readiness_score(device_id: str, graph_store: JsonGraphStore = store) -> dict[str, Any]:
    device = graph_store.get_node(device_id)
    if not device:
        return {"device_id": device_id, "score": 0, "status": "Missing device", "requirements": []}

    current_firmware = device.properties.get("current_firmware")
    requirement_scores = []
    for item in get_audit_scope(device_id, graph_store):
        requirement = item["requirement"]
        tests = item["tests"]
        proof_tests = [test for test in tests if test.get("source_type") != SourceType.SYNTHETIC.value]
        synthetic_tests = [test for test in tests if test.get("source_type") == SourceType.SYNTHETIC.value]
        evidence_exists = bool(proof_tests)
        fresh_tests = [test for test in proof_tests if test.get("firmware_tested") == current_firmware]
        evidence_fresh = bool(fresh_tests)
        acceptance_met = any(test.get("acceptance_criteria_met") is True for test in proof_tests)
        open_capa_count = _open_capa_count_for_requirement(requirement["id"], graph_store)

        score = (
            WEIGHTS["evidence"] * int(evidence_exists)
            + WEIGHTS["freshness"] * int(evidence_fresh)
            + WEIGHTS["criteria"] * int(acceptance_met)
            - WEIGHTS["open_capa"] * int(open_capa_count > 0)
        )
        score = max(0, min(100, score))
        requirement_scores.append(
            {
                "requirement_id": requirement["id"],
                "standard": requirement.get("standard"),
                "score": score,
                "status": _status(score),
                "evidence_exists": evidence_exists,
                "evidence_fresh": evidence_fresh,
                "acceptance_criteria_met": acceptance_met,
                "open_capa_count": open_capa_count,
                "controlled_or_extracted_test_count": len(proof_tests),
                "synthetic_test_count": len(synthetic_tests),
                "current_firmware": current_firmware,
                "tested_firmware_versions": sorted({str(test.get("firmware_tested")) for test in proof_tests if test.get("firmware_tested")}),
                "synthetic_test_ids": [str(test.get("id")) for test in synthetic_tests if test.get("id")],
            }
        )

    device_score = round(mean([item["score"] for item in requirement_scores]), 2) if requirement_scores else 0
    return {
        "device_id": device_id,
        "device_name": device.properties.get("name"),
        "current_firmware": current_firmware,
        "score": device_score,
        "status": _status(device_score),
        "requirements": requirement_scores,
    }


def _open_capa_count_for_requirement(requirement_id: str, graph_store: JsonGraphStore) -> int:
    risk_edges = graph_store.outgoing(requirement_id, "MITIGATED_BY") + graph_store.outgoing(requirement_id, "MITIGATES")
    seen_risks = set()
    count = 0
    for risk_edge in risk_edges:
        if risk_edge.target in seen_risks:
            continue
        seen_risks.add(risk_edge.target)
        capa_edges = graph_store.incoming(risk_edge.target, "ADDRESSES")
        for capa_edge in capa_edges:
            capa = graph_store.get_node(capa_edge.source)
            if capa and capa.properties.get("status") == "Open":
                count += 1
    return count


def _status(score: float) -> str:
    if score >= 80:
        return "Green"
    if score >= 60:
        return "Amber"
    return "Red"
