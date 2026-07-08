from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from m2_agents.core.llm import llm
from m2_agents.tools import vector_tools


REFERENCE_PATH = Path(__file__).resolve().parents[1] / "configs" / "symptom_reference_set.json"


def classify_symptom_text(text: str) -> dict[str, Any]:
    """Classify complaint symptoms with embeddings first, then constrained LLM fallback.

    The clinical severity floor remains outside this module. This classifier only
    decides which fixed symptom categories should be handed to Complaint Intake.
    """

    references = _load_references()
    valid_codes = [item["code"] for item in references] + ["SYMPTOM_UNSPECIFIED"]
    threshold, margin = _thresholds()
    scored, embedding_debug = _score_references(text, references)
    top = scored[0] if scored else {}
    second = scored[1] if len(scored) > 1 else {}
    top_score = float(top.get("score") or 0.0)
    second_score = float(second.get("score") or 0.0)
    score_margin = round(top_score - second_score, 4) if second else top_score
    needs_llm = (
        top_score < threshold
        or (second and score_margin <= margin)
        or _needs_multi_label_review(text, top, second, threshold)
    )

    base = {
        "threshold": threshold,
        "margin": margin,
        "top_score": round(top_score, 4),
        "second_score": round(second_score, 4),
        "score_margin": round(score_margin, 4),
        "top_candidates": scored[:5],
        "reference_path": str(REFERENCE_PATH),
        "reference_version": _reference_version(),
        "embedding": embedding_debug,
    }

    if not needs_llm:
        return {
            **base,
            "path": "embedding_direct",
            "symptom_codes": [top["code"]] if top.get("code") else ["SYMPTOM_UNSPECIFIED"],
            "justification": (
                f"Embedding top match {top.get('code')} exceeded threshold "
                f"{threshold} with margin {round(score_margin, 4)}."
            ),
        }

    llm_result = llm.classify_symptoms(
        {
            "complaint_text": text,
            "valid_symptoms": [
                {"code": item["code"], "description": item.get("description", "")}
                for item in references
            ]
            + [
                {
                    "code": "SYMPTOM_UNSPECIFIED",
                    "description": "No fixed controlled symptom category is clearly applicable; preserve keyword tags only.",
                }
            ],
            "embedding_candidates": scored[:6],
            "threshold": threshold,
            "margin": margin,
            "instructions": [
                "Choose one or more symptom_codes only from valid_symptoms.",
                "Choose SYMPTOM_MISSING when a primary SpO2, pulse, signal, waveform, or reading is absent or not displayed.",
                "Choose SYMPTOM_STUCK_READING when a value repeats or stays static.",
                "Choose SYMPTOM_INACCURATE when a value is present but wrong, offset, drifting, or clinically implausible.",
            ],
        }
    )
    codes = _clean_codes(llm_result.get("symptom_codes"), valid_codes)
    if not codes and top.get("code"):
        codes = [top["code"]]
    return {
        **base,
        "path": "llm_fallback",
        "symptom_codes": codes or ["SYMPTOM_UNSPECIFIED"],
        "justification": llm_result.get("justification", ""),
        "llm": {
            "source": llm_result.get("source", ""),
            "enabled": llm_result.get("enabled", False),
            "mode": llm_result.get("mode", ""),
            "model": llm_result.get("model", ""),
            "confidence": llm_result.get("confidence"),
            "fallback_reason": llm_result.get("fallback_reason", ""),
            "error": llm_result.get("error", ""),
        },
    }


def symptom_reference_taxonomy() -> list[str]:
    return [item["code"] for item in _load_references()]


def symptom_reference_details() -> list[dict[str, Any]]:
    return [{"code": item["code"], "description": item.get("description", "")} for item in _load_references()]


def _load_references() -> list[dict[str, Any]]:
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    categories = data.get("categories", [])
    return [item for item in categories if item.get("code") and item.get("examples")]


def _reference_version() -> str:
    try:
        return str(json.loads(REFERENCE_PATH.read_text(encoding="utf-8")).get("version", ""))
    except Exception:
        return ""


def _thresholds() -> tuple[float, float]:
    provider = _provider_label()
    default_threshold = 0.52 if provider == "openai" else 0.18
    threshold = _float_env("MEDTRACE_SYMPTOM_EMBEDDING_THRESHOLD", default_threshold)
    margin = _float_env("MEDTRACE_SYMPTOM_EMBEDDING_MARGIN", 0.05)
    return threshold, margin


def _needs_multi_label_review(text: str, top: dict[str, Any], second: dict[str, Any], threshold: float) -> bool:
    if not top or not second or float(second.get("score") or 0) < threshold:
        return False
    top_code = str(top.get("code") or "")
    second_code = str(second.get("code") or "")
    text_lower = (text or "").lower()
    mentions_display_loss = any(term in text_lower for term in ["screen", "display", "black", "blank", "dark"])
    mentions_clinical_output = any(
        term in text_lower
        for term in ["reading", "readings", "spo2", "pulse", "signal", "waveform", "value", "values"]
    )
    display_and_measurement = mentions_display_loss and mentions_clinical_output
    codes = {top_code, second_code}
    return display_and_measurement and bool(codes & {"SYMPTOM_DISPLAY_BLACK", "SYMPTOM_DISPLAY_FREEZE"}) and bool(
        codes & {"SYMPTOM_MISSING", "SYMPTOM_INACCURATE", "SYMPTOM_STUCK_READING", "SYMPTOM_NOT_UPDATING"}
    )


def _score_references(text: str, references: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    examples: list[tuple[str, str]] = []
    for item in references:
        for example in item.get("examples", []):
            examples.append((item["code"], str(example)))

    query_vectors, query_debug = _embed_texts([text or "unspecified complaint"])
    reference_vectors, reference_debug = _reference_embeddings(
        _provider_cache_key(query_debug),
        str(REFERENCE_PATH),
        REFERENCE_PATH.stat().st_mtime,
        tuple(example for _, example in examples),
    )
    query_vector = query_vectors[0] if query_vectors else []

    by_code: dict[str, list[dict[str, Any]]] = {}
    for index, (code, example) in enumerate(examples):
        score = _cosine(query_vector, reference_vectors[index]) if index < len(reference_vectors) else 0.0
        by_code.setdefault(code, []).append({"score": score, "example": example})

    description_by_code = {item["code"]: item.get("description", "") for item in references}
    scored = []
    for code, rows in by_code.items():
        ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
        top_rows = ranked[:3]
        scored.append(
            {
                "code": code,
                "description": description_by_code.get(code, ""),
                "score": round(float(ranked[0]["score"] if ranked else 0.0), 4),
                "mean_top3_score": round(sum(float(item["score"]) for item in top_rows) / max(1, len(top_rows)), 4),
                "best_example": ranked[0]["example"] if ranked else "",
            }
        )
    scored.sort(key=lambda item: (item["score"], item["mean_top3_score"], item["code"]), reverse=True)
    return scored, {
        "provider": query_debug.get("provider"),
        "model": query_debug.get("model"),
        "query_embedding_provider": query_debug,
        "reference_embedding_provider": reference_debug,
    }


@lru_cache(maxsize=8)
def _reference_embeddings(
    provider_key: str,
    reference_path: str,
    reference_mtime: float,
    examples: tuple[str, ...],
) -> tuple[list[list[float]], dict[str, Any]]:
    del provider_key, reference_path, reference_mtime
    return _embed_texts(list(examples))


def _embed_texts(texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
    provider = _provider_label()
    try:
        embedder = vector_tools._embedding_function()
        vectors = embedder(texts)
        return vectors, {
            "provider": provider,
            "model": os.getenv("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small") if provider == "openai" else "medtrace-hash-embedding",
            "fallback_used": False,
        }
    except Exception as exc:
        fallback = vector_tools.HashEmbeddingFunction()
        return fallback(texts), {
            "provider": "hash",
            "model": "medtrace-hash-embedding",
            "fallback_used": True,
            "error": vector_tools._sanitize_error(str(exc)),
        }


def _provider_label() -> str:
    try:
        return str(vector_tools.status().get("embedding_provider") or "hash")
    except Exception:
        return "hash"


def _provider_cache_key(debug: dict[str, Any]) -> str:
    return "|".join([str(debug.get("provider", "")), str(debug.get("model", "")), str(debug.get("fallback_used", ""))])


def _clean_codes(values: Any, valid_codes: list[str]) -> list[str]:
    valid = set(valid_codes)
    if isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values or [])
    cleaned = []
    for value in raw_values:
        code = str(value or "").strip().upper()
        if code in valid and code not in cleaned:
            cleaned.append(code)
    return cleaned


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(left[index] * left[index] for index in range(size))) or 1.0
    right_norm = math.sqrt(sum(right[index] * right[index] for index in range(size))) or 1.0
    return dot / (left_norm * right_norm)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
