from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from backend.graph.store import store
from m2_agents.tools.architecture import architecture_status
from backend.routers import agents, documents, graph

app = FastAPI(
    title="MedTrace AI M1 Knowledge Graph",
    description="No-Docker M1 backend for deterministic device traceability, audit scoring, and ripple analysis.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph.router, prefix="/graph", tags=["Knowledge Graph"])
app.include_router(agents.router, prefix="/agents", tags=["M2 Agents"])
app.include_router(documents.router, prefix="/documents", tags=["M4 DocPlus+ Documents"])


@app.get("/health")
def health():
    counts = store.counts()
    return {
        "status": "ok",
        "store_path": str(store.path),
        "nodes": counts["nodes"],
        "edges": counts["edges"],
        "orphan_count": len(store.validate_no_orphans()),
        "architecture": architecture_status(),
    }
