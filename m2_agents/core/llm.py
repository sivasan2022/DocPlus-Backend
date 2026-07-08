from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from dotenv import load_dotenv

from m2_agents.core import trace_ai

load_dotenv()


@dataclass(frozen=True)
class LlmSettings:
    mode: str
    reasoning_model: str
    fast_model: str
    timeout_seconds: float
    max_output_tokens: int


def get_llm_settings() -> LlmSettings:
    key_present = bool(os.getenv("OPENAI_API_KEY"))
    requested_mode = os.getenv("MEDTRACE_LLM_MODE")
    mode = (requested_mode or ("hybrid" if key_present else "off")).strip().lower()
    if mode not in {"off", "assist", "hybrid"}:
        mode = "off"
    return LlmSettings(
        mode=mode,
        reasoning_model=os.getenv("OPENAI_MODEL_REASONING", "gpt-4o"),
        fast_model=os.getenv("OPENAI_MODEL_FAST", os.getenv("OPENAI_MODEL_REASONING", "gpt-4o")),
        timeout_seconds=float(os.getenv("MEDTRACE_LLM_TIMEOUT_SECONDS", "25")),
        max_output_tokens=int(os.getenv("MEDTRACE_LLM_MAX_OUTPUT_TOKENS", "4000")),
    )


class OpenAIReasoningLayer:
    """Optional OpenAI layer that explains already-grounded graph/RAG results.

    The model is never treated as the source of graph truth. It receives compact,
    deterministic state from M1/M2 and returns strictly advisory JSON.
    """

    def __init__(self) -> None:
        self.settings = get_llm_settings()
        self._client: Any | None = None
        self._import_error: str | None = None
        if self.settings.mode != "off" and os.getenv("OPENAI_API_KEY"):
            try:
                from openai import OpenAI

                self._client = OpenAI(timeout=self.settings.timeout_seconds)
            except Exception as exc:  # pragma: no cover - depends on local SDK/env.
                self._import_error = str(exc)

    def enabled(self) -> bool:
        return self.settings.mode != "off" and self._client is not None

    def status(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "mode": self.settings.mode,
            "enabled": self.enabled(),
            "api_key_present": bool(os.getenv("OPENAI_API_KEY")),
            "reasoning_model": self.settings.reasoning_model,
            "fast_model": self.settings.fast_model,
            "last_error": self._import_error,
            "role": "Final reasoning and synthesis over deterministic M1 graph facts and M4 retrieval evidence.",
        }

    def final_complaint_reasoning(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "executive_summary": {"type": "string"},
                "root_cause_reasoning": {"type": "array", "items": {"type": "string"}},
                "capa_quality_notes": {"type": "array", "items": {"type": "string"}},
                "member_handoff": {
                    "type": "object",
                    "properties": {
                        "M1": {"type": "string"},
                        "M2": {"type": "string"},
                        "M3": {"type": "string"},
                        "M4": {"type": "string"},
                        "M5": {"type": "string"},
                    },
                    "required": ["M1", "M2", "M3", "M4", "M5"],
                    "additionalProperties": False,
                },
                "confidence": {"type": "number"},
            },
            "required": ["executive_summary", "root_cause_reasoning", "capa_quality_notes", "member_handoff", "confidence"],
            "additionalProperties": False,
        }
        return self._json_task(
            task="complaint_final_reasoning",
            system=(
                "You are MedTrace AI's final reasoning reviewer. Use only the supplied graph, "
                "retrieval, risk, and CAPA facts. Do not invent device facts, IDs, evidence, "
                "regulatory clauses, tests, failures, or CAPAs. Return only schema-valid JSON."
            ),
            payload=payload,
            schema=schema,
        )

    def final_audit_reasoning(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "audit_summary": {"type": "string"},
                "highest_priority_actions": {"type": "array", "items": {"type": "string"}},
                "submission_risk": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["audit_summary", "highest_priority_actions", "submission_risk", "confidence"],
            "additionalProperties": False,
        }
        return self._json_task(
            task="audit_final_reasoning",
            system=(
                "You are MedTrace AI's audit reviewer. Summarize only the supplied audit findings "
                "and readiness signals. Do not create new findings or regulatory references. Return only schema-valid JSON."
            ),
            payload=payload,
            schema=schema,
        )

    def final_trace_reasoning(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "impact_summary": {"type": "string"},
                "release_gate_recommendation": {"type": "string"},
                "member_handoff": {
                    "type": "object",
                    "properties": {
                        "M1": {"type": "string"},
                        "M2": {"type": "string"},
                        "M3": {"type": "string"},
                        "M4": {"type": "string"},
                        "M5": {"type": "string"},
                    },
                    "required": ["M1", "M2", "M3", "M4", "M5"],
                    "additionalProperties": False,
                },
                "confidence": {"type": "number"},
            },
            "required": ["impact_summary", "release_gate_recommendation", "member_handoff", "confidence"],
            "additionalProperties": False,
        }
        return self._json_task(
            task="trace_decay_final_reasoning",
            system=(
                "You are MedTrace AI's trace decay reviewer. Explain only the supplied firmware "
                "impact results. Do not add affected tests or components that are not present. Return only schema-valid JSON."
            ),
            payload=payload,
            schema=schema,
        )

    def classify_complaint_intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed_component_ids = [
            str(component.get("id"))
            for component in payload.get("allowed_components", [])
            if component.get("id")
        ]
        component_schema: dict[str, Any] = {"type": "string"}
        if allowed_component_ids:
            component_schema["enum"] = allowed_component_ids

        schema = {
            "type": "object",
            "properties": {
                "affected_component_id": component_schema,
                "matched_requirement_ids": {"type": "array", "items": {"type": "string"}},
                "matched_similar_incident_ids": {"type": "array", "items": {"type": "string"}},
                "issue_type": {"type": "string"},
                "use_condition": {"type": "string"},
                "classification_reason": {"type": "string"},
                "classification_confidence": {"type": "number"},
                "should_override_graph": {"type": "boolean"},
            },
            "required": [
                "affected_component_id",
                "matched_requirement_ids",
                "matched_similar_incident_ids",
                "issue_type",
                "use_condition",
                "classification_reason",
                "classification_confidence",
                "should_override_graph",
            ],
            "additionalProperties": False,
        }
        return self._json_task(
            task="complaint_intake_classification",
            system=(
                "You are MedTrace AI's complaint intake classifier for a pulse oximeter. "
                "Select the affected component only from the supplied allowed_components. "
                "Use the complaint text, top graph requirement candidates, and similar complaints. "
                "Prefer measurement/accuracy components for reading-value problems unless the complaint "
                "explicitly says an alarm notification failed. Prefer use-environment reasoning for altitude, "
                "airplane, sunlight, temperature, motion, skin tone, nail polish, or probe placement conditions. "
                "Do not invent IDs. Return only schema-valid JSON."
            ),
            payload=payload,
            schema=schema,
        )

    def classify_symptoms(self, payload: dict[str, Any]) -> dict[str, Any]:
        valid_codes = [
            str(item.get("code"))
            for item in payload.get("valid_symptoms", [])
            if item.get("code")
        ]
        symptom_schema: dict[str, Any] = {"type": "string"}
        if valid_codes:
            symptom_schema["enum"] = valid_codes

        schema = {
            "type": "object",
            "properties": {
                "symptom_codes": {
                    "type": "array",
                    "items": symptom_schema,
                    "minItems": 1,
                    "maxItems": 3,
                },
                "justification": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["symptom_codes", "justification", "confidence"],
            "additionalProperties": False,
        }
        return self._json_task(
            task="symptom_classification",
            system=(
                "You are MedTrace AI's complaint symptom classifier for a pulse oximeter. "
                "Select one or more symptom_codes only from the supplied valid_symptoms list. "
                "Use the complaint text as the authority and use embedding_candidates only as hints. "
                "Choose SYMPTOM_MISSING when a SpO2 value, pulse value, waveform, signal, or clinical reading "
                "is absent, blank, unavailable, not detected, or not displayed. "
                "Choose SYMPTOM_STUCK_READING when a displayed clinical value repeats or stays static. "
                "Choose SYMPTOM_INACCURATE when a value exists but is wrong, offset, drifting, or implausible. "
                "Choose SYMPTOM_UNSPECIFIED when the complaint does not clearly match any supplied controlled category. "
                "Do not invent new symptom codes. Return only schema-valid JSON."
            ),
            payload=payload,
            schema=schema,
            model=os.getenv("OPENAI_MODEL_SYMPTOM", self.settings.fast_model),
        )

    def root_cause_hypotheses(self, payload: dict[str, Any]) -> dict[str, Any]:
        hypothesis_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "affected_component": {"type": "string"},
                "base_probability": {"type": "number"},
                "why_chain": {"type": "array", "items": {"type": "string"}},
                "evidence_for": {"type": "array", "items": {"type": "string"}},
                "evidence_against": {"type": "array", "items": {"type": "string"}},
                "similar_incident_analysis": {"type": "array", "items": {"type": "string"}},
                "probability_rationale": {"type": "string"},
                "supporting_fact_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "title",
                "description",
                "affected_component",
                "base_probability",
                "why_chain",
                "evidence_for",
                "evidence_against",
                "similar_incident_analysis",
                "probability_rationale",
                "supporting_fact_ids",
            ],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": hypothesis_schema,
                    "minItems": 3,
                    "maxItems": 3,
                },
                "ranking_rationale": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": ["hypotheses", "ranking_rationale", "confidence"],
            "additionalProperties": False,
        }
        return self._json_task(
            task="root_cause_hypotheses",
            system=(
                "You are MedTrace AI's Root Cause Agent. Work like a medical-device failure investigator. "
                "Use only the provided complaint, component, graph neighborhood, requirements/tests, evidence items, "
                "and similar incidents. Do not invent test IDs, complaint IDs, evidence, facts, or regulatory claims. "
                "Return exactly 3 concise candidate hypotheses. "
                "Follow this order exactly: 1) generate candidate failure-mode hypotheses specific to the reported "
                "symptoms and affected component; 2) for each hypothesis, list evidence_for and evidence_against from "
                "the provided facts only, explicitly saying when no evidence is currently available to confirm or rule out; "
                "3) cross-check similar_incidents for each hypothesis, referencing specific IDs when present and stating "
                "whether the incident pattern is consistent, inconsistent, or insufficient; 4) then rank the hypotheses "
                "and assign probabilities justified by the evidence balance. Avoid fixed audit categories such as "
                "'traceability integrity gap', 'objective evidence coverage gap', or 'risk-control effectiveness drift' "
                "unless the complaint specifically reports an audit/traceability issue."
            ),
            payload=payload,
            schema=schema,
            model=os.getenv("OPENAI_MODEL_ROOT_CAUSE", "gpt-4o-mini"),
        )

    def _json_task(
        self,
        task: str,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        fallback = _fallback_reasoning(task, payload)
        model_name = model or self.settings.reasoning_model
        if not self.enabled():
            return {
                **fallback,
                "enabled": False,
                "mode": self.settings.mode,
                "task": task,
                "source": "deterministic_fallback",
                "fallback_reason": "OpenAI is disabled or unavailable.",
            }

        compact_payload = _json_safe(payload)
        errors: list[str] = []
        for caller in (self._responses_json, self._chat_json):
            try:
                result = caller(task, system, compact_payload, schema, model_name)
                _validate_required(result, schema, task)
                result.setdefault("enabled", True)
                result.setdefault("mode", self.settings.mode)
                result.setdefault("model", model_name)
                result.setdefault("task", task)
                result.setdefault("source", "openai_structured_output")
                return result
            except Exception as exc:  # pragma: no cover - network/API dependent.
                errors.append(f"{caller.__name__}: {exc}")
        return {
            **fallback,
            "enabled": True,
            "mode": self.settings.mode,
            "model": model_name,
            "task": task,
            "source": "deterministic_fallback",
            "fallback_reason": "OpenAI did not return schema-valid JSON.",
            "error": _sanitize_error("; ".join(errors))[-1200:],
        }

    def _responses_json(
        self,
        task: str,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            response = self._client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"task": task, "facts": payload}, ensure_ascii=True)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": task,
                        "schema": schema,
                        "strict": True,
                    }
                },
                max_output_tokens=self.settings.max_output_tokens,
            )
        except Exception as exc:
            trace_ai.record_llm_call(
                task=task,
                model=model,
                api="responses",
                status="error",
                latency_ms=(perf_counter() - started) * 1000,
                error=_sanitize_error(str(exc)),
            )
            raise
        usage = _usage_metadata(response)
        trace_ai.record_llm_call(
            task=task,
            model=model,
            api="responses",
            status="success",
            latency_ms=(perf_counter() - started) * 1000,
            **usage,
        )
        text = _extract_response_text(response)
        return _parse_json(text)

    def _chat_json(
        self,
        task: str,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": f"{system}\nReturn JSON only matching this schema: {json.dumps(schema)}"},
            {"role": "user", "content": json.dumps({"task": task, "facts": payload}, ensure_ascii=True)},
        ]
        kwargs = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if _uses_max_completion_tokens(model):
            kwargs["max_completion_tokens"] = self.settings.max_output_tokens
        else:
            kwargs["max_tokens"] = self.settings.max_output_tokens
        started = perf_counter()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            text = str(exc)
            if "max_tokens" in text and "max_completion_tokens" in text:
                trace_ai.record_llm_call(
                    task=task,
                    model=model,
                    api="chat.completions",
                    status="retry",
                    latency_ms=(perf_counter() - started) * 1000,
                    error=_sanitize_error(text),
                )
                kwargs.pop("max_tokens", None)
                kwargs["max_completion_tokens"] = self.settings.max_output_tokens
                started = perf_counter()
                response = self._client.chat.completions.create(**kwargs)
            elif "max_completion_tokens" in text and "max_tokens" in text:
                trace_ai.record_llm_call(
                    task=task,
                    model=model,
                    api="chat.completions",
                    status="retry",
                    latency_ms=(perf_counter() - started) * 1000,
                    error=_sanitize_error(text),
                )
                kwargs.pop("max_completion_tokens", None)
                kwargs["max_tokens"] = self.settings.max_output_tokens
                started = perf_counter()
                response = self._client.chat.completions.create(**kwargs)
            else:
                trace_ai.record_llm_call(
                    task=task,
                    model=model,
                    api="chat.completions",
                    status="error",
                    latency_ms=(perf_counter() - started) * 1000,
                    error=_sanitize_error(text),
                )
                raise
        usage = _usage_metadata(response)
        trace_ai.record_llm_call(
            task=task,
            model=model,
            api="chat.completions",
            status="success",
            latency_ms=(perf_counter() - started) * 1000,
            **usage,
        )
        return _parse_json(response.choices[0].message.content or "{}")


def _parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
    return parsed if isinstance(parsed, dict) else {"items": parsed}


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text)
    dumped = response.model_dump() if hasattr(response, "model_dump") else response
    return _find_text(dumped)


def _usage_metadata(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif usage is not None and not isinstance(usage, dict):
        usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    if usage is None and hasattr(response, "model_dump"):
        dumped = response.model_dump()
        usage = dumped.get("usage") if isinstance(dumped, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    try:
        prompt_int = int(prompt) if prompt is not None else None
    except Exception:
        prompt_int = None
    try:
        completion_int = int(completion) if completion is not None else None
    except Exception:
        completion_int = None
    try:
        total_int = int(total) if total is not None else None
    except Exception:
        total_int = None
    if total_int is None and (prompt_int is not None or completion_int is not None):
        total_int = int(prompt_int or 0) + int(completion_int or 0)
    return {
        "prompt_tokens": prompt_int,
        "completion_tokens": completion_int,
        "total_tokens": total_int,
    }


def _find_text(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") in {"output_text", "text"} and isinstance(value.get("text"), str):
            return value["text"]
        for key in ("output_text", "content", "output", "message"):
            found = _find_text(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_text(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_text(item)
            if found:
                return found
    if isinstance(value, str) and value.strip().startswith("{"):
        return value
    return ""


def _validate_required(result: dict[str, Any], schema: dict[str, Any], task: str) -> None:
    missing = [field for field in schema.get("required", []) if field not in result or result[field] in (None, "", [], {})]
    if missing:
        raise ValueError(f"{task} returned JSON missing required field(s): {', '.join(missing)}")


def _uses_max_completion_tokens(model: str) -> bool:
    return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))


def _sanitize_error(text: str) -> str:
    text = re.sub(r"sk-[A-Za-z0-9_\-*]+", "sk-REDACTED", text or "")
    return text.replace(os.getenv("OPENAI_API_KEY", ""), "[OPENAI_API_KEY]") if os.getenv("OPENAI_API_KEY") else text


def _json_safe(value: Any, max_chars: int = 16000) -> Any:
    raw = json.dumps(value, default=str, ensure_ascii=True)
    if len(raw) > max_chars:
        return {
            "_truncated": True,
            "payload_json_prefix": raw[:max_chars],
            "original_chars": len(raw),
        }
    return json.loads(raw)


def _fallback_reasoning(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == "complaint_intake_classification":
        return _fallback_intake_classification(payload)
    if task == "symptom_classification":
        return _fallback_symptom_classification(payload)
    if task == "root_cause_hypotheses":
        return _fallback_root_cause_hypotheses(payload)
    if task == "audit_final_reasoning":
        finding_count = int(payload.get("finding_count") or len(payload.get("findings", [])))
        return {
            "audit_summary": f"AuditShadow found {finding_count} finding(s) for {payload.get('device_id', 'the selected device')}. Review the highest-severity findings before submission.",
            "highest_priority_actions": [
                "Resolve critical stale or missing evidence findings first.",
                "Close or justify open CAPA impact before claiming audit readiness.",
                "Re-run AuditShadow after graph updates to confirm readiness.",
            ],
            "submission_risk": "High" if finding_count else "Low",
            "confidence": 0.72,
        }
    if task == "trace_decay_final_reasoning":
        alert_count = int(payload.get("alerts_count") or len(payload.get("trace_decay_alerts", [])))
        return {
            "impact_summary": f"Trace Decay found {alert_count} potentially stale verification artifact(s) after the proposed change.",
            "release_gate_recommendation": "Hold release until impacted tests are refreshed or equivalence is approved." if alert_count else "No trace-decay release hold is indicated by the current graph.",
            "member_handoff": _default_handoff(),
            "confidence": 0.74,
        }

    complaint = payload.get("structured_complaint") or {}
    risk = payload.get("risk_assessment") or {}
    hypotheses = payload.get("hypotheses") or []
    primary = hypotheses[0] if hypotheses else {}
    return {
        "executive_summary": (
            f"Complaint for {payload.get('device_id', complaint.get('device_id', 'the selected device'))} "
            f"was classified as {complaint.get('severity', 'unknown')} severity affecting "
            f"{complaint.get('affected_component_name') or complaint.get('affected_component') or 'the inferred component'}. "
            f"Current deterministic RPN is {risk.get('rpn', 'unknown')}."
        ),
        "root_cause_reasoning": [
            primary.get("description", "Primary root-cause hypothesis should be investigated against graph evidence."),
            f"The workflow collected {len(payload.get('evidence', []))} evidence item(s) and {len(hypotheses)} hypothesis item(s).",
            "Open CAPA, stale evidence, similar incidents, and readiness gaps are the main graph-grounded drivers.",
        ],
        "capa_quality_notes": [
            "CAPA draft is grounded in M1 graph evidence and M2 risk output.",
            "Verify effectiveness by re-running AuditShadow after corrective action updates.",
            "Do not close CAPA until traceability gaps and stale evidence are resolved or justified.",
        ],
        "member_handoff": _default_handoff(),
        "confidence": 0.76,
    }


def _fallback_symptom_classification(payload: dict[str, Any]) -> dict[str, Any]:
    valid_codes = {
        str(item.get("code"))
        for item in payload.get("valid_symptoms", [])
        if item.get("code")
    }
    text = str(payload.get("complaint_text") or "").lower()
    codes: list[str] = []

    def add(code: str) -> None:
        if code in valid_codes and code not in codes:
            codes.append(code)

    if any(term in text for term in ["black", "blank", "dark", "no screen", "screen off"]):
        add("SYMPTOM_DISPLAY_BLACK")
    if (
        any(term in text for term in ["no ", "not ", "does not", "doesn't", "cannot", "can't", "unable", "missing", "absent", "dashes"])
        and any(term in text for term in ["reading", "readings", "spo2", "pulse", "signal", "waveform", "value", "values", "display", "show"])
    ):
        add("SYMPTOM_MISSING")
    if any(term in text for term in ["same", "repeat", "repeating", "stuck", "frozen", "locked", "does not change", "doesn't change"]):
        add("SYMPTOM_STUCK_READING")
    if any(term in text for term in ["stale", "not update", "not updating", "does not update", "doesn't update", "refresh stopped", "lag"]):
        add("SYMPTOM_NOT_UPDATING")
    if any(term in text for term in ["wrong", "incorrect", "inaccurate", "unreliable", "implausible", "drift", "offset", "too high", "too low"]):
        add("SYMPTOM_INACCURATE")
    if any(term in text for term in ["flicker", "flashing", "garbled", "artifact", "jitters"]):
        add("SYMPTOM_DISPLAY_FLICKER")
    if any(term in text for term in ["screen freeze", "display freeze", "screen frozen", "display frozen", "interface stuck"]):
        add("SYMPTOM_DISPLAY_FREEZE")
    if any(term in text for term in ["alarm", "alert", "notification", "warning"]) and any(term in text for term in ["delay", "delayed", "late", "lag"]):
        add("SYMPTOM_ALARM_DELAY")
    if any(term in text for term in ["shutdown", "shuts down", "power loss", "restart", "battery", "charging"]):
        add("SYMPTOM_POWER_FAILURE")
    if any(term in text for term in ["drop", "dropped", "crack", "broken", "damage", "impact", "liquid"]):
        add("SYMPTOM_PHYSICAL_DAMAGE")

    if not codes:
        embedding_candidates = payload.get("embedding_candidates") or []
        for candidate in embedding_candidates[:1]:
            code = str(candidate.get("code") or "")
            if code in valid_codes:
                add(code)

    return {
        "symptom_codes": codes or (["SYMPTOM_UNSPECIFIED"] if "SYMPTOM_UNSPECIFIED" in valid_codes else [sorted(valid_codes)[0]]),
        "justification": "Deterministic fallback selected symptoms from fixed taxonomy using complaint text and embedding hints.",
        "confidence": 0.62,
    }


def _fallback_intake_classification(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_components = {
        str(component.get("id")): component
        for component in payload.get("allowed_components", [])
        if component.get("id")
    }
    initial_component = payload.get("initial_component") or {}
    initial_id = str(initial_component.get("id") or "")
    requirement_candidates = payload.get("requirement_candidates", [])
    similar_incidents = payload.get("similar_incidents", [])

    if (
        initial_id in allowed_components
        and initial_component.get("match_source") == "requirement_scope"
        and float(initial_component.get("match_score") or 0) >= 3
    ):
        return {
            "affected_component_id": initial_id,
            "matched_requirement_ids": [
                str(initial_component.get("matched_requirement", {}).get("id") or "")
            ],
            "matched_similar_incident_ids": [],
            "issue_type": "requirement-scoped complaint",
            "use_condition": "not specified",
            "classification_reason": "Deterministic requirement matching already selected a graph-backed component.",
            "classification_confidence": 0.86,
            "should_override_graph": False,
        }

    requirement_component_map = {
        str(item.get("requirement_id")): str(item.get("component_id"))
        for item in requirement_candidates
        if item.get("requirement_id") and item.get("component_id")
    }
    component_votes: dict[str, float] = {}
    matched_requirements: dict[str, list[str]] = {}
    matched_incidents: dict[str, list[str]] = {}

    for item in requirement_candidates:
        component_id = str(item.get("component_id") or "")
        if component_id not in allowed_components:
            continue
        source_type = str(item.get("source_type") or "").lower()
        source_weight = {"controlled": 1.4, "extracted": 1.25, "internal": 1.0, "inferred": 0.8, "synthetic": 0.45}.get(
            source_type,
            0.75,
        )
        score = max(float(item.get("score") or 0), 1.0)
        component_votes[component_id] = component_votes.get(component_id, 0.0) + score * source_weight
        matched_requirements.setdefault(component_id, []).append(str(item.get("requirement_id") or ""))

    for incident in similar_incidents:
        incident_text = " ".join(
            str(incident.get(key, ""))
            for key in ["id", "description", "summary", "root_cause", "investigation_scope"]
        ).lower()
        incident_score = max(float(incident.get("score") or 1), 1.0)
        for requirement_id, component_id in requirement_component_map.items():
            if requirement_id and requirement_id.lower() in incident_text and component_id in allowed_components:
                component_votes[component_id] = component_votes.get(component_id, 0.0) + incident_score * 2.5
                matched_incidents.setdefault(component_id, []).append(str(incident.get("id") or ""))

    best_component_id = ""
    if component_votes:
        best_component_id = sorted(component_votes.items(), key=lambda item: (item[1], item[0]), reverse=True)[0][0]
    elif initial_id in allowed_components and initial_component.get("match_terms"):
        best_component_id = initial_id
    elif "COMP-SYSTEM" in allowed_components:
        best_component_id = "COMP-SYSTEM"
    elif allowed_components:
        best_component_id = sorted(allowed_components)[0]

    initial_is_weak = not initial_component.get("match_terms") or float(initial_component.get("match_score") or 0) < 1
    should_override = bool(best_component_id and best_component_id != initial_id and initial_is_weak)
    confidence = 0.72 if should_override else 0.58
    reason = (
        "Graph candidate voting favored component-backed requirements and similar complaints over a weak component-name fallback."
        if should_override
        else "No OpenAI result was available; deterministic graph voting retained the best available component."
    )
    return {
        "affected_component_id": best_component_id,
        "matched_requirement_ids": [item for item in matched_requirements.get(best_component_id, []) if item][:6],
        "matched_similar_incident_ids": [item for item in matched_incidents.get(best_component_id, []) if item][:6],
        "issue_type": _fallback_issue_type(payload.get("complaint_text", "")),
        "use_condition": _fallback_use_condition(payload.get("complaint_text", "")),
        "classification_reason": reason,
        "classification_confidence": confidence,
        "should_override_graph": should_override,
    }


def _fallback_root_cause_hypotheses(payload: dict[str, Any]) -> dict[str, Any]:
    complaint = payload.get("complaint") or {}
    component = payload.get("component") or {}
    component_id = str(component.get("id") or complaint.get("affected_component") or "")
    component_label = str(component.get("name") or component.get("module") or component_id or "affected component")
    complaint_text = str(complaint.get("raw_text") or complaint.get("summary") or "")
    similar_incidents = payload.get("similar_incidents") or []
    requirements = payload.get("requirement_test_context") or []

    seeds: list[dict[str, Any]] = []
    ranked_incidents = sorted(
        (
            {
                **incident,
                "_relevance": _text_relevance(
                    complaint_text,
                    " ".join(
                        str(incident.get(key, ""))
                        for key in ["description", "summary", "root_cause", "investigation_scope"]
                    ),
                ),
            }
            for incident in similar_incidents
        ),
        key=lambda item: (item.get("_relevance", 0), float(item.get("score") or 0), str(item.get("id") or "")),
        reverse=True,
    )
    for incident in [item for item in ranked_incidents if item.get("_relevance", 0) > 0][:3]:
        description = str(incident.get("description") or incident.get("summary") or incident.get("id") or "")
        root_cause = str(incident.get("root_cause") or "")
        if root_cause:
            title = root_cause
            description_text = f"Prior complaint {incident.get('id')} reported a comparable condition: {description}"
        else:
            title = f"Failure mode consistent with prior complaint {incident.get('id')}: {description}"
            description_text = description
        seeds.append(
            {
                "title": _shorten(title, 120),
                "description": _shorten(description_text, 420),
                "evidence_for": [f"Similar incident {incident.get('id')} was retrieved: {description}"],
                "similar": [f"{incident.get('id')}: consistent pattern candidate based on overlapping complaint terms."],
                "fact_ids": [str(incident.get("id") or "")],
                "relevance": float(incident.get("_relevance") or 0) + 3,
            }
        )

    ranked_requirements = sorted(
        (
            {
                **req,
                "_relevance": _text_relevance(
                    complaint_text,
                    " ".join(str(req.get(key, "")) for key in ["id", "text", "result"]),
                ),
            }
            for req in requirements
        ),
        key=lambda item: (item.get("_relevance", 0), str(item.get("id") or item.get("requirement_id") or "")),
        reverse=True,
    )
    for req in [item for item in ranked_requirements if item.get("_relevance", 0) > 0][:5]:
        req_id = str(req.get("id") or req.get("requirement_id") or "")
        req_text = str(req.get("text") or req.get("requirement_text") or "")
        if not req_id and not req_text:
            continue
        seeds.append(
            {
                "title": _shorten(f"{component_label} failure affecting {req_text or req_id}", 120),
                "description": _shorten(
                    f"The reported complaint may arise from a fault mode that prevents the affected area from meeting {req_id}: {req_text}",
                    420,
                ),
                "evidence_for": [f"Graph requirement candidate {req_id} is linked to the affected component/test context."],
                "similar": ["Similar-incident pattern is insufficient unless matching complaints explicitly support this mode."],
                "fact_ids": [req_id],
                "relevance": float(req.get("_relevance") or 0) + 1,
            }
        )

    if not seeds:
        seeds.append(
            {
                "title": _shorten(f"{component_label} complaint-specific functional failure", 120),
                "description": _shorten(
                    f"The reported symptom should be investigated as a functional failure of {component_label}: {complaint_text}",
                    420,
                ),
                "evidence_for": ["Complaint intake selected this affected component from the graph context."],
                "similar": ["No similar incidents were available to confirm or reject this candidate."],
                "fact_ids": [component_id],
                "relevance": 0.5,
            }
        )

    hypotheses = []
    selected = sorted(seeds, key=lambda item: (item.get("relevance", 0), item.get("title", "")), reverse=True)[:3]
    while len(selected) < 3:
        selected.append(
            {
                "title": _shorten(f"{component_label} alternate fault mode requiring engineering review", 120),
                "description": _shorten(
                    f"An alternate fault mode in {component_label} remains possible because the complaint text does not yet identify the returned-unit condition.",
                    420,
                ),
                "evidence_for": ["Complaint text indicates a real-world failure condition but no returned-unit evidence is available yet."],
                "similar": ["Similar-incident evidence is not specific enough to confirm this alternate mode."],
                "fact_ids": [component_id],
                "relevance": 0.1,
            }
        )

    total_weight = sum(max(0.15, float(seed.get("relevance") or 0.1)) for seed in selected[:3]) or 1
    for index, seed in enumerate(selected[:3], start=1):
        weight = max(0.15, float(seed.get("relevance") or 0.1))
        probability = round(weight / total_weight, 2)
        hypotheses.append(
            {
                "title": seed["title"],
                "description": seed["description"],
                "affected_component": component_id,
                "base_probability": probability,
                "why_chain": [
                    f"Complaint symptom: {complaint_text or 'not specified'}",
                    f"Affected graph component: {component_label}",
                    "Candidate was generated from similar-incident or requirement/test context rather than a fixed audit category.",
                ],
                "evidence_for": seed["evidence_for"],
                "evidence_against": [
                    "No retrieved controlled test, returned-unit inspection, or engineering analysis is currently available to confirm or rule out this hypothesis at root-cause stage."
                ],
                "similar_incident_analysis": seed["similar"],
                "probability_rationale": "Probability is a deterministic fallback estimate based on available similar-incident and requirement/test signals; OpenAI reasoning was unavailable.",
                "supporting_fact_ids": [item for item in seed["fact_ids"] if item],
            }
        )
    return {
        "hypotheses": hypotheses,
        "ranking_rationale": [
            "Hypotheses were ranked by available similar-incident support first, then by affected requirement/test context.",
            "The result is a fallback because OpenAI was disabled or unavailable.",
        ],
        "confidence": 0.62,
    }


def _fallback_issue_type(text: str) -> str:
    lowered = (text or "").lower()
    if any(term in lowered for term in ["reading", "readings", "spo2", "pulse", "accuracy", "inaccurate", "high", "low"]):
        return "measurement accuracy or displayed reading complaint"
    if any(term in lowered for term in ["alarm", "notification", "alert"]):
        return "alarm notification complaint"
    if any(term in lowered for term in ["battery", "charge", "power"]):
        return "power or battery complaint"
    if any(term in lowered for term in ["display", "screen", "flicker", "freeze"]):
        return "display or user-interface complaint"
    return "general device complaint"


def _fallback_use_condition(text: str) -> str:
    lowered = (text or "").lower()
    if any(term in lowered for term in ["aeroplane", "airplane", "aircraft", "flight", "altitude", "cabin"]):
        return "air travel or altitude-related use environment"
    if any(term in lowered for term in ["sunlight", "outdoor", "bright"]):
        return "bright-light use environment"
    if any(term in lowered for term in ["temperature", "cold", "hot", "humidity"]):
        return "environmental condition"
    if any(term in lowered for term in ["motion", "moving", "exercise", "walk"]):
        return "motion or handling condition"
    return "not specified"


def _meaningful_tokens(text: str) -> set[str]:
    stop = {
        "after",
        "also",
        "cannot",
        "device",
        "during",
        "from",
        "that",
        "this",
        "when",
        "with",
        "without",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 3 and token not in stop
    }


def _text_relevance(left: str, right: str) -> int:
    left_tokens = _meaningful_tokens(left)
    right_text = str(right or "").lower()
    return sum(1 for token in left_tokens if token in right_text)


def _shorten(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    return normalized[: limit - 3] + "..." if len(normalized) > limit else normalized


def _default_handoff() -> dict[str, str]:
    return {
        "M1": "Keep the knowledge graph, evidence freshness, and traceability matrix current.",
        "M2": "Use this structured reasoning result for agent state, CAPA, audit, and trace-decay handoff.",
        "M3": "Render summary, risk, CAPA, and evidence citations in the dashboard.",
        "M4": "Use retrieved evidence snippets/citations as the grounded RAG context.",
        "M5": "Use the final structured fields for integration, demo scripts, and report export.",
    }


llm = OpenAIReasoningLayer()
