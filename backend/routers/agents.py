from __future__ import annotations

import html
import json
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.graph import neo4j_sync
from backend.graph.store import store
from backend.reporting import complaint_report_pdf
from m2_agents.core.llm import llm
from m2_agents.core.orchestrator import orchestrator
from m2_agents.tools import graph_tools
from m2_agents.tools.architecture import architecture_status
from m2_agents.tools.vector_tools import retrieve_documents_debug, status as vector_status, sync_chroma_from_graph

router = APIRouter()


class ComplaintInvestigationRequest(BaseModel):
    complaint_text: str
    device_id: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    lot: str | None = None
    regulatory_framework: str = "AUTO"


class AuditShadowRequest(BaseModel):
    device_id: str | None = None
    regulatory_framework: str = "AUTO"


class TraceDecayRequest(BaseModel):
    device_id: str | None = None
    new_firmware: str | None = None
    changed_components: list[str] = Field(default_factory=list)


class CybersecurityScanRequest(BaseModel):
    device_id: str | None = None
    sbom_path: str | None = None
    force_refresh: bool = False
    max_components: int | None = Field(default=None, ge=1, le=100)
    max_cves_per_component: int = Field(default=5, ge=1, le=50)
    delay_seconds: float | None = Field(default=None, ge=0, le=30)


class VectorQueryRequest(BaseModel):
    query: str
    device_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    firmware_filter: str | None = None


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    devices = graph_tools.list_devices()
    return {
        "devices": devices,
        "workflows": [
            "complaint_investigation",
            "audit_shadow",
            "trace_decay",
            "cybersecurity_sbom_nvd_scan",
        ],
        "agents": [
            "ComplaintIntakeAgent",
            "RootCauseAgent",
            "FirmwareTraceabilityRippleCheckAgent",
            "EvidenceAgent",
            "RiskAgent",
            "CapaAgent",
            "AuditShadowAgent",
            "TraceDecayAgent",
            "CybersecurityAgent",
        ],
        "dynamic_inputs": {
            "device_id": "optional when exactly one or more graph Device nodes exist; M2 resolves to the first available device if omitted",
            "regulatory_framework": "AUTO, FDA_21_CFR_820, ISO_13485, or EU_MDR",
            "changed_components": "optional; Trace Decay uses graph components for the selected device when omitted",
            "new_firmware": "optional; Trace Decay infers the next version from current_firmware when omitted",
        },
        "architecture": architecture_status(orchestrator.langgraph_available),
    }


@router.get("/architecture")
def architecture() -> dict[str, Any]:
    return architecture_status(orchestrator.langgraph_available)


@router.post("/vector/sync")
def sync_vector_index(device_id: str | None = None) -> dict[str, Any]:
    return sync_chroma_from_graph(device_id)


@router.get("/debug/status")
def debug_status() -> dict[str, Any]:
    return _debug_status_payload()


@router.post("/debug/vector/query")
def debug_vector_query(request: VectorQueryRequest) -> dict[str, Any]:
    device_id = graph_tools.resolve_device_id(request.device_id)
    return retrieve_documents_debug(
        request.query,
        device_id,
        top_k=request.top_k,
        firmware_filter=request.firmware_filter,
    )


@router.post("/debug/complaint")
def debug_complaint(request: ComplaintInvestigationRequest) -> dict[str, Any]:
    state = _run_complaint(request)
    return _complaint_debug_payload(state)


@router.get("/debug/complaint/view", response_class=HTMLResponse)
def debug_complaint_view(
    complaint_text: str = Query(default=""),
    device_id: str | None = None,
    firmware_version: str | None = None,
    serial_number: str | None = None,
    lot: str | None = None,
    regulatory_framework: str = "AUTO",
    include_openai: bool = False,
) -> HTMLResponse:
    request = ComplaintInvestigationRequest(
        complaint_text=complaint_text,
        device_id=device_id,
        firmware_version=firmware_version,
        serial_number=serial_number,
        lot=lot,
        regulatory_framework=regulatory_framework,
    )
    if not complaint_text.strip():
        return HTMLResponse(_render_debug_page(None, _debug_status_payload(), request))
    state = _run_complaint(request, include_openai=include_openai)
    payload = _complaint_debug_payload(state)
    return HTMLResponse(_render_debug_page(payload, payload["runtime"], request))


@router.post("/complaint/investigate")
def investigate_complaint(request: ComplaintInvestigationRequest) -> dict[str, Any]:
    state = _run_complaint(request)
    return state.model_dump()


@router.post("/complaint/scope")
def scope_complaint_for_m4(request: ComplaintInvestigationRequest) -> dict[str, Any]:
    state = orchestrator.run_complaint_pipeline(
        raw_complaint=request.complaint_text,
        device_id=request.device_id,
        regulatory_framework=request.regulatory_framework,
        firmware_version=request.firmware_version,
        serial_number=request.serial_number,
        lot=request.lot,
    )
    return state.investigation_scope


@router.post("/report/complaint/pdf")
def complaint_pdf_report(request: ComplaintInvestigationRequest) -> Response:
    state = orchestrator.run_complaint_pipeline(
        raw_complaint=request.complaint_text,
        device_id=request.device_id,
        regulatory_framework=request.regulatory_framework,
        firmware_version=request.firmware_version,
        serial_number=request.serial_number,
        lot=request.lot,
    )
    pdf = complaint_report_pdf(state)
    filename = f"medtrace_complaint_report_{state.device_id or 'device'}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/complaint/investigate/stream")
def stream_complaint_investigation(request: ComplaintInvestigationRequest) -> StreamingResponse:
    def events():
        for event in orchestrator.stream_complaint_pipeline(
            request.complaint_text,
            request.device_id,
            request.regulatory_framework,
            request.firmware_version,
            request.serial_number,
            request.lot,
        ):
            yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/audit/shadow")
def run_audit_shadow(request: AuditShadowRequest) -> dict[str, Any]:
    state = orchestrator.run_audit_shadow(request.device_id, request.regulatory_framework)
    return state.model_dump()


@router.post("/audit/shadow/stream")
def stream_audit_shadow(request: AuditShadowRequest) -> StreamingResponse:
    def events():
        for event in orchestrator.stream_audit_shadow(request.device_id, request.regulatory_framework):
            yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/trace/decay-check")
def run_trace_decay(request: TraceDecayRequest) -> dict[str, Any]:
    state = orchestrator.run_trace_decay(request.device_id, request.new_firmware, request.changed_components)
    return state.model_dump()


@router.post("/cybersecurity/sbom-scan")
def run_cybersecurity_scan(request: CybersecurityScanRequest) -> dict[str, Any]:
    state = orchestrator.run_cybersecurity_scan(
        device_id=request.device_id,
        sbom_path=request.sbom_path,
        force_refresh=request.force_refresh,
        max_components=request.max_components,
        max_cves_per_component=request.max_cves_per_component,
        delay_seconds=request.delay_seconds,
    )
    return state.model_dump()


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _run_complaint(request: ComplaintInvestigationRequest, include_openai: bool = True):
    return orchestrator.run_complaint_pipeline(
        raw_complaint=request.complaint_text,
        device_id=request.device_id,
        regulatory_framework=request.regulatory_framework,
        firmware_version=request.firmware_version,
        serial_number=request.serial_number,
        lot=request.lot,
        include_openai=include_openai,
    )


def _debug_status_payload() -> dict[str, Any]:
    counts = store.counts()
    return {
        "graph": {
            "agent_graph_source": "M1 local JSON graph store",
            "note": "M2 agents read this local M1 graph store; Neo4j is the configured production mirror/sync target.",
            "store_path": str(store.path),
            "nodes": counts["nodes"],
            "edges": counts["edges"],
            "orphan_count": len(store.validate_no_orphans()),
            "neo4j": neo4j_sync.status(),
        },
        "vector": vector_status(),
        "openai": llm.status(),
        "architecture": architecture_status(orchestrator.langgraph_available),
    }


def _complaint_debug_payload(state) -> dict[str, Any]:
    ordered_agents = [
        "complaint_intake",
        "root_cause",
        "firmware_traceability_ripple_check",
        "evidence",
        "risk",
        "capa",
        "openai_reasoning",
    ]
    steps = []
    for agent_name in ordered_agents:
        if agent_name in state.agent_debug:
            steps.append({"agent": agent_name, **state.agent_debug[agent_name]})
    graph_fetches = []
    vector_retrievals = []
    for step in steps:
        for fetch in step.get("graph_fetches", []):
            graph_fetches.append({"agent": step["agent"], **fetch})
        vector_retrievals.extend(step.get("vector_retrievals", []))
    return {
        "summary": {
            "status": state.status,
            "device_id": state.device_id,
            "regulatory_framework": state.regulatory_label or state.regulatory_framework,
            "structured_complaint": state.structured_complaint.model_dump() if state.structured_complaint else None,
            "hypothesis_count": len(state.hypotheses),
            "evidence_count": len(state.evidence_collected),
            "risk_assessment": state.risk_assessment.model_dump() if state.risk_assessment else None,
            "capa_section_count": len(state.capa_sections),
            "errors": state.errors,
        },
        "runtime": _debug_status_payload(),
        "agent_steps": steps,
        "graph_fetches": graph_fetches,
        "vector_retrievals": vector_retrievals,
        "trace": [event.model_dump() for event in state.trace],
        "full_state": state.model_dump(),
    }


def _render_debug_page(
    payload: dict[str, Any] | None,
    runtime: dict[str, Any],
    request: ComplaintInvestigationRequest,
) -> str:
    status_cards = "".join(
        [
            _status_card("Graph", runtime.get("graph", {})),
            _status_card("Vector", runtime.get("vector", {})),
            _status_card("OpenAI", runtime.get("openai", {})),
        ]
    )
    body = ""
    if payload:
        body = (
            _summary_section(payload.get("summary", {}))
            + _agent_sections(payload.get("agent_steps", []))
            + _details("All Graph Fetches", payload.get("graph_fetches", []), open_section=False)
            + _details("All Vector Retrievals", payload.get("vector_retrievals", []), open_section=False)
            + _details("Trace Events", payload.get("trace", []), open_section=False)
            + "<section class=\"empty\">Full state JSON is available from <code>POST /agents/debug/complaint</code>.</section>"
        )
    else:
        body = "<section class=\"empty\">Enter a complaint and submit to inspect each M2 agent step.</section>"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>MedTrace Agent Debug</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #14253a; background: #f6f8fb; }}
    header {{ background: #102f4a; color: white; padding: 24px 36px; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px 28px 64px; }}
    h1 {{ margin: 0; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 24px 0 12px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; }}
    form {{ display: grid; gap: 12px; background: white; border: 1px solid #ccd7e4; padding: 16px; border-radius: 8px; }}
    textarea, input, select {{ width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #b9c8d8; border-radius: 6px; font: inherit; }}
    input[type="checkbox"] {{ width: auto; padding: 0; margin: 0; }}
    textarea {{ min-height: 86px; }}
    .check {{ display: flex; align-items: center; gap: 8px; }}
    button {{ width: fit-content; padding: 10px 16px; border: 0; border-radius: 6px; background: #0f5f99; color: white; font-weight: 700; cursor: pointer; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; margin: 16px 0; }}
    .card, details, .empty {{ background: white; border: 1px solid #ccd7e4; border-radius: 8px; padding: 14px; }}
    .metric {{ display: flex; justify-content: space-between; gap: 12px; border-top: 1px solid #e5ebf2; padding-top: 7px; margin-top: 7px; }}
    .metric span:first-child {{ font-weight: 700; color: #37516b; }}
    summary {{ cursor: pointer; font-weight: 700; color: #102f4a; }}
    pre {{ overflow: auto; background: #0f1f31; color: #eaf2fb; padding: 12px; border-radius: 6px; font-size: 12px; line-height: 1.4; }}
    .agent {{ margin-bottom: 14px; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #e8f1f8; color: #123b5f; font-size: 12px; margin-left: 8px; }}
    .row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }}
    .small {{ color: #5d7086; font-size: 13px; }}
  </style>
</head>
<body>
  <header>
    <h1>MedTrace Agent Debug Console</h1>
    <div class="small">Inspect individual agent output, Chroma/OpenAI status, vector retrieval, graph nodes, and relationships.</div>
  </header>
  <main>
    {_form(request)}
    <h2>Runtime Status</h2>
    <div class="grid">{status_cards}</div>
    {body}
  </main>
</body>
</html>"""


def _form(request: ComplaintInvestigationRequest) -> str:
    return f"""
<form method="get" action="/agents/debug/complaint/view">
  <label>Complaint text
    <textarea name="complaint_text" placeholder="Example: Battery drains unusually fast and the device shuts down during home monitoring.">{_e(request.complaint_text)}</textarea>
  </label>
  <div class="row">
    <label>Device ID <input name="device_id" value="{_e(request.device_id or '')}" placeholder="optional"></label>
    <label>Firmware <input name="firmware_version" value="{_e(request.firmware_version or '')}" placeholder="optional"></label>
    <label>Serial <input name="serial_number" value="{_e(request.serial_number or '')}" placeholder="optional"></label>
    <label>Lot <input name="lot" value="{_e(request.lot or '')}" placeholder="optional"></label>
  </div>
  <label>Regulatory framework
    <select name="regulatory_framework">
      {_option("AUTO", request.regulatory_framework)}
      {_option("FDA_21_CFR_820", request.regulatory_framework)}
      {_option("ISO_13485", request.regulatory_framework)}
      {_option("EU_MDR", request.regulatory_framework)}
    </select>
  </label>
  <label class="check"><input type="checkbox" name="include_openai" value="true"> Include OpenAI final reasoning</label>
  <button type="submit">Run Agent Debug</button>
</form>"""


def _option(value: str, selected: str) -> str:
    marker = " selected" if value == selected else ""
    return f"<option value=\"{_e(value)}\"{marker}>{_e(value)}</option>"


def _status_card(title: str, data: dict[str, Any]) -> str:
    lines = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"<div class=\"metric\"><span>{_e(str(key))}</span><span>{_e(str(value))}</span></div>")
    return f"<section class=\"card\"><h3>{_e(title)}</h3>{''.join(lines)}</section>"


def _summary_section(summary: dict[str, Any]) -> str:
    structured = summary.get("structured_complaint") or {}
    risk = summary.get("risk_assessment") or {}
    return f"""
<h2>Run Summary</h2>
<section class="card">
  <div class="grid">
    <div><strong>Status</strong><br>{_e(str(summary.get("status", "")))}</div>
    <div><strong>Device</strong><br>{_e(str(summary.get("device_id", "")))}</div>
    <div><strong>Affected Component</strong><br>{_e(str(structured.get("affected_component_name") or structured.get("affected_component") or ""))}</div>
    <div><strong>Severity</strong><br>{_e(str(structured.get("severity", "")))}</div>
    <div><strong>Risk</strong><br>{_e(str(risk.get("risk_level", "")))} / RPN {_e(str(risk.get("rpn", "")))}</div>
    <div><strong>Evidence Items</strong><br>{_e(str(summary.get("evidence_count", 0)))}</div>
  </div>
</section>"""


def _agent_sections(steps: list[dict[str, Any]]) -> str:
    sections = ["<h2>Agent Outputs</h2>"]
    for step in steps:
        title = step.get("agent", "agent").replace("_", " ").title()
        vector_count = len(step.get("vector_retrievals", []))
        graph_count = len(step.get("graph_fetches", []))
        sections.append(
            f"""
<section class="agent card">
  <h3>{_e(title)}<span class="pill">{graph_count} graph fetches</span><span class="pill">{vector_count} vector queries</span></h3>
  {_details("Outcome", step.get("outcome", {}), open_section=True)}
  {_details("Graph Fetches", step.get("graph_fetches", []), open_section=bool(graph_count))}
  {_details("Vector Retrievals", step.get("vector_retrievals", []), open_section=bool(vector_count))}
</section>"""
        )
    return "".join(sections)


def _details(title: str, value: Any, open_section: bool = True) -> str:
    opened = " open" if open_section else ""
    return f"<details{opened}><summary>{_e(title)}</summary>{_json_block(value)}</details>"


def _json_block(value: Any) -> str:
    text = json.dumps(value, indent=2, default=str, ensure_ascii=True)
    if len(text) > 18000:
        text = text[:18000] + "\n... truncated for browser display; use POST /agents/debug/complaint for full JSON ..."
    return f"<pre>{_e(text)}</pre>"


def _e(value: str) -> str:
    return html.escape(value, quote=True)
