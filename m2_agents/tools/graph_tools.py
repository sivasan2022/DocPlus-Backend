from __future__ import annotations

from typing import Any

from backend.graph import queries
from backend.graph.readiness_score import calculate_readiness_score
from backend.graph.ripple_engine import propagate_firmware_change
from backend.graph.store import store
from m2_agents.core.trace_ai import traced_tool


@traced_tool("graph_tools.list_devices")
def list_devices() -> list[dict[str, Any]]:
    return [{"id": node.id, **node.properties} for node in store.nodes_by_label("Device")]


@traced_tool("graph_tools.resolve_device_id")
def resolve_device_id(device_id: str | None = None) -> str:
    if device_id and store.get_node(device_id):
        return device_id
    devices = list_devices()
    if not devices:
        raise ValueError("No Device nodes found. Seed or upload device data before running M2.")
    if device_id:
        raise ValueError(f"Device not found: {device_id}")
    return devices[0]["id"]


@traced_tool("graph_tools.get_device_context")
def get_device_context(device_id: str) -> dict[str, Any]:
    device_id = resolve_device_id(device_id)
    twin = store.device_twin(device_id)
    device = store.get_node(device_id)
    components = [node for node in twin.nodes if "Component" in node.labels]
    firmware = [node for node in twin.nodes if "SoftwareVersion" in node.labels or "FirmwareVersion" in node.labels]
    requirements = [node for node in twin.nodes if "Requirement" in node.labels]
    risks = [node for node in twin.nodes if "Risk" in node.labels]
    complaints = [node for node in twin.nodes if "Complaint" in node.labels]
    edges_by_node: dict[str, int] = {}
    for edge in twin.edges:
        edges_by_node[edge.source] = edges_by_node.get(edge.source, 0) + 1
        edges_by_node[edge.target] = edges_by_node.get(edge.target, 0) + 1
    standards = sorted({str(node.properties.get("standard")) for node in requirements if node.properties.get("standard")})
    return {
        "device": {"id": device.id, **device.properties} if device else {"id": device_id},
        "components": [{"id": node.id, "degree": edges_by_node.get(node.id, 0), **node.properties} for node in components],
        "firmware_versions": [{"id": node.id, **node.properties} for node in firmware],
        "requirements": [{"id": node.id, **node.properties} for node in requirements],
        "risks": [{"id": node.id, **node.properties} for node in risks],
        "complaints": [{"id": node.id, **node.properties} for node in complaints],
        "standards": standards,
        "readiness": calculate_readiness_score(device_id),
        "stale_evidence": queries.check_evidence_freshness(device_id),
        "open_capa_count": sum(1 for node in twin.nodes if "CAPA" in node.labels and node.properties.get("status") == "Open"),
        "node_count": len(twin.nodes),
        "edge_count": len(twin.edges),
    }


@traced_tool("graph_tools.get_graph_neighborhood")
def get_graph_neighborhood(node_id: str, depth: int = 2) -> dict[str, Any]:
    payload = queries.get_neighborhood(node_id, depth)
    return {
        "nodes": [node.model_dump() for node in payload.nodes],
        "edges": [edge.model_dump() for edge in payload.edges],
    }


@traced_tool("graph_tools.find_similar_complaints")
def find_similar_complaints(term: str, limit: int = 5) -> list[dict[str, Any]]:
    return queries.find_similar_complaints(term, limit)


@traced_tool("graph_tools.get_audit_scope")
def get_audit_scope(device_id: str) -> list[dict[str, Any]]:
    device_id = resolve_device_id(device_id)
    return queries.get_audit_scope(device_id)


@traced_tool("graph_tools.get_component_scoped_evidence")
def get_component_scoped_evidence(
    device_id: str,
    component_id: str,
    component_name: str = "",
    query_terms: list[str] | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    device_id = resolve_device_id(device_id)
    return queries.component_scoped_evidence(device_id, component_id, component_name, query_terms or [], limit)


@traced_tool("graph_tools.get_traceability_matrix")
def get_traceability_matrix(device_id: str) -> list[dict[str, Any]]:
    device_id = resolve_device_id(device_id)
    return queries.requirements_matrix(device_id)


@traced_tool("graph_tools.get_capa_context")
def get_capa_context(device_id: str, requirement_ids: list[str] | None = None) -> dict[str, Any]:
    device_id = resolve_device_id(device_id)
    return queries.capa_context(device_id, requirement_ids)


@traced_tool("graph_tools.get_evidence_freshness")
def get_evidence_freshness(device_id: str) -> list[dict[str, Any]]:
    device_id = resolve_device_id(device_id)
    return queries.check_evidence_freshness(device_id)


@traced_tool("graph_tools.get_readiness")
def get_readiness(device_id: str) -> dict[str, Any]:
    device_id = resolve_device_id(device_id)
    return calculate_readiness_score(device_id)


@traced_tool("graph_tools.run_ripple")
def run_ripple(device_id: str, new_version: str, changed_components: list[str] | None = None) -> dict[str, Any]:
    device_id = resolve_device_id(device_id)
    return propagate_firmware_change(device_id, new_version, changed_components or [], "M2 trace decay analysis")


@traced_tool("graph_tools.open_capa_count")
def open_capa_count(device_id: str) -> int:
    device_id = resolve_device_id(device_id)
    twin = store.device_twin(device_id)
    return sum(1 for node in twin.nodes if "CAPA" in node.labels and node.properties.get("status") == "Open")


@traced_tool("graph_tools.list_evidence")
def list_evidence(device_id: str, limit: int = 20) -> list[dict[str, Any]]:
    device_id = resolve_device_id(device_id)
    twin = store.device_twin(device_id)
    evidence = [node for node in twin.nodes if "Evidence" in node.labels]
    return [{"id": node.id, **node.properties} for node in evidence[:limit]]
