from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.graph import queries
from backend.graph import neo4j_sync
from backend.graph.ingestion import ingest_device_source
from backend.graph.readiness_score import calculate_readiness_score
from backend.graph.ripple_engine import propagate_firmware_change
from backend.graph.store import store
from backend.models.schemas import FirmwareChangeRequest, GraphPayload, IngestionSummary

router = APIRouter()


@router.post("/upload-device-data", response_model=IngestionSummary)
async def upload_device_data(
    file: UploadFile = File(...),
    device_name: str = Form(...),
    device_id: str | None = Form(default=None),
    current_firmware: str = Form(default="v3.4"),
    reset: bool = Form(default=False),
) -> IngestionSummary:
    suffix = Path(file.filename or "upload.zip").suffix or ".zip"
    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        return ingest_device_source(tmp_path, device_name, device_id, current_firmware, store, reset=reset)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/ingest-local", response_model=IngestionSummary)
def ingest_local_source(
    source_path: str,
    device_name: str,
    device_id: str | None = None,
    current_firmware: str = "v3.4",
    reset: bool = False,
) -> IngestionSummary:
    path = Path(source_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Source path not found: {source_path}")
    return ingest_device_source(path, device_name, device_id, current_firmware, store, reset=reset)


@router.get("/device/{device_id}/twin", response_model=GraphPayload)
def device_twin(device_id: str) -> GraphPayload:
    return store.device_twin(device_id)


@router.get("/device/{device_id}/score")
def device_score(device_id: str):
    return calculate_readiness_score(device_id)


@router.get("/device/{device_id}/kpis")
def device_kpis(device_id: str):
    return queries.graph_kpis(device_id)


@router.get("/node/{node_id}/neighborhood", response_model=GraphPayload)
def node_neighborhood(node_id: str, depth: int = 2) -> GraphPayload:
    return queries.get_neighborhood(node_id, depth)


@router.get("/node/{node_id}/audit-trail")
def node_audit_trail(node_id: str):
    return queries.get_audit_trail(node_id)


@router.get("/device/{device_id}/audit-scope")
def audit_scope(device_id: str):
    return queries.get_audit_scope(device_id)


@router.get("/device/{device_id}/evidence-freshness")
def evidence_freshness(device_id: str):
    return queries.check_evidence_freshness(device_id)


@router.post("/whatif/firmware-change")
def firmware_change(request: FirmwareChangeRequest):
    return propagate_firmware_change(
        device_id=request.device_id,
        new_version=request.new_version,
        changed_components=request.changed_components,
        change_summary=request.change_summary,
    )


@router.get("/complaints/similar")
def similar_complaints(term: str, limit: int = 5):
    return queries.find_similar_complaints(term, limit)


@router.get("/requirements/matrix/{device_id}")
def traceability_matrix(device_id: str):
    return queries.requirements_matrix(device_id)


@router.get("/device/{device_id}/capa-context")
def capa_context(device_id: str, requirement_ids: str | None = None):
    ids = [item.strip() for item in (requirement_ids or "").split(",") if item.strip()]
    return queries.capa_context(device_id, ids or None)


@router.get("/traceability/orphans")
def orphan_nodes():
    return {"orphans": store.validate_no_orphans(), "count": len(store.validate_no_orphans())}


@router.get("/backend-status")
def backend_status():
    return neo4j_sync.status()


@router.post("/sync/neo4j")
def sync_neo4j(clear_existing: bool = False):
    return neo4j_sync.sync_graph_to_neo4j(clear_existing=clear_existing)


@router.post("/sync/from-aura")
def sync_from_aura(batch_size: int = 1000):
    try:
        return neo4j_sync.sync_graph_from_neo4j(batch_size=batch_size)
    except neo4j_sync.Neo4jSyncError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


@router.delete("/reset")
def reset_graph():
    store.reset()
    return {"status": "reset", "path": str(store.path)}
