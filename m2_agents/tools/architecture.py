from __future__ import annotations

from typing import Any

from backend.graph import neo4j_sync
from m2_agents.core.llm import llm
from m2_agents.tools import vector_tools


def architecture_status(langgraph_available: bool | None = None) -> dict[str, Any]:
    return {
        "architecture": "Neo4j + ChromaDB + LangGraph + OpenAI",
        "truth_source": "M1 graph facts remain authoritative; OpenAI only reasons over supplied facts.",
        "m1_graph_layer": neo4j_sync.status(),
        "m4_retrieval_layer": vector_tools.status(),
        "m2_orchestration_layer": {
            "provider": "langgraph",
            "enabled": bool(langgraph_available),
            "role": "StateGraph workflow routes deterministic and AI-assisted agents.",
        },
        "openai_reasoning_layer": llm.status(),
        "fallbacks": {
            "graph": "JSON graph store remains active when Neo4j driver/config is unavailable.",
            "retrieval": "Lexical M1 evidence retrieval remains active when ChromaDB is unavailable.",
            "reasoning": "Deterministic M2 outputs remain active when OpenAI is disabled or unavailable.",
        },
    }
