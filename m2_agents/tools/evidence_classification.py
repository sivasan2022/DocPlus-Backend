from __future__ import annotations

from typing import Any

from backend.graph.schema import EvidenceClass, SourceType, normalize_source_type


def classify_evidence(node: Any, complaint_context: dict[str, Any]) -> str:
    """Classify regulatory usability of evidence without LLM judgment."""

    if not node:
        return EvidenceClass.NO_EVIDENCE.value

    props = _props(node)
    source_type = normalize_source_type(props.get("source_type"), SourceType.INFERRED)
    if source_type == SourceType.SYNTHETIC.value:
        return EvidenceClass.CANDIDATE.value

    labels = {str(label).lower() for label in props.get("labels", [])}
    doc_type = str(props.get("doc_type") or props.get("category") or props.get("artifact_type") or "").lower()
    title_source = f"{props.get('title', '')} {props.get('source', '')} {props.get('source_artifact', '')}".lower()
    review_status = str(props.get("review_status") or "").lower()
    controlled_status = str(props.get("controlled_status") or "").lower()
    objective_evidence = bool(props.get("objective_evidence"))
    evidence_text = f"{doc_type} {title_source}"

    if "simulation" in evidence_text or "twin" in evidence_text:
        return EvidenceClass.SIMULATED.value

    is_test_or_verification = bool(
        labels & {"testrun", "test", "testcase", "telemetrylog", "evidenceartifact"}
    ) or any(term in evidence_text for term in ["verification", "test_report", "test report", "rawlog"])
    is_reviewed = (
        review_status in {"approved", "accepted", "reviewed"}
        or controlled_status in {"approved", "controlled"}
    )
    firmware = str(props.get("firmware_version") or props.get("firmware") or props.get("firmware_tested") or "").strip()
    target_firmware = str(
        complaint_context.get("complaint_firmware")
        or complaint_context.get("current_firmware")
        or complaint_context.get("firmware_version")
        or ""
    ).strip()
    firmware_matches = bool(target_firmware and firmware and firmware == target_firmware)
    firmware_is_stale = bool(target_firmware and firmware and firmware != target_firmware)

    if is_test_or_verification and source_type in {SourceType.CONTROLLED.value, SourceType.EXTRACTED.value}:
        if firmware_is_stale:
            return EvidenceClass.HISTORICAL_CONTROLLED.value
        if firmware_matches and (is_reviewed or objective_evidence):
            return EvidenceClass.CONTROLLED_VERIFICATION.value
        if objective_evidence and is_reviewed and not target_firmware:
            return EvidenceClass.CONTROLLED_VERIFICATION.value

    return EvidenceClass.CANDIDATE.value


def _props(node: Any) -> dict[str, Any]:
    if isinstance(node, dict):
        values = dict(node)
    else:
        values = dict(getattr(node, "properties", {}) or {})
        values.setdefault("id", getattr(node, "id", ""))
        values.setdefault("labels", getattr(node, "labels", []))
    metadata = values.get("metadata")
    if isinstance(metadata, dict):
        merged = dict(metadata)
        merged.update(values)
        values = merged
    return values

