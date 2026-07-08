from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from m2_agents.core import trace_ai
from m2_agents.core.state import GraphState


@contextmanager
def traced(state: GraphState, agent: str, action: str) -> Iterator[None]:
    start = perf_counter()
    step, tokens = trace_ai.begin_agent_step(state, agent, action)
    state.add_event(agent, action, "started")
    try:
        yield
    except Exception as exc:
        state.add_event(agent, action, "error", error=str(exc))
        trace_ai.end_agent_step(step, "error", error=str(exc))
        raise
    else:
        elapsed_ms = round((perf_counter() - start) * 1000, 2)
        state.add_event(agent, action, "completed", elapsed_ms=elapsed_ms)
        trace_ai.end_agent_step(step, "success")
    finally:
        trace_ai.reset_context(tokens)
