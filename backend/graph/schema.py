from __future__ import annotations

from enum import Enum
from typing import Any


class SourceType(str, Enum):
    CONTROLLED = "controlled"
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    SYNTHETIC = "synthetic"
    INTERNAL = "internal"


class EvidenceClass(str, Enum):
    CONTROLLED_VERIFICATION = "controlled_verification"
    HISTORICAL_CONTROLLED = "historical_controlled"
    SIMULATED = "simulated"
    CANDIDATE = "candidate"
    NO_EVIDENCE = "no_evidence"


SOURCE_TYPE_VALUES = {item.value for item in SourceType}
EVIDENCE_CLASS_VALUES = {item.value for item in EvidenceClass}
REPORT_TAG_SOURCE_TYPES = {SourceType.SYNTHETIC.value, SourceType.INFERRED.value}
CAPA_BLOCKING_SOURCE_TYPES = {SourceType.SYNTHETIC.value}
CAPA_BLOCKING_EVIDENCE_CLASSES = {
    EvidenceClass.HISTORICAL_CONTROLLED.value,
    EvidenceClass.SIMULATED.value,
    EvidenceClass.CANDIDATE.value,
    EvidenceClass.NO_EVIDENCE.value,
}


def normalize_source_type(value: Any, fallback: SourceType = SourceType.INFERRED) -> str:
    text = str(value or "").strip().lower()
    return text if text in SOURCE_TYPE_VALUES else fallback.value


def normalize_evidence_class(value: Any, fallback: EvidenceClass = EvidenceClass.CANDIDATE) -> str:
    text = str(value or "").strip().lower()
    return text if text in EVIDENCE_CLASS_VALUES else fallback.value


def source_type_report_tag(value: Any) -> str:
    source_type = normalize_source_type(value)
    if source_type == SourceType.SYNTHETIC.value:
        return "[DEMO DATA - NOT CONTROLLED EVIDENCE]"
    if source_type == SourceType.INFERRED.value:
        return "[INFERRED DATA - REVIEW REQUIRED]"
    return ""


def evidence_class_label(value: Any) -> str:
    evidence_class = normalize_evidence_class(value)
    labels = {
        EvidenceClass.CONTROLLED_VERIFICATION.value: "Controlled current verification",
        EvidenceClass.HISTORICAL_CONTROLLED.value: "Historical controlled evidence - stale or prior version",
        EvidenceClass.SIMULATED.value: "Firmware traceability signal - informational, not controlled verification evidence",
        EvidenceClass.CANDIDATE.value: "Candidate evidence - requires Quality review",
        EvidenceClass.NO_EVIDENCE.value: "No direct evidence found",
    }
    return labels[evidence_class]


def is_uncontrolled_for_report(value: Any) -> bool:
    return normalize_source_type(value) in REPORT_TAG_SOURCE_TYPES
