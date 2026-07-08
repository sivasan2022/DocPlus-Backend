from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


GENERIC_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "before",
    "during",
    "for",
    "from",
    "into",
    "not",
    "patient",
    "reported",
    "that",
    "the",
    "this",
    "was",
    "with",
}

REQUIREMENT_MATCH_FIELDS = ["id", "text", "category", "module", "component_id", "source_artifact"]

REQUIREMENT_SOURCE_RANK = {
    "controlled": 4,
    "extracted": 3,
    "internal": 2,
    "inferred": 1,
    "synthetic": 0,
}

SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]

SEVERITY_TERMS = {
    "Critical": {"death", "serious", "surgery", "icu", "injury", "harm", "critical", "life", "emergency"},
    "High": {
        "alarm",
        "burn",
        "failed",
        "fails",
        "failure",
        "incorrect",
        "overdose",
        "shock",
        "shut",
        "shutdown",
        "shuts",
        "stopped",
        "unable",
        "underdose",
        "unsafe",
        "unavailable",
    },
    "Medium": {
        "confusing",
        "delay",
        "drain",
        "drains",
        "drift",
        "inaccurate",
        "inconsistent",
        "intermittent",
        "late",
        "pairing",
        "slow",
        "unstable",
        "unexpected",
        "warning",
    },
}

SYMPTOM_SIGNATURE_RULES = [
    (
        "SYMPTOM_STUCK_READING",
        [
            r"\bsame\s+(?:spo2\s+|pulse\s+|measurement\s+)?readings?\b",
            r"\breadings?\s+(?:is|are|was|were|stays?|stayed|remain(?:s|ed)?|keeps?)\s+(?:the\s+)?same\b",
            r"\b(?:value|reading|measurement|spo2|pulse)\s+(?:doesn'?t|does\s+not|do\s+not|won'?t)\s+change\b",
            r"\b(?:value|reading|measurement|spo2|pulse)\s+(?:is|are|was|were)?\s*(?:stuck|frozen|locked)\b",
            r"\bstuck\s+at\b",
            r"\b(?:again\s+and\s+again|repeating|repeatedly|repeats?)\b",
        ],
    ),
    (
        "SYMPTOM_NOT_UPDATING",
        [
            r"\b(?:not|never)\s+updat(?:e|es|ing|ed)\b",
            r"\b(?:doesn'?t|does\s+not|do\s+not|won'?t)\s+updat(?:e|es|ing|ed)\b",
            r"\bstale\s+(?:value|reading|measurement|display)\b",
            r"\b(?:refresh|poll|sample)\s+(?:stops?|stalled|stuck|not\s+running)\b",
        ],
    ),
    (
        "SYMPTOM_INACCURATE",
        [
            r"\bwrong\b",
            r"\bincorrect\b",
            r"\binaccurate\b",
            r"\bunreliable\b",
            r"\bfalse\b",
            r"\bimpossible\s+(?:spo2\s+|pulse\s+|measurement\s+)?values?\b",
            r"\b(?:high|low)\s+(?:spo2\s+|pulse\s+|measurement\s+)?readings?\b",
            r"\breadings?\s+(?:high|low|wrong|incorrect|inaccurate)\b",
            r"\bdiffer(?:s|ed|ing)?\s+(?:significantly\s+)?from\b",
            r"\bdrift(?:s|ed|ing)?\b",
            r"\boffset\b",
        ],
    ),
    (
        "SYMPTOM_MISSING",
        [
            r"\bmissing\b",
            r"\babsent\b",
            r"\b(?:no|zero)\s+(?:spo2\s+|pulse\s+|measurement\s+)?(?:reading|readings|value|signal)\b",
            r"\bno\s+pulse\s+rate\b",
            r"\bpulse\s+rate\s+(?:is\s+|was\s+)?not\s+detected\b",
            r"\b(?:spo2|pulse|measurement|reading|value|signal)\s+(?:not|isn'?t|wasn'?t)\s+(?:shown|displayed|detected|available)\b",
            r"\b(?:cannot|can'?t|unable\s+to)\s+detect\b",
            r"\bblank\s+(?:reading|screen|display)\b",
            r"\bdisappear(?:s|ed|ing)?\b",
        ],
    ),
    (
        "SYMPTOM_DISPLAY_BLACK",
        [r"\bblack\s+screen\b", r"\bscreen\s+(?:is|was|goes|went|stays|remains)?\s*(?:black|blank|dark)\b", r"\bdisplay\s+(?:is|was|goes|went|stays|remains)?\s*(?:black|blank|dark)\b"],
    ),
    (
        "SYMPTOM_DISPLAY_FLICKER",
        [r"\bflicker(?:s|ed|ing)?\b", r"\bgarbled\b", r"\bvisual\s+artifact"],
    ),
    (
        "SYMPTOM_DISPLAY_FREEZE",
        [r"\bdisplay\s+(?:is|was|gets?|got)?\s*(?:frozen|froze|freezes?)\b", r"\bscreen\s+(?:frozen|froze|freezes?)\b"],
    ),
    (
        "SYMPTOM_ALARM_DELAY",
        [r"\balarm\s+(?:delay|delayed|late)\b", r"\bnotification\s+(?:delay|delayed|late)\b"],
    ),
    (
        "SYMPTOM_POWER_FAILURE",
        [r"\bshutdown\b", r"\bshuts?\s+down\b", r"\brestarts?\b", r"\bpower\s+(?:loss|failure)\b"],
    ),
    (
        "SYMPTOM_PHYSICAL_DAMAGE",
        [r"\bdrop(?:ped)?\b", r"\bshock\b", r"\bcrack(?:ed)?\b", r"\bdamage(?:d)?\b"],
    ),
]

PRIMARY_CLINICAL_COMPONENT_IDS = {
    "COMP-MEASUREMENT",
    "COMP-OPTICAL-SENSOR",
    "COMP-SENSOR",
}

PRIMARY_CLINICAL_COMPONENT_TERMS = {
    "measurement",
    "measurement_core",
    "optical",
    "optical_frontend",
    "sensor",
}

PRIMARY_CLINICAL_TEXT_TERMS = {
    "spo2",
    "oxygen",
    "saturation",
    "pulse",
    "pulseox",
    "oximeter",
    "measurement",
    "measurements",
    "reading",
    "readings",
    "perfusion",
    "waveform",
    "clinical",
    "vital",
}

PRIMARY_CLINICAL_SYMPTOMS = {
    "SYMPTOM_STUCK_READING",
    "SYMPTOM_NOT_UPDATING",
    "SYMPTOM_INACCURATE",
    "SYMPTOM_MISSING",
}


def tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9]+", text or "")
        if len(token) > 2 and token.lower() not in GENERIC_STOPWORDS
    ]


def keyword_summary(text: str, limit: int = 6) -> list[str]:
    counts = Counter(tokens(text))
    return [token for token, _ in counts.most_common(limit)]


def severity_from_text(text: str) -> str:
    token_set = set(tokens(text))
    for severity, terms in SEVERITY_TERMS.items():
        if token_set & terms:
            return severity
    return "Low"


def severity_with_context(
    text: str,
    symptom_codes: list[str],
    component: dict[str, Any] | None = None,
    base_severity: str | None = None,
) -> tuple[str, dict[str, Any]]:
    base = base_severity or severity_from_text(text)
    floor, reason = clinical_severity_floor(text, symptom_codes, component)
    final = _max_severity(base, floor)
    return final, {
        "base_severity": base,
        "floor": floor,
        "final_severity": final,
        "floor_applied": _severity_rank(final) > _severity_rank(base),
        "reason": reason,
    }


def clinical_severity_floor(
    text: str,
    symptom_codes: list[str],
    component: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Hard safety floor for primary clinical outputs.

    A pulse oximeter exists to report SpO2/pulse measurements. Wrong, missing, or
    stale values for those outputs are patient-safety-relevant even when the
    complaint wording is quiet, so Intake must not leave them at Low severity.
    """
    if _primary_clinical_output_in_scope(text, symptom_codes, component):
        return (
            "High",
            "Primary clinical measurement output in scope: SpO2/pulse/measurement value complaint with inaccurate, missing, stuck, or non-updating signal.",
        )
    return "Low", "No primary clinical measurement severity floor applied."


def _max_severity(left: str, right: str) -> str:
    return left if _severity_rank(left) >= _severity_rank(right) else right


def _severity_rank(value: str) -> int:
    try:
        return SEVERITY_ORDER.index(value)
    except ValueError:
        return SEVERITY_ORDER.index("Medium")


def infer_timeline(text: str) -> str:
    lower = (text or "").lower()
    if any(term in lower for term in ["during", "while", "immediately", "acute", "sudden"]):
        return "acute event"
    if any(term in lower for term in ["after", "following", "post", "since"]):
        return "post-change or delayed event"
    if any(term in lower for term in ["repeated", "recurring", "intermittent", "sometimes"]):
        return "recurring/intermittent event"
    return "not specified"


def infer_firmware_version(text: str) -> str:
    match = re.search(r"(?:FW[-_\s]*)?v?(\d+(?:\.\d+)+)", text or "", flags=re.IGNORECASE)
    return f"v{match.group(1)}" if match else ""


def infer_serial_or_lot(text: str, label: str) -> str:
    pattern = rf"\b{label}\s*[:#-]?\s*([A-Z0-9][A-Z0-9._-]{{2,}})"
    match = re.search(pattern, text or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def dynamic_symptom_classification(text: str, limit: int = 5) -> dict[str, Any]:
    try:
        from m2_agents.tools.symptom_classifier import classify_symptom_text

        classification = classify_symptom_text(text)
        semantic_codes = [
            code
            for code in classification.get("symptom_codes", [])
            if isinstance(code, str) and code.startswith("SYMPTOM_") and code != "SYMPTOM_UNSPECIFIED"
        ]
    except Exception as exc:
        classification = {
            "path": "regex_fallback_after_classifier_error",
            "error": str(exc)[:800],
            "symptom_codes": [],
        }
        semantic_codes = _semantic_symptom_codes(text)
    keyword_codes = [f"SYMPTOM_{term.upper()}" for term in keyword_summary(text, limit)]
    codes = _dedupe([*semantic_codes, *keyword_codes])
    if not codes:
        codes = ["SYMPTOM_UNSPECIFIED"]
    return {
        "symptom_codes": codes,
        "semantic_symptom_codes": semantic_codes,
        "keyword_symptom_codes": keyword_codes,
        "classification": classification,
    }


def dynamic_symptom_codes(text: str, limit: int = 5) -> list[str]:
    return dynamic_symptom_classification(text, limit).get("symptom_codes", ["SYMPTOM_UNSPECIFIED"])


def symptom_taxonomy() -> list[str]:
    try:
        from m2_agents.tools.symptom_classifier import symptom_reference_taxonomy

        controlled_codes = symptom_reference_taxonomy()
    except Exception:
        controlled_codes = [code for code, _ in SYMPTOM_SIGNATURE_RULES]
    return _dedupe([*controlled_codes, "SYMPTOM_<KEYWORD>", "SYMPTOM_UNSPECIFIED"])


def symptom_taxonomy_details() -> list[dict[str, str]]:
    try:
        from m2_agents.tools.symptom_classifier import symptom_reference_details

        return symptom_reference_details()
    except Exception:
        return [{"code": code, "description": "Regex fallback symptom category."} for code, _ in SYMPTOM_SIGNATURE_RULES]


def _semantic_symptom_codes(text: str) -> list[str]:
    normalized = (text or "").lower()
    codes = []
    for code, patterns in SYMPTOM_SIGNATURE_RULES:
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns):
            codes.append(code)
    return codes


def _primary_clinical_output_in_scope(
    text: str,
    symptom_codes: list[str],
    component: dict[str, Any] | None,
) -> bool:
    symptom_set = set(symptom_codes)
    has_measurement_failure = bool(symptom_set & PRIMARY_CLINICAL_SYMPTOMS)
    if not has_measurement_failure:
        return False

    text_terms = set(tokens(text))
    text_mentions_primary_output = bool(text_terms & PRIMARY_CLINICAL_TEXT_TERMS)
    if text_mentions_primary_output:
        return True

    component = component or {}
    component_id = str(component.get("id", ""))
    if component_id in PRIMARY_CLINICAL_COMPONENT_IDS:
        return True

    component_text = " ".join(
        str(component.get(key, ""))
        for key in ["id", "name", "module", "part_type", "description", "safety_relevance"]
    ).lower()
    return any(term in component_text for term in PRIMARY_CLINICAL_COMPONENT_TERMS)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def score_requirement_relevance(
    text: str,
    requirement: dict[str, Any],
    component_id: str | None = None,
) -> tuple[int, list[str]]:
    """Score a complaint against the same requirement fields used for RTM scoping."""
    haystack = " ".join(str(requirement.get(key, "")) for key in REQUIREMENT_MATCH_FIELDS).lower()
    terms = [term for term in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(term) > 3]
    hits = [term for term in terms if term in haystack]
    score = len(hits)
    if component_id and requirement.get("component_id") == component_id:
        score += 4
    return score, sorted(set(hits))


def match_requirements_by_relevance(
    text: str,
    requirements: list[dict[str, Any]],
    component_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    normalized_text = _normalize_match_text(text)
    for requirement in requirements:
        score, hit_terms = score_requirement_relevance(text, requirement, component_id)
        if score <= 0:
            continue
        req_text = str(requirement.get("text") or requirement.get("acceptance_criteria") or "")
        exact_phrase = 1 if _normalize_match_text(req_text) and _normalize_match_text(req_text) in normalized_text else 0
        source_type = str(requirement.get("source_type", "")).lower()
        matches.append(
            {
                "score": score,
                "match_terms": hit_terms,
                "requirement": requirement,
                "requirement_id": requirement.get("id", ""),
                "component_id": requirement.get("component_id", ""),
                "module": requirement.get("module", ""),
                "source_type": requirement.get("source_type", ""),
                "source_rank": REQUIREMENT_SOURCE_RANK.get(source_type, 0),
                "has_component": 1 if requirement.get("component_id") else 0,
                "exact_phrase": exact_phrase,
                "confidence_score": float(requirement.get("confidence_score", 0) or 0),
            }
        )

    matches.sort(
        key=lambda item: (
            item["score"],
            item["has_component"],
            item["source_rank"],
            item["exact_phrase"],
            item["confidence_score"],
            item["requirement_id"],
        ),
        reverse=True,
    )
    return matches[:limit]


def infer_component(
    text: str,
    components: list[dict[str, Any]],
    fallback_id: str,
    requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not components:
        return {"id": fallback_id, "name": fallback_id, "match_score": 0.0, "match_terms": []}

    requirement_component = _component_from_requirement_match(text, components, requirements or [])
    if requirement_component:
        return requirement_component

    return _infer_component_from_terms(text, components)


def _infer_component_from_terms(text: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    complaint_terms = set(tokens(text))
    scored = []
    for component in components:
        fields = " ".join(str(component.get(key, "")) for key in ["id", "name", "part_type", "supplier_id", "description"])
        component_terms = set(tokens(fields))
        overlap = complaint_terms & component_terms
        degree = int(component.get("degree", 0))
        score = len(overlap) + min(degree, 10) * 0.05
        scored.append((score, sorted(overlap), component))

    scored.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    best_score, match_terms, best = scored[0]
    if best_score <= 0 and len(scored) > 1:
        degree_sorted = sorted(components, key=lambda item: item.get("degree", 0), reverse=True)
        best = degree_sorted[0]
        match_terms = []
    return {**best, "match_score": round(float(best_score), 3), "match_terms": match_terms, "match_source": "component_text"}


def _component_from_requirement_match(
    text: str,
    components: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not requirements:
        return None

    for match in match_requirements_by_relevance(text, requirements, limit=8):
        if match["score"] < 3:
            continue
        component = _component_for_requirement(match["requirement"], components)
        if not component:
            continue
        return {
            **component,
            "match_score": round(float(match["score"]), 3),
            "match_terms": match["match_terms"],
            "match_source": "requirement_scope",
            "matched_requirement": {
                "id": match["requirement_id"],
                "component_id": match["component_id"],
                "module": match["module"],
                "score": match["score"],
                "source_type": match["source_type"],
                "text": match["requirement"].get("text") or match["requirement"].get("acceptance_criteria") or "",
            },
        }
    return None


def _component_for_requirement(
    requirement: dict[str, Any],
    components: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_id = {str(component.get("id", "")): component for component in components}
    component_id = str(requirement.get("component_id") or "")
    if component_id in by_id:
        return by_id[component_id]

    wanted = [_normalize_key(component_id), _normalize_key(requirement.get("module", ""))]
    wanted = [item for item in wanted if item]
    if not wanted:
        return None

    for component in components:
        fields = [
            component.get("id", ""),
            component.get("name", ""),
            component.get("module", ""),
            component.get("part_type", ""),
            component.get("description", ""),
        ]
        normalized_fields = [_normalize_key(field) for field in fields if field]
        if any(want == field or want in field or field in want for want in wanted for field in normalized_fields):
            return component
    return None


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_match_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def next_version(current_version: str | None) -> str:
    if not current_version:
        return "candidate-next"
    match = re.search(r"(\d+)(?!.*\d)", current_version)
    if not match:
        return f"{current_version}-next"
    start, end = match.span(1)
    number = int(match.group(1)) + 1
    width = max(len(match.group(1)), int(math.log10(number)) + 1 if number > 0 else 1)
    return f"{current_version[:start]}{number:0{width}d}{current_version[end:]}"
