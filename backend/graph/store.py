from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from uuid import uuid4
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from dotenv import load_dotenv

from backend.graph.schema import SourceType, normalize_source_type
from backend.models.schemas import GraphEdge, GraphNode, GraphPayload

load_dotenv()


DEFAULT_STORE_PATH = Path(os.getenv("MEDTRACE_STORE_PATH", "data/runtime/medtrace_graph.json"))


class JsonGraphStore:
    """Small deterministic graph store for the no-Docker M1 demo."""

    def __init__(self, path: str | Path = DEFAULT_STORE_PATH):
        self.path = Path(path)
        self._lock = RLock()
        self._batch_depth = 0
        self._batch_graph: dict[str, Any] | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"nodes": {}, "edges": []})

    @contextmanager
    def batch(self) -> Iterator["JsonGraphStore"]:
        with self._lock:
            if self._batch_depth == 0:
                self._batch_graph = self._read()
            self._batch_depth += 1
        failed = False
        try:
            yield self
        except Exception:
            failed = True
            raise
        finally:
            with self._lock:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    graph = self._batch_graph
                    self._batch_graph = None
                    if graph is not None and not failed:
                        self._write(graph)

    def reset(self) -> None:
        with self._lock:
            self._write({"nodes": {}, "edges": []})

    def replace_all(self, payload: GraphPayload) -> None:
        """Atomically replace the graph after validating node and edge references."""
        with self._lock:
            graph = _payload_to_raw_graph(payload)
            self._write(graph)

    def upsert_node(self, node_id: str, labels: list[str], **properties: Any) -> GraphNode:
        with self._lock:
            graph = self._read()
            existing = graph["nodes"].get(node_id, {"id": node_id, "labels": [], "properties": {}})
            merged_labels = sorted(set(existing["labels"]) | set(labels))
            merged_properties = {**existing["properties"], **{k: v for k, v in properties.items() if v is not None}}
            merged_properties = _with_required_m1_metadata(node_id, merged_labels, merged_properties)
            graph["nodes"][node_id] = {
                "id": node_id,
                "labels": merged_labels,
                "properties": merged_properties,
            }
            if self._batch_graph is None:
                self._write(graph)
            return GraphNode(**graph["nodes"][node_id])

    def upsert_edge(self, source: str, target: str, edge_type: str, **properties: Any) -> GraphEdge:
        with self._lock:
            graph = self._read()
            if source not in graph["nodes"]:
                raise ValueError(f"Cannot create edge from missing node: {source}")
            if target not in graph["nodes"]:
                raise ValueError(f"Cannot create edge to missing node: {target}")

            clean_props = {k: v for k, v in properties.items() if v is not None}
            for edge in graph["edges"]:
                if edge["source"] == source and edge["target"] == target and edge["type"] == edge_type:
                    edge["properties"] = {**edge.get("properties", {}), **clean_props}
                    if self._batch_graph is None:
                        self._write(graph)
                    return GraphEdge(**edge)

            edge = {"source": source, "target": target, "type": edge_type, "properties": clean_props}
            graph["edges"].append(edge)
            if self._batch_graph is None:
                self._write(graph)
            return GraphEdge(**edge)

    def get_node(self, node_id: str) -> GraphNode | None:
        graph = self._read()
        raw = graph["nodes"].get(node_id)
        return GraphNode(**raw) if raw else None

    def nodes_by_label(self, label: str) -> list[GraphNode]:
        graph = self._read()
        return [GraphNode(**raw) for raw in graph["nodes"].values() if label in raw["labels"]]

    def all(self) -> GraphPayload:
        graph = self._read()
        return GraphPayload(
            nodes=[GraphNode(**raw) for raw in graph["nodes"].values()],
            edges=[GraphEdge(**raw) for raw in graph["edges"]],
        )

    def device_twin(self, device_id: str) -> GraphPayload:
        return self.neighborhood(device_id, depth=6, allowed_edge_types=None)

    def neighborhood(
        self,
        node_id: str,
        depth: int = 2,
        allowed_edge_types: set[str] | None = None,
    ) -> GraphPayload:
        graph = self._read()
        if node_id not in graph["nodes"]:
            return GraphPayload()

        depth = max(1, min(depth, 6))
        visited = {node_id}
        frontier = {node_id}
        selected_edges: list[dict[str, Any]] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in graph["edges"]:
                if allowed_edge_types and edge["type"] not in allowed_edge_types:
                    continue
                touches_frontier = edge["source"] in frontier or edge["target"] in frontier
                if not touches_frontier:
                    continue
                selected_edges.append(edge)
                other_ids = {edge["source"], edge["target"]} - visited
                next_frontier.update(other_ids)
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        nodes = [GraphNode(**graph["nodes"][nid]) for nid in sorted(visited) if nid in graph["nodes"]]
        deduped_edges = {
            (e["source"], e["target"], e["type"]): e
            for e in selected_edges
            if e["source"] in visited and e["target"] in visited
        }
        return GraphPayload(nodes=nodes, edges=[GraphEdge(**edge) for edge in deduped_edges.values()])

    def outgoing(self, source: str, edge_type: str | None = None) -> list[GraphEdge]:
        graph = self._read()
        return [
            GraphEdge(**edge)
            for edge in graph["edges"]
            if edge["source"] == source and (edge_type is None or edge["type"] == edge_type)
        ]

    def incoming(self, target: str, edge_type: str | None = None) -> list[GraphEdge]:
        graph = self._read()
        return [
            GraphEdge(**edge)
            for edge in graph["edges"]
            if edge["target"] == target and (edge_type is None or edge["type"] == edge_type)
        ]

    def validate_no_orphans(self) -> list[str]:
        graph = self._read()
        connected: set[str] = set()
        for edge in graph["edges"]:
            connected.add(edge["source"])
            connected.add(edge["target"])
        return sorted(node_id for node_id in graph["nodes"] if node_id not in connected)

    def counts(self) -> dict[str, int]:
        graph = self._read()
        return {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])}

    def _read(self) -> dict[str, Any]:
        with self._lock:
            if self._batch_graph is not None:
                return self._batch_graph
            last_error: Exception | None = None
            for _ in range(5):
                try:
                    raw = self.path.read_text(encoding="utf-8")
                    if raw.strip():
                        return json.loads(raw)
                except (json.JSONDecodeError, OSError) as exc:
                    last_error = exc
                time.sleep(0.05)
            if last_error:
                raise last_error
            return {"nodes": {}, "edges": []}

    def _write(self, graph: dict[str, Any]) -> None:
        with self._lock:
            tmp_path = self.path.with_name(f"{self.path.stem}.{os.getpid()}.{uuid4().hex}.tmp")
            tmp_path.write_text(json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8")
            last_error: Exception | None = None
            for _ in range(10):
                try:
                    tmp_path.replace(self.path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.1)
            tmp_path.unlink(missing_ok=True)
            if last_error:
                raise last_error


store = JsonGraphStore()


def _payload_to_raw_graph(payload: GraphPayload) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for node in payload.nodes:
        node_id = str(node.id).strip()
        if not node_id:
            raise ValueError("Cannot store a graph node with an empty id.")
        if node_id in nodes:
            raise ValueError(f"Duplicate graph node id: {node_id}")
        nodes[node_id] = {
            "id": node_id,
            "labels": [str(label) for label in node.labels],
            "properties": dict(node.properties),
        }

    edges: list[dict[str, Any]] = []
    for edge in payload.edges:
        source = str(edge.source).strip()
        target = str(edge.target).strip()
        edge_type = str(edge.type).strip()
        if not source or not target or not edge_type:
            raise ValueError("Cannot store a graph edge with an empty source, target, or type.")
        if source not in nodes:
            raise ValueError(f"Cannot store edge from missing node: {source}")
        if target not in nodes:
            raise ValueError(f"Cannot store edge to missing node: {target}")
        edges.append(
            {
                "source": source,
                "target": target,
                "type": edge_type,
                "properties": dict(edge.properties),
            }
        )

    return {"nodes": nodes, "edges": edges}


def _with_required_m1_metadata(node_id: str, labels: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    """Ensure every node carries the M1 evidence confidence fields."""
    enriched = dict(properties)

    raw_source_type = enriched.get("source_type")
    if raw_source_type:
        source_type = normalize_source_type(raw_source_type, SourceType.INFERRED)
    else:
        source_type = _infer_source_type(node_id, labels, enriched)
    enriched["source_type"] = source_type

    enriched.setdefault("review_status", _default_review_status(source_type, enriched))
    enriched.setdefault("confidence_score", _default_confidence(source_type, labels))
    enriched.setdefault("objective_evidence", _default_objective_evidence(source_type, labels, enriched))
    enriched.setdefault("controlled_status", _default_controlled_status(source_type, labels, enriched))
    return enriched


def _infer_source_type(node_id: str, labels: list[str], properties: dict[str, Any]) -> str:
    if properties.get("controlled_status") == "demo_only":
        return SourceType.SYNTHETIC.value
    if properties.get("source_artifact") or node_id.startswith(("REQ-REAL-", "CMP-REAL-", "RISK-REAL-")):
        return SourceType.EXTRACTED.value
    if re.match(r"^(TEST|CMP|RISK)-\d{3}$", node_id):
        return SourceType.SYNTHETIC.value
    if any(label in labels for label in ["Evidence", "EvidenceArtifact", "SourceDocument", "TestRun"]):
        return SourceType.CONTROLLED.value
    if node_id.startswith(("REQ-", "TC-", "RC-", "CAPA-", "LOG-", "FWCHANGE-")):
        return SourceType.EXTRACTED.value
    if any(label in labels for label in ["Device", "FirmwareVersion", "SoftwareVersion", "Component"]):
        return SourceType.CONTROLLED.value
    return SourceType.INFERRED.value


def _default_review_status(source_type: str, properties: dict[str, Any]) -> str:
    if source_type == SourceType.SYNTHETIC.value:
        return "draft"
    if properties.get("approval_status") or source_type == SourceType.CONTROLLED.value:
        return "approved"
    return "needs_review"


def _default_confidence(source_type: str, labels: list[str]) -> float:
    if source_type == SourceType.CONTROLLED.value:
        return 0.95
    if source_type == SourceType.EXTRACTED.value:
        return 0.82
    if source_type == SourceType.SYNTHETIC.value:
        return 0.35
    if "Device" in labels:
        return 0.9
    return 0.55


def _default_objective_evidence(source_type: str, labels: list[str], properties: dict[str, Any]) -> bool:
    if source_type == SourceType.SYNTHETIC.value:
        return False
    if any(label in labels for label in ["Evidence", "EvidenceArtifact", "SourceDocument", "TestRun", "TelemetryLog"]):
        return True
    return bool(properties.get("hash") or properties.get("content_hash"))


def _default_controlled_status(source_type: str, labels: list[str], properties: dict[str, Any]) -> str:
    if source_type == SourceType.SYNTHETIC.value:
        return "demo_only"
    if properties.get("controlled_status"):
        return str(properties["controlled_status"])
    if any(label in labels for label in ["Evidence", "EvidenceArtifact", "SourceDocument", "TestRun"]):
        return "approved"
    if source_type == SourceType.CONTROLLED.value:
        return "approved"
    return "needs_review"
