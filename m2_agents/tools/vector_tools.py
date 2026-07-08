from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any

from dotenv import load_dotenv

from backend.graph.schema import SourceType, normalize_source_type
from backend.graph.store import store
from m2_agents.core.trace_ai import traced_tool

load_dotenv()


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "device",
    "reported",
    "during",
    "shall",
    "from",
    "into",
    "using",
}


RETRIEVABLE_LABELS = {
    "Evidence",
    "TestRun",
    "Test",
    "Requirement",
    "Risk",
    "RiskControl",
    "Complaint",
    "CAPA",
    "TelemetryLog",
    "FirmwareChange",
}


def retrieve_documents(query: str, device_id: str, top_k: int = 5, firmware_filter: str | None = None) -> list[dict[str, Any]]:
    """M4-compatible retrieval.

    Uses ChromaDB when installed/enabled; otherwise falls back to deterministic
    lexical retrieval over M1 evidence snippets.
    """

    return retrieve_documents_debug(query, device_id, top_k, firmware_filter)["documents"]


@traced_tool("vector_tools.retrieve_documents_debug")
def retrieve_documents_debug(
    query: str,
    device_id: str,
    top_k: int = 5,
    firmware_filter: str | None = None,
) -> dict[str, Any]:
    """Retrieve evidence and return the diagnostics needed for agent inspection."""

    backend = os.getenv("MEDTRACE_VECTOR_BACKEND", "auto").strip().lower()
    debug: dict[str, Any] = {
        "query": query,
        "device_id": device_id,
        "top_k": top_k,
        "firmware_filter": firmware_filter,
        "requested_backend": backend,
        "vector_status": status(),
        "attempts": [],
        "documents": [],
        "selected_backend": "",
    }
    if backend in {"auto", "chroma"}:
        chroma_docs, chroma_attempt = _retrieve_chroma_debug(query, device_id, top_k, firmware_filter)
        debug["attempts"].append(chroma_attempt)
        if chroma_docs or backend == "chroma":
            debug["documents"] = chroma_docs
            debug["selected_backend"] = "chromadb"
            return debug

    lexical_docs = _retrieve_lexical(query, device_id, top_k, firmware_filter)
    debug["attempts"].append(
        {
            "backend": "lexical",
            "status": "completed",
            "query_terms": _terms(query),
            "returned_count": len(lexical_docs),
            "returned_ids": [item.get("id") for item in lexical_docs],
        }
    )
    debug["documents"] = lexical_docs
    debug["selected_backend"] = "lexical"
    return debug


def sync_chroma_from_graph(device_id: str | None = None) -> dict[str, Any]:
    chroma = _chroma()
    if chroma is None:
        return {"status": "unavailable", "reason": "chromadb package is not installed", **status()}

    try:
        collection = _collection(chroma)
    except Exception as exc:
        return {"status": "error", "reason": _sanitize_error(str(exc)), **status()}

    devices = [device_id] if device_id else [node.id for node in store.nodes_by_label("Device")]
    documents: list[str] = []
    ids: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for active_device_id in devices:
        twin = store.device_twin(active_device_id)
        for node in twin.nodes:
            if not _is_retrievable(node):
                continue
            doc = _node_text(node)
            if not doc.strip():
                continue
            ids.append(node.id)
            documents.append(doc)
            metadatas.append(
                {
                    "device_id": active_device_id,
                    "source": str(node.properties.get("source_path", node.properties.get("title", node.id))),
                    "title": str(node.properties.get("title", node.properties.get("name", node.id))),
                    "category": str(node.properties.get("category", node.properties.get("doc_type", _primary_label(node)))),
                    "doc_type": _primary_label(node),
                    "firmware": str(node.properties.get("firmware_version", node.properties.get("firmware_tested", node.properties.get("version", "")))),
                    "page_number": int(node.properties.get("page_number", 1) or 1),
                    "source_type": normalize_source_type(node.properties.get("source_type"), SourceType.INFERRED),
                    "review_status": str(node.properties.get("review_status", "")),
                    "controlled_status": str(node.properties.get("controlled_status", "")),
                    "objective_evidence": bool(node.properties.get("objective_evidence", False)),
                }
            )
    try:
        for start in range(0, len(ids), 64):
            collection.upsert(
                ids=ids[start : start + 64],
                documents=documents[start : start + 64],
                metadatas=metadatas[start : start + 64],
            )
    except Exception as exc:
        return {
            "status": "error",
            "reason": _sanitize_error(str(exc)),
            "documents_prepared": len(ids),
            **status(),
        }
    return {"status": "synced", "backend": "chromadb", "documents": len(ids), **status()}


def status() -> dict[str, Any]:
    backend = os.getenv("MEDTRACE_VECTOR_BACKEND", "auto").strip().lower()
    package_installed = _chroma() is not None
    active = "chromadb" if backend == "chroma" or (backend == "auto" and package_installed) else "lexical"
    embedding_provider = _embedding_provider()
    return {
        "provider": active,
        "requested_backend": backend,
        "chromadb_package_installed": package_installed,
        "persist_path": os.getenv("MEDTRACE_CHROMA_PATH", "data/runtime/chroma"),
        "collection": _collection_name(),
        "embedding_provider": embedding_provider,
        "openai_api_key_present": bool(os.getenv("OPENAI_API_KEY")),
        "openai_embedding_model": os.getenv("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small"),
        "openai_embedding_available": _openai_embedding_available() if embedding_provider == "openai" else False,
        "role": "M4-style evidence retrieval for M2 agents.",
    }


def _retrieve_chroma(query: str, device_id: str, top_k: int, firmware_filter: str | None) -> list[dict[str, Any]]:
    return _retrieve_chroma_debug(query, device_id, top_k, firmware_filter)[0]


def _retrieve_chroma_debug(
    query: str,
    device_id: str,
    top_k: int,
    firmware_filter: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    attempt: dict[str, Any] = {
        "backend": "chromadb",
        "status": "started",
        "embedding_provider": _embedding_provider(),
        "collection": _collection_name(),
        "query": query,
        "device_id": device_id,
        "firmware_filter": firmware_filter,
        "requested_results": top_k,
    }
    chroma = _chroma()
    if chroma is None:
        attempt.update({"status": "unavailable", "reason": "chromadb package is not installed"})
        return [], attempt
    try:
        collection = _collection(chroma)
        if collection.count() == 0:
            sync_result = sync_chroma_from_graph(device_id)
            attempt["sync_result"] = sync_result
        attempt["collection_count"] = collection.count()
        result = collection.query(
            query_texts=[query or "device evidence"],
            n_results=max(1, top_k * 2),
            where={"device_id": device_id},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        attempt.update({"status": "error", "error": _sanitize_error(str(exc))})
        return [], attempt

    rows = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for index, evidence_id in enumerate(ids):
        metadata = metadatas[index] or {}
        node = store.get_node(evidence_id)
        node_props = node.properties if node else {}
        labels = node.labels if node else []
        document = docs[index] or ""
        if firmware_filter and metadata.get("firmware") and metadata["firmware"] != firmware_filter:
            continue
        distance = float(distances[index] if index < len(distances) else 0.5)
        confidence = max(0.45, min(0.97, 1.0 - min(distance, 1.0) * 0.45))
        title = metadata.get("title") or evidence_id
        rows.append(
            {
                "id": evidence_id,
                "source": metadata.get("source", title),
                "title": title,
                "snippet": document[:900],
                "confidence": round(confidence, 2),
                "category": metadata.get("category", "evidence"),
                "doc_type": metadata.get("doc_type", "Evidence"),
                "firmware_version": metadata.get("firmware", ""),
                "source_type": normalize_source_type(metadata.get("source_type") or node_props.get("source_type"), SourceType.INFERRED),
                "review_status": metadata.get("review_status") or node_props.get("review_status", ""),
                "controlled_status": metadata.get("controlled_status") or node_props.get("controlled_status", ""),
                "objective_evidence": bool(metadata.get("objective_evidence") or node_props.get("objective_evidence", False)),
                "labels": labels,
                "source_node_id": evidence_id,
                "page_number": metadata.get("page_number", 1),
                "metadata": metadata,
                "citation": f"[Source: {title}, Confidence: {round(confidence, 2)}]",
                "relevance_score": round(confidence, 2),
                "retrieval_backend": "chromadb",
            }
        )
    selected = rows[:top_k]
    attempt.update(
        {
            "status": "completed",
            "returned_count": len(selected),
            "returned_ids": [item.get("id") for item in selected],
            "raw_match_count": len(rows),
        }
    )
    return selected, attempt


def _retrieve_lexical(query: str, device_id: str, top_k: int = 5, firmware_filter: str | None = None) -> list[dict[str, Any]]:
    """Local fallback: lexical retrieval over M1 evidence snippets."""

    terms = _terms(query)
    twin = store.device_twin(device_id)
    evidence_nodes = [node for node in twin.nodes if _is_retrievable(node)]
    scored = []
    for node in evidence_nodes:
        node_firmware = str(node.properties.get("firmware_version", node.properties.get("firmware_tested", "")))
        if firmware_filter and node_firmware and node_firmware != firmware_filter:
            continue
        haystack = _node_text(node).lower()
        score = sum(1 for term in terms if term in haystack)
        if firmware_filter and firmware_filter.lower() in haystack:
            score += 2
        if score > 0:
            confidence = min(0.95, 0.55 + (score * 0.08))
            title = node.properties.get("title", node.properties.get("name", node.id))
            source = node.properties.get("source_path", node.properties.get("source_artifact", title))
            source_type = normalize_source_type(node.properties.get("source_type"), SourceType.INFERRED)
            scored.append(
                {
                    "id": node.id,
                    "source": source,
                    "title": title,
                    "snippet": _node_text(node)[:900],
                    "confidence": round(confidence, 2),
                    "category": node.properties.get("category", node.properties.get("doc_type", _primary_label(node))),
                    "doc_type": _primary_label(node),
                    "firmware_version": node_firmware,
                    "source_type": source_type,
                    "review_status": node.properties.get("review_status", ""),
                    "controlled_status": node.properties.get("controlled_status", ""),
                    "objective_evidence": bool(node.properties.get("objective_evidence", False)),
                    "labels": node.labels,
                    "source_node_id": node.id,
                    "page_number": node.properties.get("page_number", 1),
                    "metadata": {
                        "device_id": device_id,
                        "source": source,
                        "title": title,
                        "doc_type": _primary_label(node),
                        "firmware": node_firmware,
                        "page_number": node.properties.get("page_number", 1),
                        "source_type": source_type,
                        "review_status": node.properties.get("review_status", ""),
                        "controlled_status": node.properties.get("controlled_status", ""),
                    },
                    "citation": f"[Source: {title}, Confidence: {round(confidence, 2)}]",
                    "relevance_score": round(confidence, 2),
                    "retrieval_backend": "lexical",
                }
            )

    if not scored:
        for node in evidence_nodes[:top_k]:
            source_type = normalize_source_type(node.properties.get("source_type"), SourceType.INFERRED)
            scored.append(
                {
                    "id": node.id,
                    "source": node.properties.get("source_path", node.id),
                    "title": node.properties.get("title", node.id),
                    "snippet": _node_text(node)[:900],
                    "confidence": 0.55,
                    "category": node.properties.get("category", "evidence"),
                    "doc_type": _primary_label(node),
                    "firmware_version": node.properties.get("firmware_version", node.properties.get("firmware_tested", "")),
                    "source_type": source_type,
                    "review_status": node.properties.get("review_status", ""),
                    "controlled_status": node.properties.get("controlled_status", ""),
                    "objective_evidence": bool(node.properties.get("objective_evidence", False)),
                    "labels": node.labels,
                    "source_node_id": node.id,
                    "page_number": node.properties.get("page_number", 1),
                    "citation": f"[Source: {node.properties.get('title', node.id)}, Confidence: 0.55]",
                    "relevance_score": 0.55,
                    "retrieval_backend": "lexical",
                }
            )
    return sorted(scored, key=lambda item: item["confidence"], reverse=True)[:top_k]


def _terms(query: str) -> list[str]:
    return [
        term
        for term in re.findall(r"[a-zA-Z0-9]+", query.lower())
        if len(term) > 2 and term not in STOPWORDS
    ]


def _node_text(node: Any) -> str:
    return "\n".join(
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


def _is_retrievable(node: Any) -> bool:
    return bool(set(node.labels) & RETRIEVABLE_LABELS)


def _primary_label(node: Any) -> str:
    for label in node.labels:
        if label != "MedTraceNode":
            return label
    return node.labels[0] if node.labels else "Evidence"


def _chroma():
    try:
        import chromadb

        return chromadb
    except Exception:
        return None


def _collection(chroma: Any):
    client = chroma.PersistentClient(path=os.getenv("MEDTRACE_CHROMA_PATH", "data/runtime/chroma"))
    return client.get_or_create_collection(
        name=_collection_name(),
        embedding_function=_embedding_function(),
        metadata={
            "description": "MedTrace M1 evidence chunks for M2 agents",
            "embedding_provider": _embedding_provider(),
        },
    )


def _collection_name() -> str:
    configured = os.getenv("MEDTRACE_CHROMA_COLLECTION")
    if configured:
        return configured
    if _embedding_provider() == "openai":
        return "medtrace_evidence_openai"
    return "medtrace_evidence"


def _embedding_provider() -> str:
    provider = (
        os.getenv("MEDTRACE_CHROMA_EMBEDDING_PROVIDER")
        or os.getenv("MEDTRACE_VECTOR_EMBEDDINGS")
        or "hash"
    ).strip().lower()
    if provider not in {"hash", "openai"}:
        return "hash"
    return provider


def _embedding_function():
    if _embedding_provider() == "openai":
        return OpenAIEmbeddingFunction()
    return HashEmbeddingFunction()


def _openai_embedding_available() -> bool:
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        from openai import OpenAI  # noqa: F401

        return True
    except Exception:
        return False


class HashEmbeddingFunction:
    """Small deterministic embedding function so ChromaDB needs no model download."""

    def name(self) -> str:
        return "medtrace-hash-embedding"

    def __call__(self, input: Any) -> list[list[float]]:  # Chroma's protocol uses the name "input".
        return [_hash_embedding(text) for text in _embedding_texts(input)]

    def embed_documents(self, input: Any) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: Any):
        texts = _embedding_texts(input)
        return self(texts)


class OpenAIEmbeddingFunction:
    """Chroma embedding function backed by OpenAI text embeddings."""

    def __init__(self) -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required when MEDTRACE_CHROMA_EMBEDDING_PROVIDER=openai")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - depends on local package install.
            raise RuntimeError(f"openai package is required for OpenAI embeddings: {exc}") from exc
        self.model = os.getenv("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small")
        self.client = OpenAI(timeout=float(os.getenv("MEDTRACE_LLM_TIMEOUT_SECONDS", "25")))

    def name(self) -> str:
        return f"openai-{self.model}"

    def __call__(self, input: Any) -> list[list[float]]:  # Chroma's protocol uses the name "input".
        texts = _embedding_texts(input)
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def embed_documents(self, input: Any) -> list[list[float]]:
        return self(input)

    def embed_query(self, input: Any):
        texts = _embedding_texts(input)
        return self(texts)


def _embedding_texts(input: Any) -> list[str]:
    if isinstance(input, str):
        values = [input]
    else:
        values = list(input or [])
    return [str(text).strip() or "device evidence" for text in values]


def _hash_embedding(text: str, dims: int = 128) -> list[float]:
    vector = [0.0] * dims
    for term in _terms(text):
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _sanitize_error(text: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        text = text.replace(api_key, "[OPENAI_API_KEY]")
    return re.sub(r"sk-[A-Za-z0-9_\-*]+", "sk-REDACTED", text or "")
