from __future__ import annotations

from datetime import date
from typing import Any

from backend.graph.queries import check_evidence_freshness
from backend.graph.schema import SourceType
from backend.graph.store import JsonGraphStore, store


def propagate_firmware_change(
    device_id: str,
    new_version: str,
    changed_components: list[str] | None = None,
    change_summary: str = "Firmware change impact analysis",
    graph_store: JsonGraphStore = store,
) -> dict[str, Any]:
    changed_components = changed_components or []
    fw_id = f"FW-{new_version}"
    graph_store.upsert_node(
        fw_id,
        ["SoftwareVersion", "FirmwareVersion"],
        version=new_version,
        release_date=str(date.today()),
        change_summary=change_summary,
    )
    graph_store.upsert_edge(device_id, fw_id, "HAS_VERSION", rationale="What-if firmware candidate", current=False)

    affected_requirements = _affected_requirements(device_id, changed_components, graph_store)
    stale_tests = []
    payload = graph_store.all()
    nodes = {node.id: node for node in payload.nodes}
    for req_id in affected_requirements:
        for edge in graph_store.outgoing(req_id, "VERIFIED_BY"):
            test = nodes.get(edge.target)
            if not test:
                continue
            if test.properties.get("firmware_tested") != new_version:
                req = nodes.get(req_id)
                stale_tests.append(
                    {
                        "requirement_id": req_id,
                        "test_id": test.id,
                        "tested_firmware": test.properties.get("firmware_tested"),
                        "required_firmware": new_version,
                        "reason": "Test evidence does not match the proposed firmware.",
                        "source_type": test.properties.get("source_type", SourceType.INFERRED.value),
                        "source_types": sorted(
                            {
                                str(test.properties.get("source_type", SourceType.INFERRED.value)),
                                str(req.properties.get("source_type", SourceType.INFERRED.value)) if req else SourceType.INFERRED.value,
                            }
                        ),
                        "source_node_ids": [node_id for node_id in [req_id, test.id] if node_id],
                    }
                )

    return {
        "device_id": device_id,
        "new_firmware": new_version,
        "changed_components": changed_components,
        "affected_requirement_count": len(affected_requirements),
        "affected_requirements": sorted(affected_requirements),
        "stale_test_count": len(stale_tests),
        "stale_tests": stale_tests,
        "current_state_stale_tests": check_evidence_freshness(device_id, graph_store),
    }


def _affected_requirements(device_id: str, changed_components: list[str], graph_store: JsonGraphStore) -> set[str]:
    payload = graph_store.all()
    nodes = {node.id: node for node in payload.nodes}
    if changed_components:
        starts = set(changed_components)
    else:
        starts = {
            edge.target
            for edge in payload.edges
            if edge.source == device_id and edge.type == "HAS_COMPONENT"
        }

    affected = set()
    for edge in payload.edges:
        if edge.source in starts and edge.type == "AFFECTS" and "Requirement" in nodes.get(edge.target, object()).labels:
            affected.add(edge.target)

    if affected:
        return affected

    return {
        edge.target
        for edge in payload.edges
        if edge.source == device_id and edge.type == "CONTAINS" and "Requirement" in nodes.get(edge.target, object()).labels
    }
