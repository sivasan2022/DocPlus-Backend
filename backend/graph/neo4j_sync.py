from __future__ import annotations

import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from backend.graph.store import store
from backend.models.schemas import GraphEdge, GraphNode, GraphPayload

load_dotenv()

logger = logging.getLogger(__name__)


class Neo4jSyncError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        attempts: list[str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.attempts = attempts or []

    def detail(self) -> dict[str, Any]:
        detail = {"status": "error", "error": self.error, "message": self.message, **status()}
        if self.attempts:
            detail["attempts"] = self.attempts
        return detail


def status() -> dict[str, Any]:
    configured = bool(os.getenv("NEO4J_URI") and (os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")) and os.getenv("NEO4J_PASSWORD"))
    return {
        "provider": "neo4j",
        "configured": configured,
        "package_installed": _driver_available(),
        "sync_enabled": os.getenv("MEDTRACE_NEO4J_SYNC", "false").lower() == "true",
        "uri_present": bool(os.getenv("NEO4J_URI")),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
        "role": "Optional production graph mirror for M1 knowledge graph data.",
        "active_local_store": str(store.path),
    }


def sync_graph_to_neo4j(clear_existing: bool = False) -> dict[str, Any]:
    try:
        GraphDatabase = _graph_database()
    except Neo4jSyncError as exc:  # pragma: no cover - optional dependency.
        return {"status": "unavailable", "reason": exc.message, **status()}

    try:
        uri, user, password, database = _neo4j_settings()
    except Neo4jSyncError as exc:
        return {"status": "unconfigured", "reason": exc.message, **status()}

    payload = store.all()
    errors: list[str] = []
    for candidate_database in _database_candidates(database):
        for candidate_uri in _uri_candidates(uri):
            driver = GraphDatabase.driver(candidate_uri, auth=(user, password))
            try:
                _write_payload(driver, candidate_database, payload, clear_existing)
                return {
                    "status": "synced",
                    "nodes": len(payload.nodes),
                    "edges": len(payload.edges),
                    "database_used": candidate_database,
                    "uri_scheme_used": _uri_scheme(candidate_uri),
                    **status(),
                }
            except Exception as exc:
                errors.append(
                    f"{_uri_scheme(candidate_uri)} database={candidate_database}: "
                    f"{exc.__class__.__name__}: {exc}"
                )
            finally:
                driver.close()

    return {
        "status": "connection_failed",
        "reason": "Neo4j sync could not connect or write using the configured URI/database candidates.",
        "attempts": errors,
        "next_steps": [
            "For Aura, confirm NEO4J_URI starts with neo4j+s:// and the instance is running.",
            "For local Neo4j Desktop, use bolt://localhost:7687 and NEO4J_DATABASE=neo4j.",
            "If NEO4J_DATABASE is an Aura instance id, set it to neo4j unless your database has a custom name.",
        ],
        **status(),
    }


def sync_graph_from_neo4j(batch_size: int = 1000) -> dict[str, Any]:
    logger.info("Starting Neo4j Aura synchronization...")
    started = time.perf_counter()
    batch_size = max(1, min(batch_size, 10000))

    GraphDatabase = _graph_database()
    uri, user, password, database = _neo4j_settings()
    attempts: list[str] = []

    for candidate_database in _database_candidates(database):
        for candidate_uri in _uri_candidates(uri):
            driver = GraphDatabase.driver(candidate_uri, auth=(user, password))
            try:
                driver.verify_connectivity()
                logger.info("Connected to Aura")

                payload = _read_payload(driver, candidate_database, batch_size)
                logger.info("Fetched %s nodes", len(payload.nodes))
                logger.info("Fetched %s relationships", len(payload.edges))
                logger.info("Clearing local graph...")
                logger.info("Rebuilding local graph...")
                store.replace_all(payload)

                duration_ms = round((time.perf_counter() - started) * 1000)
                logger.info("Synchronization completed successfully in %s ms", duration_ms)
                return {
                    "status": "success",
                    "source": "neo4j_aura",
                    "nodes_synced": len(payload.nodes),
                    "relationships_synced": len(payload.edges),
                    "duration_ms": duration_ms,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "database_used": candidate_database,
                    "uri_scheme_used": _uri_scheme(candidate_uri),
                    "local_store": str(store.path),
                }
            except Neo4jSyncError:
                raise
            except ValueError as exc:
                raise Neo4jSyncError(422, "malformed_graph", str(exc)) from exc
            except OSError as exc:
                raise Neo4jSyncError(500, "local_graph_update_failed", str(exc)) from exc
            except Exception as exc:
                if _is_auth_error(exc):
                    raise Neo4jSyncError(401, "neo4j_authentication_failed", str(exc)) from exc
                attempts.append(
                    f"{_uri_scheme(candidate_uri)} database={candidate_database}: "
                    f"{exc.__class__.__name__}: {exc}"
                )
            finally:
                driver.close()

    raise _connection_failure(attempts)


def _write_payload(driver: Any, database: str, payload: Any, clear_existing: bool) -> None:
    with driver.session(database=database) as session:
        session.run("CREATE CONSTRAINT medtrace_node_id IF NOT EXISTS FOR (n:MedTraceNode) REQUIRE n.id IS UNIQUE")
        if clear_existing:
            session.run("MATCH (n:MedTraceNode) DETACH DELETE n")
        node_rows = []
        for node in payload.nodes:
            props = dict(node.properties)
            props["medtrace_labels"] = node.labels
            props["medtrace_primary_label"] = node.labels[0] if node.labels else "Node"
            node_rows.append({"id": node.id, "props": props})
        for chunk in _chunks(node_rows, 250):
            session.run(
                """
                UNWIND $rows AS row
                MERGE (n:MedTraceNode {id: row.id})
                SET n += row.props
                """,
                rows=chunk,
            )

        edge_rows_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in payload.edges:
            rel_type = _safe_label(edge.type) or "RELATED_TO"
            props = dict(edge.properties)
            props["medtrace_type"] = edge.type
            edge_rows_by_type[rel_type].append(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "props": props,
                }
            )
        for rel_type, rows in edge_rows_by_type.items():
            for chunk in _chunks(rows, 250):
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (a:MedTraceNode {{id: row.source}})
                    MATCH (b:MedTraceNode {{id: row.target}})
                    MERGE (a)-[r:`{rel_type}`]->(b)
                    SET r += row.props
                    """,
                    rows=chunk,
                )


def _read_payload(driver: Any, database: str, batch_size: int) -> GraphPayload:
    with driver.session(database=database, fetch_size=batch_size) as session:
        return session.execute_read(_read_payload_transaction)


def _read_payload_transaction(tx: Any) -> GraphPayload:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    node_ids_by_element_id: dict[str, str] = {}
    seen_node_ids: set[str] = set()

    node_result = tx.run(
        """
        MATCH (n)
        RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties
        """
    )
    for record in node_result:
        element_id = str(record["element_id"])
        properties = _json_safe_properties(record["properties"] or {})
        node_id = _local_node_id(element_id, properties)
        if node_id in seen_node_ids:
            raise ValueError(f"Duplicate Aura node id cannot be mirrored locally: {node_id}")
        seen_node_ids.add(node_id)
        node_ids_by_element_id[element_id] = node_id
        nodes.append(
            GraphNode(
                id=node_id,
                labels=_local_node_labels(record["labels"] or [], properties),
                properties=properties,
            )
        )

    relationship_result = tx.run(
        """
        MATCH (source)-[relationship]->(target)
        RETURN
          elementId(relationship) AS element_id,
          elementId(source) AS source_element_id,
          elementId(target) AS target_element_id,
          type(relationship) AS type,
          properties(relationship) AS properties
        """
    )
    for record in relationship_result:
        source = node_ids_by_element_id.get(str(record["source_element_id"]))
        target = node_ids_by_element_id.get(str(record["target_element_id"]))
        if not source or not target:
            raise ValueError(
                "Aura relationship references a node that was not returned by the node scan: "
                f"{record['element_id']}"
            )
        properties = _json_safe_properties(record["properties"] or {})
        edge_type = str(properties.get("medtrace_type") or record["type"] or "").strip()
        if not edge_type:
            raise ValueError(f"Aura relationship has an empty type: {record['element_id']}")
        edges.append(GraphEdge(source=source, target=target, type=edge_type, properties=properties))

    return GraphPayload(nodes=nodes, edges=edges)


def _graph_database() -> Any:
    try:
        from neo4j import GraphDatabase

        return GraphDatabase
    except Exception as exc:  # pragma: no cover - optional dependency.
        raise Neo4jSyncError(503, "neo4j_driver_unavailable", f"neo4j package is not installed: {exc}") from exc


def _neo4j_settings() -> tuple[str, str, str, str]:
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not (uri and user and password):
        raise Neo4jSyncError(
            400,
            "neo4j_unconfigured",
            "NEO4J_URI, NEO4J_USER/NEO4J_USERNAME, and NEO4J_PASSWORD are required.",
        )
    return uri, user, password, database


def _driver_available() -> bool:
    try:
        import neo4j  # noqa: F401

        return True
    except Exception:
        return False


def _local_node_id(element_id: str, properties: dict[str, Any]) -> str:
    for key in ("id", "external_id", "uid"):
        value = properties.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return element_id


def _local_node_labels(labels: list[Any], properties: dict[str, Any]) -> list[str]:
    medtrace_labels = properties.get("medtrace_labels")
    if isinstance(medtrace_labels, list) and medtrace_labels:
        return _dedupe_strings(medtrace_labels)
    return _dedupe_strings(labels)


def _dedupe_strings(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _json_safe_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe_value(value) for key, value in properties.items()}


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "srid") and hasattr(value, "coordinates"):
        return {"srid": value.srid, "coordinates": [_json_safe_value(item) for item in value.coordinates]}
    return str(value)


def _is_auth_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"AuthError", "Unauthorized", "Forbidden"}


def _connection_failure(attempts: list[str]) -> Neo4jSyncError:
    joined_attempts = " | ".join(attempts)
    if "Timeout" in joined_attempts or "timed out" in joined_attempts.lower():
        return Neo4jSyncError(504, "neo4j_connection_timeout", "Timed out connecting to Neo4j Aura.", attempts)
    return Neo4jSyncError(
        503,
        "neo4j_aura_unavailable",
        "Neo4j Aura could not be reached using the configured URI/database candidates.",
        attempts,
    )


def _safe_label(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]", "_", value or "")
    if not value:
        return ""
    if value[0].isdigit():
        value = f"L_{value}"
    return value[:64]


def _uri_candidates(uri: str) -> list[str]:
    candidates = [uri]
    replacements = {
        "neo4j+s://": ["neo4j+ssc://", "bolt+s://", "bolt+ssc://"],
        "neo4j+ssc://": ["bolt+ssc://"],
        "neo4j://": ["neo4j+ssc://", "bolt://", "bolt+ssc://"],
    }
    for prefix, replacement_schemes in replacements.items():
        if uri.startswith(prefix):
            candidates.extend(replacement + uri[len(prefix) :] for replacement in replacement_schemes)
            break
    return list(dict.fromkeys(candidates))


def _database_candidates(database: str) -> list[str]:
    candidates = [database or "neo4j"]
    if database != "neo4j":
        candidates.append("neo4j")
    return list(dict.fromkeys(candidates))


def _uri_scheme(uri: str) -> str:
    return uri.split("://", 1)[0] if "://" in uri else "unknown"


def _chunks(rows: list[dict[str, Any]], size: int):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]
