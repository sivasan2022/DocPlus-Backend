from __future__ import annotations

import json
from pathlib import Path

from m2_agents.core.state import GraphState


class JsonMemorySaver:
    def __init__(self, root: str | Path = "data/runtime/m2_memory"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, thread_id: str, step: str, state: GraphState) -> Path:
        safe_step = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in step)
        path = self.root / f"{thread_id}_{safe_step}.json"
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_latest(self, thread_id: str) -> GraphState | None:
        matches = sorted(self.root.glob(f"{thread_id}_*.json"), key=lambda p: p.stat().st_mtime)
        if not matches:
            return None
        return GraphState(**json.loads(matches[-1].read_text(encoding="utf-8")))
