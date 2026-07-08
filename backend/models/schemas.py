from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    labels: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPayload(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class FirmwareChangeRequest(BaseModel):
    device_id: str
    new_version: str
    changed_components: list[str] = Field(default_factory=list)
    change_summary: str = "Firmware change impact analysis"


class IngestionSummary(BaseModel):
    device_id: str
    device_name: str
    real_documents_ingested: int
    structured_artifacts_ingested: int = 0
    structured_requirements_added: int = 0
    structured_tests_added: int = 0
    structured_test_runs_added: int = 0
    structured_risks_added: int = 0
    structured_complaints_added: int = 0
    structured_capas_added: int = 0
    structured_evidence_added: int = 0
    synthetic_requirements_added: int
    synthetic_tests_added: int
    synthetic_risks_added: int
    synthetic_complaints_added: int
    synthetic_capas_added: int
    nodes_total: int
    edges_total: int
    orphan_count: int
