from __future__ import annotations

import re
from typing import Iterable

from m2_agents.core.state import GraphState


class BaseAgent:
    name = "base_agent"

    def run(self, state: GraphState) -> GraphState:
        raise NotImplementedError

    def _citation(self, source: str, confidence: float) -> str:
        return f"[Source: {source}, Confidence: {confidence:.2f}]"

    def _keywords(self, text: str) -> list[str]:
        return [token.lower() for token in re.findall(r"[a-zA-Z0-9]+", text) if len(token) > 2]

    def _contains_any(self, text: str, terms: Iterable[str]) -> bool:
        lower = text.lower()
        return any(term.lower() in lower for term in terms)
