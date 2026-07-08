from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.graph.store import JsonGraphStore, store
from backend.graph.synthetic import add_synthetic_backfill
from backend.graph.m1_artifacts import ingest_structured_m1_artifacts
from backend.graph.schema import SourceType
from backend.graph.text_extractors import ExtractedDocument, extract_documents
from backend.models.schemas import IngestionSummary


def ingest_device_source(
    source: str | Path,
    device_name: str,
    device_id: str | None = None,
    current_firmware: str = "v3.4",
    graph_store: JsonGraphStore = store,
    reset: bool = False,
) -> IngestionSummary:
    if reset:
        graph_store.reset()

    resolved_device_id = device_id or _slug_id("DEV", device_name)
    _create_device_spine(graph_store, resolved_device_id, device_name, current_firmware)

    documents = extract_documents(source)
    with graph_store.batch():
        for index, document in enumerate(documents, start=1):
            _ingest_document(graph_store, resolved_device_id, document, index)

        structured_counts = ingest_structured_m1_artifacts(source, resolved_device_id, current_firmware, graph_store)
        synthetic_counts = add_synthetic_backfill(graph_store, resolved_device_id, current_firmware)
    counts = graph_store.counts()
    orphans = graph_store.validate_no_orphans()
    return IngestionSummary(
        device_id=resolved_device_id,
        device_name=device_name,
        real_documents_ingested=len(documents),
        structured_artifacts_ingested=structured_counts["artifacts"],
        structured_requirements_added=structured_counts["requirements"],
        structured_tests_added=structured_counts["tests"],
        structured_test_runs_added=structured_counts["test_runs"],
        structured_risks_added=structured_counts["risks"],
        structured_complaints_added=structured_counts["complaints"],
        structured_capas_added=structured_counts["capas"],
        structured_evidence_added=structured_counts["evidence"],
        synthetic_requirements_added=synthetic_counts["requirements"],
        synthetic_tests_added=synthetic_counts["tests"],
        synthetic_risks_added=synthetic_counts["risks"],
        synthetic_complaints_added=synthetic_counts["complaints"],
        synthetic_capas_added=synthetic_counts["capas"],
        nodes_total=counts["nodes"],
        edges_total=counts["edges"],
        orphan_count=len(orphans),
    )


def _create_device_spine(store: JsonGraphStore, device_id: str, device_name: str, current_firmware: str) -> None:
    store.upsert_node(
        device_id,
        ["Device"],
        name=device_name,
        model=device_name.replace(" ", "-").upper(),
        device_class="Class II",
        status="Demo Active",
        current_firmware=current_firmware,
        nomenclature_code="Pulse oximeter / generic device",
    )
    versions = [
        ("FW-v2.1", "v2.1", "Baseline release used by stale evidence"),
        (f"FW-{current_firmware}", current_firmware, "Current release under audit"),
        ("FW-v3.5", "v3.5", "What-if future firmware release"),
    ]
    for fw_id, version, summary in versions:
        store.upsert_node(fw_id, ["SoftwareVersion", "FirmwareVersion"], version=version, release_date="2026-06-01", change_summary=summary)
        store.upsert_edge(device_id, fw_id, "HAS_VERSION", rationale="Device firmware history", current=version == current_firmware)
    store.upsert_edge("FW-v3.5", f"FW-{current_firmware}", "SUPERSEDES", rationale="Temporal versioning")


def _ingest_document(store: JsonGraphStore, device_id: str, document: ExtractedDocument, index: int) -> None:
    if _is_planning_document(document):
        return

    doc_hash = hashlib.sha1(f"{document.title}|{document.text[:2000]}".encode("utf-8")).hexdigest()[:12]
    evidence_id = f"EVID-{doc_hash}"
    store.upsert_node(
        evidence_id,
        ["Evidence"],
        title=document.title,
        source_path=document.path,
        extension=document.extension,
        category=document.category,
        content_hash=doc_hash,
        snippet=document.text[:900],
        confidence_score=0.86,
        source_type=SourceType.CONTROLLED.value if document.category in {"evidence", "design_evidence", "verification_evidence", "firmware_change_control"} else SourceType.EXTRACTED.value,
        firmware_version=_infer_firmware(document),
        doc_type=document.category,
        page_number=1,
    )
    store.upsert_edge(device_id, evidence_id, "HAS_DOCUMENT", rationale="Uploaded device evidence")

    if document.category in {"regulatory", "design_evidence"}:
        req_id = f"REQ-REAL-{index:03d}"
        standard = _infer_standard(document)
        store.upsert_node(
            req_id,
            ["Requirement"],
            text=_requirement_text(document),
            standard=standard,
            acceptance_criteria=f"Evidence from {document.title} supports the requirement.",
            status="Active",
            vector_ref_id=evidence_id,
        )
        store.upsert_edge(device_id, req_id, "CONTAINS", rationale="Requirement extracted from uploaded corpus")
        store.upsert_edge(req_id, evidence_id, "SUPPORTED_BY", rationale="Source document backing requirement")

    if document.category == "risk":
        risk_id = f"RISK-REAL-{index:03d}"
        store.upsert_node(
            risk_id,
            ["Risk"],
            hazard=_risk_text(document),
            severity=4,
            probability=3,
            risk_level="High",
            mitigation="Review source evidence and verify updated risk controls.",
            vector_ref_id=evidence_id,
        )
        store.upsert_edge(device_id, risk_id, "HAS_RISK", rationale="Risk evidence from uploaded corpus")
        store.upsert_edge(risk_id, evidence_id, "SUPPORTED_BY", rationale="Risk source document")

    if document.category == "complaint":
        complaint_id = f"CMP-REAL-{index:03d}"
        store.upsert_node(
            complaint_id,
            ["Complaint"],
            description=_complaint_text(document),
            severity="High" if "recall" in document.path.lower() else "Medium",
            status="Open" if "maude" in document.path.lower() else "Closed",
            date="2026-06-11",
            vector_ref_id=evidence_id,
        )
        store.upsert_edge(complaint_id, device_id, "REPORTED_ON", rationale="Complaint or post-market record from upload")
        store.upsert_edge(complaint_id, evidence_id, "SUPPORTED_BY", rationale="Complaint source document")

    if document.category == "cybersecurity":
        sbom_id = f"SBOM-REAL-{index:03d}"
        store.upsert_node(
            sbom_id,
            ["SBOM_Component"],
            name=document.title,
            package_type="software/cybersecurity evidence",
            version="unknown",
            vector_ref_id=evidence_id,
        )
        store.upsert_edge(sbom_id, device_id, "AFFECTS_COMPONENT_IN", rationale="Cybersecurity artifact from upload")
        store.upsert_edge(sbom_id, evidence_id, "SUPPORTED_BY", rationale="Cybersecurity source document")


def _infer_standard(document: ExtractedDocument) -> str:
    text = f"{document.path} {document.text}".lower()
    if "80601" in text:
        return "ISO 80601-2-61"
    if "14971" in text:
        return "ISO 14971"
    if "21 cfr" in text or "820" in text:
        return "21 CFR Part 820"
    if "imdrf" in text:
        return "IMDRF"
    if "mdr" in text:
        return "EU MDR"
    return "Device design control"


def _requirement_text(document: ExtractedDocument) -> str:
    return _first_sentence(document.text) or f"Requirement evidence extracted from {document.title}"


def _risk_text(document: ExtractedDocument) -> str:
    return _first_sentence(document.text) or f"Risk signal extracted from {document.title}"


def _complaint_text(document: ExtractedDocument) -> str:
    return _first_sentence(document.text) or f"Post-market record extracted from {document.title}"


def _first_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return parts[0][:500]


def _is_planning_document(document: ExtractedDocument) -> bool:
    path = document.path.lower()
    title = document.title.lower()
    return "data_requirements" in path or "data requirements" in title or "execution_blueprint" in path


def _infer_firmware(document: ExtractedDocument) -> str:
    text = f"{document.path} {document.title} {document.text[:600]}"
    match = re.search(r"(?:FW[-_\s]*)?v?(\d+(?:\.\d+)+)", text, flags=re.IGNORECASE)
    return f"v{match.group(1)}" if match else ""


def _slug_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")
    return f"{prefix}-{slug[:40]}"
