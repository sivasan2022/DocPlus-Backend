# DocPlus Backend

FastAPI backend for DocPlus, an agentic AI compliance intelligence system for MedTech complaint investigation, device traceability, audit readiness, cybersecurity SBOM/CVE review, and audit-ready complaint/CAPA report generation.

The backend exposes graph APIs, multi-agent investigation APIs, document generation APIs, optional Neo4j synchronization, optional ChromaDB vector retrieval, and optional OpenAI reasoning over grounded graph/RAG facts.

## What This Service Does

- Maintains a medical-device compliance knowledge graph across devices, requirements, tests, risks, evidence, complaints, CAPAs, firmware, and SBOM components.
- Runs complaint investigation workflows that classify the complaint, inspect related graph context, retrieve evidence, reason about root cause, assess risk, and draft CAPA content.
- Runs audit shadow checks, trace-decay checks, and cybersecurity SBOM/CVE scans.
- Generates audit-ready complaint/CAPA PDF reports with live source provenance.
- Stores the local graph in JSON for no-Docker demos and optionally syncs to/from Neo4j.
- Uses ChromaDB for local evidence retrieval and can use OpenAI for grounded synthesis when configured.

## Tech Stack

- FastAPI
- Uvicorn
- Pydantic
- LangGraph
- OpenAI Python SDK
- Neo4j Python driver
- ChromaDB
- ReportLab
- pypdf
- python-docx
- openpyxl
- local JSON graph store

## Project Structure

```text
DocPlus-Backend-main/
  requirements.txt
  assets/
    docplus_logo.png
  backend/
    main.py                    # FastAPI app and router registration
    routers/
      agents.py                # Agent workflow, debug, streaming, vector routes
      documents.py             # Complaint/CAPA report generation and downloads
      graph.py                 # Graph ingestion, query, score, sync, reset routes
    graph/
      store.py                 # Local JSON graph store
      ingestion.py             # Device source ingestion
      queries.py               # Graph query helpers
      readiness_score.py       # Audit readiness scoring
      ripple_engine.py         # Firmware impact analysis
      neo4j_sync.py            # Optional Neo4j sync
      schema.py                # Source/evidence metadata helpers
    m4/
      report_builder.py        # DocPlus PDF generation
    models/
      schemas.py               # Shared Pydantic models
  m2_agents/
    agents/                    # Complaint, root-cause, risk, CAPA, audit, cyber agents
    core/                      # Orchestration, LLM wrapper, memory, tracing
    tools/                     # Graph, vector, symptom, evidence, cybersecurity tools
    workflows/                 # Complaint, audit, trace-decay workflows
    configs/                   # Prompts, regulatory rules, symptom references
  data/
    seed/seed_device.py        # CLI graph seeding utility
    source/                    # Source evidence and structured device artifacts
    runtime/                   # Local graph, ChromaDB, logs, memory, generated runtime data
    sbom/                      # SBOM artifacts
  output/pdf/                  # Generated complaint/CAPA PDFs
```

## Prerequisites

- Python 3.11 or newer recommended
- pip
- Optional: OpenAI API key for LLM-assisted reasoning
- Optional: Neo4j Aura or local Neo4j for graph mirroring
- Optional: NVD API key for faster CVE lookups

The service can still run without OpenAI and Neo4j. In that mode it uses deterministic fallback reasoning and the local JSON graph store.

## Quick Start

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
http://127.0.0.1:8000/health
```

## Environment Variables

Create a `.env` file in `DocPlus-Backend-main/` when you need custom settings.

```env
# Core graph store
MEDTRACE_STORE_PATH=data/runtime/medtrace_graph.json

# Default demo device seeding
MEDTRACE_DEFAULT_DEVICE_NAME=Pulse Oximeter
MEDTRACE_DEFAULT_DEVICE_ID=DEV-PULSE-OX
MEDTRACE_CURRENT_FIRMWARE=v3.4
MEDTRACE_DEVICE_SOURCES=data/source/DocPlus_M1_Source_Pack

# Optional OpenAI reasoning
OPENAI_API_KEY=
MEDTRACE_LLM_MODE=hybrid
OPENAI_MODEL_REASONING=gpt-4o
OPENAI_MODEL_FAST=gpt-4o
OPENAI_MODEL_ROOT_CAUSE=gpt-4o-mini
OPENAI_MODEL_SYMPTOM=gpt-4o
OPENAI_MODEL_EMBEDDING=text-embedding-3-small
MEDTRACE_LLM_TIMEOUT_SECONDS=25
MEDTRACE_LLM_MAX_OUTPUT_TOKENS=4000

# Optional vector backend
MEDTRACE_VECTOR_BACKEND=auto
MEDTRACE_CHROMA_PATH=data/runtime/chroma
MEDTRACE_CHROMA_COLLECTION=
MEDTRACE_CHROMA_EMBEDDING_PROVIDER=
MEDTRACE_VECTOR_EMBEDDINGS=

# Optional Neo4j sync
MEDTRACE_NEO4J_SYNC=false
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j

# Optional cybersecurity scanning
MEDTRACE_SBOM_PATH=data/sbom/Pulse_Oximeter_SBOM_Report.xlsx
NVD_API_KEY=
MEDTRACE_NVD_DELAY_SECONDS=6

# Optional tracing
MEDTRACE_TRACE_AI_VERBOSE=false
```

### LLM Modes

- `off` - no OpenAI calls; deterministic fallback reasoning only.
- `assist` - use OpenAI where enabled by workflow code.
- `hybrid` - default when `OPENAI_API_KEY` is present; combines deterministic graph facts with OpenAI synthesis.

The OpenAI layer is advisory. Graph facts, retrieved evidence, and workflow state remain the source of truth.

## Run With Demo Data

The repository includes runtime graph data under:

```text
data/runtime/medtrace_graph.json
```

If you need to rebuild or replace the graph from source documents, use the seeding script:

```powershell
python -m data.seed.seed_device --source "data/source/DocPlus_M1_Source_Pack" --device-name "Pulse Oximeter" --device-id DEV-PULSE-OX --current-firmware v3.4
```

Append without resetting:

```powershell
python -m data.seed.seed_device --source "data/source/13 artifacts" --append
```

You can also ingest through the API:

```text
POST /graph/ingest-local
```

## API Overview

Interactive API docs are available at:

```text
http://127.0.0.1:8000/docs
```

### Core Health

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Backend, graph count, orphan count, architecture status |

### Agent APIs

All paths below are mounted under `/agents`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/agents/capabilities` | Available devices, workflows, agents, dynamic input hints |
| `GET` | `/agents/architecture` | Runtime architecture status |
| `POST` | `/agents/vector/sync` | Sync graph evidence into ChromaDB |
| `GET` | `/agents/debug/status` | Graph, vector, OpenAI, architecture debug status |
| `POST` | `/agents/debug/vector/query` | Query vector evidence retrieval directly |
| `POST` | `/agents/debug/complaint` | Run complaint workflow and return debug payload |
| `GET` | `/agents/debug/complaint/view` | Browser debug console for complaint runs |
| `POST` | `/agents/complaint/investigate` | Run complaint investigation and return full state |
| `POST` | `/agents/complaint/scope` | Return complaint investigation scope for M4/reporting |
| `POST` | `/agents/complaint/investigate/stream` | Stream complaint investigation events as SSE |
| `POST` | `/agents/report/complaint/pdf` | Run complaint workflow and return PDF directly |
| `POST` | `/agents/audit/shadow` | Run mock auditor checks |
| `POST` | `/agents/audit/shadow/stream` | Stream audit shadow events as SSE |
| `POST` | `/agents/trace/decay-check` | Check firmware/component traceability impact |
| `POST` | `/agents/cybersecurity/sbom-scan` | Run SBOM/CVE cybersecurity scan |

### Graph APIs

All paths below are mounted under `/graph`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/graph/upload-device-data` | Upload a device source archive |
| `POST` | `/graph/ingest-local` | Ingest a local source path |
| `GET` | `/graph/device/{device_id}/twin` | Load full device digital twin graph |
| `GET` | `/graph/device/{device_id}/score` | Get audit readiness score |
| `GET` | `/graph/device/{device_id}/kpis` | Get graph KPIs |
| `GET` | `/graph/node/{node_id}/neighborhood` | Load a node neighborhood |
| `GET` | `/graph/node/{node_id}/audit-trail` | Load a node audit trail |
| `GET` | `/graph/device/{device_id}/audit-scope` | Load audit scope for a device |
| `GET` | `/graph/device/{device_id}/evidence-freshness` | Find stale evidence |
| `POST` | `/graph/whatif/firmware-change` | Simulate firmware change impact |
| `GET` | `/graph/complaints/similar` | Find similar complaints |
| `GET` | `/graph/requirements/matrix/{device_id}` | Get traceability matrix |
| `GET` | `/graph/device/{device_id}/capa-context` | Get CAPA context |
| `GET` | `/graph/traceability/orphans` | List orphan graph nodes |
| `GET` | `/graph/backend-status` | Neo4j sync configuration/status |
| `POST` | `/graph/sync/neo4j` | Push local graph to Neo4j |
| `POST` | `/graph/sync/from-aura` | Pull graph from Neo4j/Aura into local store |
| `DELETE` | `/graph/reset` | Reset the local JSON graph store |

### Document APIs

All paths below are mounted under `/documents`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/documents/health` | Document/report generator health |
| `GET` | `/documents/contracts` | Request/response contract details |
| `POST` | `/documents/complaint-report` | Run live workflows and return generated PDF metadata |
| `POST` | `/documents/complaint-report/pdf` | Run live workflows and return PDF file directly |
| `GET` | `/documents/download/{filename}` | Download a generated PDF |

## Example Requests

### Generate Complaint/CAPA Report Metadata

```powershell
$body = @{
  complaint_text = "Screen is fully black and not giving any reading"
  device_id = "DEV-PULSE-OX"
  regulatory_framework = "AUTO"
  include_audit_shadow = $true
  include_trace_decay = $true
  include_cybersecurity = $true
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/documents/complaint-report" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The response includes:

- `status`
- `document_id`
- `filename`
- `output_path`
- `download_url`
- `generated_at`
- `summary`
- `live_sources`

Download the PDF:

```text
http://127.0.0.1:8000/documents/download/{filename}
```

### Run Complaint Investigation Only

```powershell
$body = @{
  complaint_text = "Alarm icon does not appear during low oxygen event"
  device_id = "DEV-PULSE-OX"
  regulatory_framework = "AUTO"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agents/complaint/investigate" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

### Run Firmware Trace Decay Check

```powershell
$body = @{
  device_id = "DEV-PULSE-OX"
  new_firmware = "v3.5"
  changed_components = @("COMP-DISPLAY", "COMP-ALARM")
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agents/trace/decay-check" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

### Sync Graph Evidence Into ChromaDB

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agents/vector/sync" `
  -Method Post
```

### Sync Local Graph To Neo4j

Configure `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE`, then call:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/graph/sync/neo4j?clear_existing=false" `
  -Method Post
```

## Data And Runtime Files

Important local paths:

- `data/runtime/medtrace_graph.json` - local JSON graph store
- `data/runtime/chroma/` - ChromaDB persistent index
- `data/runtime/m2_memory/` - saved workflow memory/state artifacts
- `data/runtime/*.log` - local server/runtime logs
- `data/source/` - source evidence and structured device artifacts
- `data/sbom/` - SBOM inputs
- `output/pdf/` - generated complaint/CAPA PDFs

Do not treat generated runtime files as controlled production records unless your process has reviewed and promoted them.

## Architecture Notes

### Local Graph Store

The backend uses `backend.graph.store.JsonGraphStore` by default. It reads and writes:

```text
data/runtime/medtrace_graph.json
```

Override this with:

```env
MEDTRACE_STORE_PATH=path/to/graph.json
```

### Neo4j

Neo4j is optional and acts as a production graph mirror/sync target. The app can run using only the local JSON graph.

Use:

- `GET /graph/backend-status` to inspect Neo4j configuration.
- `POST /graph/sync/neo4j` to push local graph data to Neo4j.
- `POST /graph/sync/from-aura` to pull graph data from Neo4j into the local JSON store.

### ChromaDB

ChromaDB is used for evidence retrieval. By default it persists to:

```text
data/runtime/chroma
```

Use:

```text
POST /agents/vector/sync
```

to rebuild/sync the vector index from graph evidence.

### OpenAI

OpenAI is optional. If `OPENAI_API_KEY` is missing, the backend uses deterministic fallback logic. If configured, OpenAI is used to synthesize structured explanations over supplied graph, evidence, risk, and CAPA facts.

### Report Generation

The M4 report builder writes PDFs to:

```text
output/pdf
```

The frontend typically calls `POST /documents/complaint-report`, receives a `download_url`, and then downloads the file through `GET /documents/download/{filename}`.

## Development Workflow

1. Start the backend:

   ```powershell
   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
   ```

2. Check runtime health:

   ```text
   http://127.0.0.1:8000/health
   http://127.0.0.1:8000/agents/debug/status
   http://127.0.0.1:8000/documents/health
   ```

3. Start the frontend from `DocPlus-Frontend-main/`:

   ```powershell
   npm install
   npm run dev
   ```

4. Run a complaint from the frontend or call `POST /documents/complaint-report` directly.

## Testing And Verification

There is no dedicated test suite in this snapshot. Use these checks after changes:

```powershell
python -m compileall backend m2_agents data
```

Start the API and check:

```text
GET /health
GET /agents/capabilities
GET /documents/health
GET /graph/device/DEV-PULSE-OX/score
POST /documents/complaint-report
```

For frontend integration, run the frontend and complete a complaint investigation from the UI.

## Troubleshooting

### `ModuleNotFoundError`

Confirm the virtual environment is active and dependencies are installed:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Backend starts but graph is empty

Check:

```text
GET /health
```

If node and edge counts are zero, seed the graph:

```powershell
python -m data.seed.seed_device --source "data/source/DocPlus_M1_Source_Pack"
```

### OpenAI calls are disabled

Check:

```text
GET /agents/debug/status
```

If `api_key_present` is false, add `OPENAI_API_KEY` to `.env`. If you want deterministic mode, set:

```env
MEDTRACE_LLM_MODE=off
```

### Neo4j sync is unavailable

Check:

```text
GET /graph/backend-status
```

Required settings:

- `NEO4J_URI`
- `NEO4J_USER` or `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

For Aura, `NEO4J_URI` usually starts with `neo4j+s://`. For local Neo4j Desktop, use `bolt://localhost:7687`.

### Complaint report generation is slow

`POST /documents/complaint-report` can run complaint investigation, audit shadow, trace decay, cybersecurity scan, vector retrieval, optional OpenAI reasoning, and PDF generation. For faster local demos, send:

```json
{
  "complaint_text": "Screen is black and not giving any reading",
  "include_audit_shadow": false,
  "include_trace_decay": false,
  "include_cybersecurity": false
}
```

### CVE scanning is rate-limited

Use an `NVD_API_KEY` or increase `MEDTRACE_NVD_DELAY_SECONDS`. You can also limit scan size with request fields:

- `cybersecurity_max_components`
- `cybersecurity_max_cves_per_component`

## Frontend Contract

The current frontend expects these backend routes:

- `GET /health`
- `GET /documents/health`
- `GET /agents/capabilities`
- `GET /agents/debug/status`
- `GET /graph/backend-status`
- `GET /graph/device/{device_id}/twin`
- `GET /graph/device/{device_id}/score`
- `GET /graph/device/{device_id}/kpis`
- `GET /graph/device/{device_id}/evidence-freshness`
- `POST /documents/complaint-report`
- `GET /documents/download/{filename}`

Keep these stable when changing backend behavior.
