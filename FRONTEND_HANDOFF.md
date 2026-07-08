# MedTrace AI Frontend Handoff

Date: 2026-07-08  
Workspace: `C:\Users\DELL\Downloads\medTraceAi\medTraceAi`

## 1. Current Frontend Status

There is no active frontend application checked into this workspace right now.

Verified absent:

- No `frontend/` app directory.
- No `package.json`.
- No React/Vite/Next source files.
- No Streamlit `app.py` or `pages/` implementation.

What exists:

- Backend FastAPI service under `backend/`.
- Frontend/M3 blueprint in `M3.md`.
- Product architecture docs in `README.md`, `overall.md`, `day plan.md`, and related milestone files.

So the frontend handoff state is: design and backend contracts exist, but the actual frontend must still be created.

## 2. Current Backend Reality

Backend framework: FastAPI  
Default backend URL: `http://127.0.0.1:8000`  
Swagger docs: `http://127.0.0.1:8000/docs`

The backend exposes three primary route groups:

- `/graph/*` for M1 graph, digital twin, readiness, traceability, Neo4j sync.
- `/agents/*` for M2 complaint, audit, trace decay, cybersecurity, debug, SSE streams.
- `/documents/*` for M4-style complaint report generation and PDF download.

CORS is currently open in `backend/main.py`, so a separate local frontend can call the API without CORS changes.

Current local graph was synced from Neo4j Aura and verified at:

```json
{
  "nodes": 870,
  "edges": 2925
}
```

Neo4j Aura is now the intended source of truth. The local graph is refreshed from Aura using:

```http
POST /graph/sync/from-aura
```

The frontend should continue reading local graph endpoints. It does not need to call Neo4j directly.

## 3. How To Run Backend In VS Code

Run from the repo root:

```powershell
cd C:\Users\DELL\Downloads\medTraceAi\medTraceAi
```

If `.venv` is corrupted, rebuild it:

```powershell
if (Test-Path .venv) {
  Remove-Item -LiteralPath .venv -Recurse -Force
}

C:\Users\DELL\AppData\Local\Programs\Python\Python313\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Do not run plain `uvicorn backend.main:app` from global Python. Use the virtualenv Python explicitly.

## 4. Recommended Frontend Stack

The existing M3 blueprint proposes Streamlit. That is the fastest path if the team wants a Python dashboard demo.

Recommended Streamlit structure:

```text
frontend/
  app.py
  requirements.txt
  .env
  .streamlit/
    config.toml
  assets/
    style.css
    logo.png
  services/
    api_client.py
    stream_handler.py
  state/
    session_manager.py
  components/
    kpi_card.py
    graph_renderer.py
    agent_stepper.py
    alert_panel.py
  pages/
    1_dashboard.py
    2_digital_twin.py
    3_complaint_investigation.py
    4_audit_shadow.py
    5_traceability.py
    6_sbom_cyber.py
```

Suggested frontend `.env`:

```text
BACKEND_URL=http://127.0.0.1:8000
DEFAULT_DEVICE_ID=DEV-PULSE-OX
```

If building a JavaScript frontend instead, use the same API contracts below. No backend route changes are required.

## 5. Primary User Flows

### Dashboard

Purpose: executive overview of device readiness and operational risk.

Use:

- `GET /health`
- `GET /graph/device/{device_id}/score`
- `GET /graph/device/{device_id}/kpis`
- `GET /graph/backend-status`
- `GET /agents/debug/status`
- `GET /documents/health`

Show:

- Graph node/edge counts.
- Audit readiness score.
- KPI cards for complaints, risks, CAPAs, orphan count, stale evidence.
- Neo4j/Aura sync status.
- Vector/OpenAI status from debug status.

### Device Selector

Use:

```http
GET /agents/capabilities
```

The response includes `devices`. Use the first device as the default if the user has not selected one.

### Digital Twin Explorer

Use:

```http
GET /graph/device/{device_id}/twin
GET /graph/node/{node_id}/neighborhood?depth=2
GET /graph/node/{node_id}/audit-trail
```

Expected graph payload:

```json
{
  "nodes": [
    {
      "id": "DEV-PULSE-OX",
      "labels": ["Device"],
      "properties": {
        "name": "Pulse Oximeter"
      }
    }
  ],
  "edges": [
    {
      "source": "DEV-PULSE-OX",
      "target": "REQ-001",
      "type": "CONTAINS",
      "properties": {
        "rationale": "Requirement scope"
      }
    }
  ]
}
```

Frontend mapping:

- Node id: `node.id`
- Node type: first item in `node.labels`
- Node title: `properties.name`, `properties.title`, `properties.requirement`, or fallback to `id`
- Edge label: `edge.type`
- Inspector panel: render `node.properties` as JSON/table.

Important: do not hardcode labels. The graph can contain `Device`, `Requirement`, `Test`, `Evidence`, `Risk`, `Complaint`, `CAPA`, `SoftwareVersion`, `SBOM_Component`, `CVE`, and future labels.

### Traceability

Use:

```http
GET /graph/requirements/matrix/{device_id}
GET /graph/device/{device_id}/audit-scope
GET /graph/device/{device_id}/evidence-freshness
GET /graph/traceability/orphans
```

Show:

- Requirement to test to evidence rows.
- Missing/stale evidence.
- Orphan nodes.
- Open CAPA exposure.

### Complaint Investigation

Non-streaming endpoint:

```http
POST /agents/complaint/investigate
```

Streaming endpoint:

```http
POST /agents/complaint/investigate/stream
```

Request body:

```json
{
  "complaint_text": "Patient reported the pulse oximeter alarm was delayed after firmware update.",
  "device_id": "DEV-PULSE-OX",
  "firmware_version": "v3.4",
  "serial_number": "SN-optional",
  "lot": "LOT-optional",
  "regulatory_framework": "AUTO"
}
```

Use streaming for the UI. The complaint pipeline can take time because it runs multiple agents, graph lookups, memory writes, and optional OpenAI reasoning.

The SSE stream format is:

```text
data: {"agent": "...", "status": "...", "...": "..."}

```

Frontend handling:

- Read each line beginning with `data:`.
- Parse the JSON after `data:`.
- Append events to a run log.
- Update a stepper based on agent/status fields.
- Keep the final state visible after the stream completes.

Suggested stepper:

1. Complaint Intake
2. Root Cause
3. Firmware Traceability Ripple Check
4. Evidence Retrieval
5. Risk Assessment
6. CAPA Draft
7. OpenAI Reasoning, if enabled

Debug UI available from backend:

```http
GET /agents/debug/complaint/view
POST /agents/debug/complaint
```

### Complaint Report / PDF

Use:

```http
POST /documents/complaint-report
POST /documents/complaint-report/pdf
GET /documents/download/{filename}
```

`POST /documents/complaint-report` returns metadata and a download URL.  
`POST /documents/complaint-report/pdf` returns the PDF directly.

### AuditShadow

Non-streaming endpoint:

```http
POST /agents/audit/shadow
```

Streaming endpoint:

```http
POST /agents/audit/shadow/stream
```

Request body:

```json
{
  "device_id": "DEV-PULSE-OX",
  "regulatory_framework": "AUTO"
}
```

Frontend view:

- Terminal-like event stream.
- Findings dashboard.
- Counters for major findings, minor findings, traceability gaps, stale firmware evidence.
- Export affordance can call document/PDF routes if needed.

### Trace Decay / Firmware What-If

Use:

```http
POST /agents/trace/decay-check
POST /graph/whatif/firmware-change
```

Trace decay request:

```json
{
  "device_id": "DEV-PULSE-OX",
  "new_firmware": "v3.5",
  "changed_components": []
}
```

Firmware what-if request:

```json
{
  "device_id": "DEV-PULSE-OX",
  "new_version": "v3.5",
  "changed_components": [],
  "change_summary": "Firmware change impact analysis"
}
```

### Cybersecurity / SBOM

Use:

```http
POST /agents/cybersecurity/sbom-scan
```

Request body:

```json
{
  "device_id": "DEV-PULSE-OX",
  "sbom_path": null,
  "force_refresh": false,
  "max_components": null,
  "max_cves_per_component": 5,
  "delay_seconds": null
}
```

Show:

- CVE counts by severity.
- Affected components.
- Recommended remediation summary.
- Links back to graph nodes when possible.

### Aura Sync Admin Control

Use:

```http
POST /graph/sync/from-aura
```

Response:

```json
{
  "status": "success",
  "source": "neo4j_aura",
  "nodes_synced": 870,
  "relationships_synced": 2925,
  "duration_ms": 4460,
  "timestamp": "2026-07-08T06:08:31.696705+00:00",
  "database_used": "961e1d81",
  "uri_scheme_used": "neo4j+s",
  "local_store": "data\\runtime\\medtrace_graph.json"
}
```

This should be an admin-only button in the frontend, not part of normal user flows.

## 6. API Error Handling Rules

All frontend calls should:

- Use a shared API client.
- Read non-2xx responses and display `detail.message` if available.
- Never show raw Python tracebacks in the UI.
- Use loading states for complaint, audit, report generation, and Aura sync.
- Use streaming endpoints for long-running agent workflows.

Example backend error shape from Aura sync:

```json
{
  "detail": {
    "status": "error",
    "error": "neo4j_aura_unavailable",
    "message": "Neo4j Aura could not be reached using the configured URI/database candidates."
  }
}
```

## 7. Important Performance Notes

Complaint runs are slow by design in the current backend because they execute a full investigation pipeline:

- Graph context lookup.
- Similar complaint lookup.
- Root cause hypotheses.
- Evidence retrieval.
- Risk/RPN calculation.
- CAPA drafting.
- Runtime memory writes to `data/runtime/m2_memory`.
- Optional OpenAI reasoning.

Frontend should not block the whole page during complaint runs. Use:

- SSE stream endpoint.
- Progress stepper.
- Cancel/reset UI affordance.
- Clear "working" state.
- Show partial events as they arrive.

Digital twin rendering should avoid rendering the entire graph if it becomes too dense. Prefer:

- Device twin for first load.
- Node neighborhood for drill-down.
- Filters by label/type/status.
- Depth limit controls.

## 8. Known Issues / Risks

### Virtualenv corruption

The existing `.venv` had corrupted package files, for example:

```text
SyntaxError: source code string cannot contain null bytes
```

This appeared in `faker` and `pydantic` package imports. Rebuild `.venv` if it occurs.

### No committed frontend

The current repository is backend-first. A frontend developer must scaffold the frontend app.

### M3.md encoding

`M3.md` contains some mojibake around icons/Unicode characters. Use it for architecture, but clean visible text before copying into UI.

### Backend uses local JSON graph

Frontend reads the local graph through FastAPI. Neo4j Aura is the source of truth, but the UI should not connect directly to Aura.

### Long workflows

Complaint, audit, cybersecurity, and PDF generation routes can be slow. Treat them as async-feeling flows in the UI even though the backend endpoints are synchronous/SSE.

## 9. Recommended Build Order

1. Scaffold Streamlit or React app.
2. Build shared API client with `BACKEND_URL`.
3. Add health/dashboard page.
4. Add device selector from `/agents/capabilities`.
5. Add digital twin graph from `/graph/device/{device_id}/twin`.
6. Add node inspector and neighborhood drill-down.
7. Add complaint investigation form using SSE.
8. Add AuditShadow stream page.
9. Add traceability matrix and evidence freshness views.
10. Add report generation/download.
11. Add admin Aura sync button.
12. Add polished loading/error/empty states.

## 10. Demo Click Path

1. Open Dashboard.
2. Confirm graph count and readiness score.
3. Open Digital Twin for `DEV-PULSE-OX`.
4. Click a requirement/test/evidence node and show metadata.
5. Run AuditShadow stream and show findings.
6. Run Complaint Investigation stream with a firmware/display/alarm complaint.
7. Generate complaint PDF.
8. Optional admin: run Aura sync and refresh dashboard counts.

## 11. Minimum Frontend Acceptance Criteria

- Starts locally without backend code changes.
- Reads `BACKEND_URL` from env/config.
- Shows backend health and graph counts.
- Renders digital twin graph from real API data.
- Supports device selection without hardcoding a single device.
- Streams complaint investigation events.
- Streams AuditShadow events.
- Generates/downloads complaint report.
- Displays API errors gracefully.
- Does not call Neo4j Aura directly.
- Does not require frontend changes after local graph refresh from Aura.

