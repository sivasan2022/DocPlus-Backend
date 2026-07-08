from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.graph.store import store
from backend.m4.report_builder import (
    OUTPUT_DIR,
    PROJECT_NAME,
    build_complaint_report,
    report_summary,
)
from m2_agents.core.state import GraphState
from m2_agents.core.orchestrator import orchestrator
from m2_agents.tools import graph_tools
from m2_agents.tools.vector_tools import status as vector_status

router = APIRouter()


class ComplaintDocumentRequest(BaseModel):
    complaint_text: str
    device_id: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    lot: str | None = None
    regulatory_framework: str = "AUTO"
    include_audit_shadow: bool = True
    include_trace_decay: bool = True
    include_cybersecurity: bool = True
    cybersecurity_force_refresh: bool = False
    cybersecurity_max_components: int | None = Field(default=None, ge=1, le=100)
    cybersecurity_max_cves_per_component: int = Field(default=5, ge=1, le=50)
    new_firmware: str | None = None
    changed_components: list[str] = Field(default_factory=list)


class ComplaintDocumentResponse(BaseModel):
    status: str
    project_name: str
    document_id: str
    filename: str
    output_path: str
    download_url: str
    generated_at: str
    summary: dict[str, Any]
    live_sources: list[str]


@router.get("/health")
def document_health() -> dict[str, Any]:
    counts = store.counts()
    return {
        "status": "ok",
        "project_name": PROJECT_NAME,
        "output_dir": str(OUTPUT_DIR),
        "graph_nodes": counts["nodes"],
        "graph_edges": counts["edges"],
        "vector": vector_status(),
        "capabilities": [
            "live_complaint_pdf",
            "firmware_traceability_ripple_check",
            "cybersecurity_sbom_nvd_narrative",
            "audit_shadow_appendix",
            "trace_decay_appendix",
            "audit_corrective_action_summary",
        ],
    }


@router.get("/contracts")
def integration_contracts() -> dict[str, Any]:
    return {
        "project_name": PROJECT_NAME,
        "m4_owner": "Evidence retrieval and regulatory document generation",
        "endpoints": {
            "POST /documents/complaint-report": "Runs live M2 agents and returns a generated PDF download URL.",
            "POST /documents/complaint-report/pdf": "Runs live M2 agents and returns the PDF file directly.",
            "GET /documents/download/{filename}": "Downloads a generated DocPlus+ PDF.",
            "GET /documents/health": "Reports graph/vector/document generator health.",
        },
        "data_sources": [
            "M1 graph context, device firmware, readiness, and requirement traceability",
            "M2 structured complaint, hypotheses, evidence, risk, CAPA, AuditShadow, and Trace Decay state",
            "M2 device-level Cybersecurity Agent SBOM/NVD scan state",
            "M4 PDF generation, document control, provenance presentation, and output storage",
        ],
        "request_schema": ComplaintDocumentRequest.model_json_schema(),
        "response_schema": ComplaintDocumentResponse.model_json_schema(),
    }


@router.post("/complaint-report", response_model=ComplaintDocumentResponse)
def generate_complaint_report(request: ComplaintDocumentRequest) -> ComplaintDocumentResponse:
    complaint_state, audit_state, trace_state, cybersecurity_state = _run_live_sources(request)
    report = build_complaint_report(complaint_state, audit_state, trace_state, cybersecurity_state)
    sources = ["M2 complaint pipeline", "M2 Firmware Traceability Ripple Check", "M1 graph context", "M4 evidence retrieval"]
    if cybersecurity_state:
        sources.append("M2 Cybersecurity SBOM/NVD")
    if audit_state:
        sources.append("M2 AuditShadow")
    if trace_state:
        sources.append("M2 Trace Decay")
    return ComplaintDocumentResponse(
        status="generated",
        project_name=PROJECT_NAME,
        document_id=report.document_id,
        filename=report.filename,
        output_path=str(report.output_path),
        download_url=f"/documents/download/{report.filename}",
        generated_at=report.generated_at,
        summary=report_summary(complaint_state, audit_state, trace_state, cybersecurity_state),
        live_sources=sources,
    )


@router.post("/complaint-report/pdf")
def generate_complaint_report_pdf(request: ComplaintDocumentRequest) -> FileResponse:
    complaint_state, audit_state, trace_state, cybersecurity_state = _run_live_sources(request)
    report = build_complaint_report(complaint_state, audit_state, trace_state, cybersecurity_state)
    return FileResponse(
        report.output_path,
        media_type="application/pdf",
        filename=report.filename,
        headers={"X-DocPlus-Document-ID": report.document_id},
    )


@router.get("/download/{filename}")
def download_document(filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid PDF filename")
    path = (OUTPUT_DIR / filename).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in path.parents and path != output_root:
        raise HTTPException(status_code=400, detail="Invalid PDF path")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Document not found: {filename}")
    return FileResponse(path, media_type="application/pdf", filename=filename)


def _run_live_sources(request: ComplaintDocumentRequest):
    resolved_device_id = graph_tools.resolve_device_id(request.device_id)

    with ThreadPoolExecutor(max_workers=3) as pool:
        complaint_future = pool.submit(
            orchestrator.run_complaint_pipeline,
            raw_complaint=request.complaint_text,
            device_id=resolved_device_id,
            regulatory_framework=request.regulatory_framework,
            firmware_version=request.firmware_version,
            serial_number=request.serial_number,
            lot=request.lot,
        )
        audit_future = (
            pool.submit(orchestrator.run_audit_shadow, resolved_device_id, request.regulatory_framework)
            if request.include_audit_shadow
            else None
        )
        cybersecurity_future = (
            pool.submit(
                orchestrator.run_cybersecurity_scan,
                resolved_device_id,
                force_refresh=request.cybersecurity_force_refresh,
                max_components=request.cybersecurity_max_components,
                max_cves_per_component=request.cybersecurity_max_cves_per_component,
            )
            if request.include_cybersecurity
            else None
        )

        complaint_state = complaint_future.result()
        audit_state = audit_future.result() if audit_future else None
        cybersecurity_state = cybersecurity_future.result() if cybersecurity_future else None

    scoped_requirement_ids = _scoped_requirement_ids(complaint_state)
    if audit_state and scoped_requirement_ids:
        audit_state.audit_findings = [
            finding
            for finding in audit_state.audit_findings
            if finding.requirement_id in scoped_requirement_ids
        ]

    trace_state = None
    if request.include_trace_decay:
        if request.new_firmware or request.changed_components:
            trace_state = orchestrator.run_trace_decay(
                device_id=resolved_device_id,
                new_firmware=request.new_firmware,
                changed_components=request.changed_components,
            )
            if scoped_requirement_ids:
                trace_state.trace_decay_alerts = _dedupe_alerts(
                    [
                        alert
                        for alert in trace_state.trace_decay_alerts
                        if alert.get("requirement_id") in scoped_requirement_ids
                    ]
                )
        else:
            trace_state = _trace_state_from_complaint_scope(complaint_state)
    return complaint_state, audit_state, trace_state, cybersecurity_state


def _scoped_requirement_ids(state: GraphState) -> set[str]:
    return {
        str(item.get("requirement_id"))
        for item in state.investigation_scope.get("requirements_scope", [])
        if item.get("requirement_id")
    }


def _trace_state_from_complaint_scope(state: GraphState) -> GraphState:
    trace_state = GraphState(device_id=state.device_id)
    alerts: list[dict[str, Any]] = []
    for row in state.investigation_scope.get("verification_scope", []):
        status = str(row.get("trace_decay_status", "")).strip().lower()
        if not status or status == "current and usable":
            continue
        alerts.append(
            {
                "requirement_id": row.get("linked_requirement_id", ""),
                "test_id": row.get("test_case_id", ""),
                "tested_firmware": row.get("tested_firmware", ""),
                "required_firmware": row.get("complaint_firmware", ""),
                "status": row.get("trace_decay_status", ""),
                "reason": row.get("trace_decay_reason", ""),
                "source_type": row.get("source_type", "inferred"),
                "source_types": row.get("source_types", [row.get("source_type", "inferred")]),
                "evidence_class": row.get("evidence_class", ""),
            }
        )
    trace_state.trace_decay_alerts = _dedupe_alerts(alerts)
    trace_state.status = "trace_decay_scoped_from_complaint"
    return trace_state


def _dedupe_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for alert in alerts:
        key = (
            alert.get("requirement_id"),
            alert.get("test_id"),
            alert.get("tested_firmware"),
            alert.get("required_firmware"),
            alert.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(alert)
    return unique
