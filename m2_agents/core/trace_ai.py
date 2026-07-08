from __future__ import annotations

import inspect
import os
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


_current_state: ContextVar[Any | None] = ContextVar("trace_ai_state", default=None)
_current_agent: ContextVar[str] = ContextVar("trace_ai_agent", default="")
_current_step_id: ContextVar[str] = ContextVar("trace_ai_step_id", default="")


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def ensure_trace_ai(state: Any) -> dict[str, Any]:
    trace = getattr(state, "trace_ai", None)
    if not isinstance(trace, dict):
        trace = {}
        setattr(state, "trace_ai", trace)
    trace.setdefault("steps", [])
    trace.setdefault("llm_calls", [])
    trace.setdefault("tool_calls", [])
    trace.setdefault("summary", {})
    trace.setdefault("verbose_enabled", _verbose_enabled())
    return trace


def begin_agent_step(state: Any, agent: str, action: str) -> tuple[dict[str, Any], tuple[Any, Any, Any]]:
    trace = ensure_trace_ai(state)
    step = {
        "step_id": f"step-{uuid4().hex[:10]}",
        "agent": agent,
        "action": action,
        "status": "started",
        "started_at": utc_now(),
        "ended_at": "",
        "elapsed_ms": 0.0,
        "llm_call_ids": [],
        "tool_call_ids": [],
        "_perf_start": perf_counter(),
    }
    trace["steps"].append(step)
    tokens = (
        _current_state.set(state),
        _current_agent.set(agent),
        _current_step_id.set(step["step_id"]),
    )
    return step, tokens


def end_agent_step(step: dict[str, Any], status: str, error: str | None = None) -> None:
    step["status"] = status
    step["ended_at"] = utc_now()
    step["elapsed_ms"] = round((perf_counter() - float(step.pop("_perf_start", perf_counter()))) * 1000, 2)
    if error:
        step["error"] = _clean_text(error, 240)


def reset_context(tokens: tuple[Any, Any, Any]) -> None:
    state_token, agent_token, step_token = tokens
    _current_step_id.reset(step_token)
    _current_agent.reset(agent_token)
    _current_state.reset(state_token)


def current_state() -> Any | None:
    return _current_state.get()


def current_agent() -> str:
    return _current_agent.get() or "unknown"


def current_step_id() -> str:
    return _current_step_id.get()


def record_llm_call(
    *,
    task: str,
    model: str,
    api: str,
    status: str,
    latency_ms: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    error: str = "",
) -> None:
    state = current_state()
    if state is None:
        return
    trace = ensure_trace_ai(state)
    call_id = f"llm-{uuid4().hex[:10]}"
    item = {
        "call_id": call_id,
        "step_id": current_step_id(),
        "agent": current_agent(),
        "task": _clean_text(task, 80),
        "model": _clean_text(model, 80),
        "api": _clean_text(api, 40),
        "status": status,
        "started_at": utc_now(),
        "latency_ms": round(float(latency_ms), 2),
        "prompt_tokens": _int_or_none(prompt_tokens),
        "completion_tokens": _int_or_none(completion_tokens),
        "total_tokens": _int_or_none(total_tokens),
    }
    if error:
        item["error"] = _clean_text(error, 240)
    trace["llm_calls"].append(item)
    _link_to_step(trace, "llm_call_ids", call_id)


def record_tool_call(
    *,
    tool: str,
    query: str = "",
    status: str,
    latency_ms: float,
    result_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    state = current_state()
    if state is None:
        return
    trace = ensure_trace_ai(state)
    call_id = f"tool-{uuid4().hex[:10]}"
    item = {
        "call_id": call_id,
        "step_id": current_step_id(),
        "agent": current_agent(),
        "tool": _clean_text(tool, 100),
        "query": _clean_text(query, 180),
        "status": status,
        "started_at": utc_now(),
        "latency_ms": round(float(latency_ms), 2),
        "result_count": _int_or_none(result_count),
        "metadata": _clean_metadata(metadata or {}),
    }
    if error:
        item["error"] = _clean_text(error, 240)
    trace["tool_calls"].append(item)
    _link_to_step(trace, "tool_call_ids", call_id)


def traced_tool(tool_name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = tool_name or f"{fn.__module__}.{fn.__name__}"

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if current_state() is None:
                return fn(*args, **kwargs)
            started = perf_counter()
            query = _query_from_call(fn, args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                record_tool_call(
                    tool=name,
                    query=query,
                    status="error",
                    latency_ms=(perf_counter() - started) * 1000,
                    error=str(exc),
                )
                raise
            record_tool_call(
                tool=name,
                query=query,
                status="success",
                latency_ms=(perf_counter() - started) * 1000,
                result_count=_result_count(result),
                metadata=_result_metadata(result),
            )
            return result

        return wrapper

    return decorator


def trace_summary(*states: Any) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    llm_calls: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for state in states:
        if state is None:
            continue
        trace = getattr(state, "trace_ai", {}) or {}
        steps.extend(_public_items(trace.get("steps", [])))
        llm_calls.extend(_public_items(trace.get("llm_calls", [])))
        tool_calls.extend(_public_items(trace.get("tool_calls", [])))

    steps.sort(key=lambda item: str(item.get("started_at", "")))
    llm_calls.sort(key=lambda item: str(item.get("started_at", "")))
    tool_calls.sort(key=lambda item: str(item.get("started_at", "")))

    total_prompt = sum(int(item.get("prompt_tokens") or 0) for item in llm_calls)
    total_completion = sum(int(item.get("completion_tokens") or 0) for item in llm_calls)
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in llm_calls) or total_prompt + total_completion
    per_agent: dict[str, float] = {}
    for step in steps:
        agent = str(step.get("agent") or "unknown")
        per_agent[agent] = round(per_agent.get(agent, 0.0) + float(step.get("elapsed_ms") or 0), 2)
    longest = sorted(per_agent.items(), key=lambda item: item[1], reverse=True)
    wall_clock = _wall_clock_ms(steps)
    return {
        "summary": {
            "total_wall_clock_ms": wall_clock,
            "agent_step_count": len(steps),
            "llm_call_count": len(llm_calls),
            "tool_call_count": len(tool_calls),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "per_agent_elapsed_ms": per_agent,
            "longest_agent": longest[0][0] if longest else "",
            "longest_agent_elapsed_ms": longest[0][1] if longest else 0,
            "telemetry_note": "Trace AI is operational telemetry only; it is not clinical evidence, regulatory evidence, Trace Decay, or firmware freshness analysis.",
        },
        "steps": steps,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
    }


def _link_to_step(trace: dict[str, Any], key: str, call_id: str) -> None:
    step_id = current_step_id()
    if not step_id:
        return
    for step in trace.get("steps", []):
        if step.get("step_id") == step_id:
            step.setdefault(key, []).append(call_id)
            return


def _query_from_call(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
        wanted = []
        for key in (
            "device_id",
            "node_id",
            "component_id",
            "component_name",
            "term",
            "query",
            "query_terms",
            "cpe",
            "max_results",
            "top_k",
            "limit",
            "new_version",
            "changed_components",
        ):
            if key in bound.arguments:
                wanted.append(f"{key}={_clean_text(bound.arguments[key], 80)}")
        return "; ".join(wanted)
    except Exception:
        return ""


def _result_count(result: Any) -> int | None:
    if isinstance(result, list):
        return len(result)
    if isinstance(result, tuple) and result and isinstance(result[0], list):
        return len(result[0])
    if isinstance(result, dict):
        for key in ("documents", "items", "findings", "components", "nodes"):
            if isinstance(result.get(key), list):
                return len(result[key])
        for key in ("returned_count", "finding_count", "node_count", "request_count", "stale_test_count", "open_capa_count"):
            if key in result:
                return _int_or_none(result.get(key))
    if isinstance(result, (int, float)):
        return int(result)
    return None


def _result_metadata(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        metadata: dict[str, Any] = {}
        for key in (
            "selected_backend",
            "requested_backend",
            "vector_status",
            "request_count",
            "finding_count",
            "queried_component_count",
            "stale_test_count",
            "affected_requirement_count",
            "readiness_score",
            "score",
        ):
            if key in result:
                value = result[key]
                metadata[key] = value.get("provider") if key == "vector_status" and isinstance(value, dict) else value
        return metadata
    return {}


def _public_items(items: Any) -> list[dict[str, Any]]:
    public = []
    for item in items or []:
        if isinstance(item, dict):
            public.append({key: value for key, value in item.items() if not str(key).startswith("_")})
    return public


def _wall_clock_ms(steps: list[dict[str, Any]]) -> float:
    if not steps:
        return 0.0
    total = sum(float(step.get("elapsed_ms") or 0) for step in steps)
    return round(total, 2)


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = _clean_text(value, 140) if isinstance(value, str) else value
        elif isinstance(value, list):
            clean[str(key)] = [_clean_text(item, 80) for item in value[:8]]
        elif isinstance(value, dict):
            clean[str(key)] = {str(k): _clean_text(v, 80) for k, v in list(value.items())[:8]}
        else:
            clean[str(key)] = _clean_text(value, 80)
    return clean


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _verbose_enabled() -> bool:
    return os.getenv("MEDTRACE_TRACE_AI_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}
