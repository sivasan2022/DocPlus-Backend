from __future__ import annotations

from faker import Faker

from backend.graph.schema import SourceType
from backend.graph.store import JsonGraphStore

fake = Faker()

SYNTHETIC_METADATA = {
    "source_type": SourceType.SYNTHETIC.value,
    "controlled_status": "demo_only",
    "review_status": "draft",
    "confidence_score": 0.35,
    "objective_evidence": False,
}


PULSE_REQUIREMENTS = [
    ("REQ-SPO2-001", "SpO2 accuracy shall be within clinically acceptable limits across 70-100 percent saturation.", "ISO 80601-2-61"),
    ("REQ-SPO2-002", "Pulse rate measurement shall remain stable during low perfusion conditions.", "ISO 80601-2-61"),
    ("REQ-SPO2-003", "Alarm behavior shall notify the user when saturation drops below configured thresholds.", "IEC 60601-1-8"),
    ("REQ-SPO2-004", "The sensor interface shall detect disconnected or degraded probes.", "IEC 60601-1"),
    ("REQ-SPO2-005", "The display shall present SpO2 and pulse rate readings without ambiguity.", "IEC 62366-1"),
    ("REQ-SPO2-006", "The device shall maintain operation on battery for the specified monitoring duration.", "IEC 60601-1"),
    ("REQ-SPO2-007", "The firmware shall reject invalid sensor packets and log communication errors.", "IEC 62304"),
    ("REQ-SPO2-008", "Cybersecurity controls shall protect update and configuration interfaces.", "FDA Cybersecurity Guidance"),
]

GENERIC_REQUIREMENTS = [
    ("REQ-GEN-001", "Design inputs shall be traceable to verification evidence.", "21 CFR 820.30"),
    ("REQ-GEN-002", "Design outputs shall satisfy approved design inputs.", "21 CFR 820.30"),
    ("REQ-GEN-003", "Risk controls shall be verified for effectiveness.", "ISO 14971"),
    ("REQ-GEN-004", "Complaint records shall be evaluated for reportability.", "21 CFR 820.198"),
    ("REQ-GEN-005", "CAPA records shall identify root cause and verification of effectiveness.", "21 CFR 820.100"),
    ("REQ-GEN-006", "Software changes shall be assessed for verification impact.", "IEC 62304"),
    ("REQ-GEN-007", "Post-market signals shall be linked to affected device models.", "EU MDR PMS"),
    ("REQ-GEN-008", "Usability-related hazards shall be evaluated and mitigated.", "IEC 62366-1"),
    ("REQ-GEN-009", "Supplied components shall be traceable to affected products.", "ISO 13485"),
    ("REQ-GEN-010", "Technical documentation shall include current objective evidence.", "EU MDR Annex II"),
    ("REQ-GEN-011", "Cybersecurity vulnerabilities shall be assessed for patient impact.", "FDA Cybersecurity Guidance"),
    ("REQ-GEN-012", "The risk management file shall remain current through production and post-production.", "ISO 14971"),
]

DEMO_REQUIREMENT_IDS = [req_id for req_id, _, _ in (PULSE_REQUIREMENTS + GENERIC_REQUIREMENTS)[:20]]


def add_synthetic_backfill(store: JsonGraphStore, device_id: str, current_firmware: str) -> dict[str, int]:
    requirements_added = _ensure_requirements(store, device_id)
    risks_added = _ensure_risks(store, device_id)
    tests_added = _ensure_tests(store, device_id, current_firmware)
    complaints_added = _ensure_complaints(store, device_id)
    capas_added = _ensure_capas(store)
    _ensure_components_and_sbom(store, device_id)
    return {
        "requirements": requirements_added,
        "tests": tests_added,
        "risks": risks_added,
        "complaints": complaints_added,
        "capas": capas_added,
    }


def _ensure_requirements(store: JsonGraphStore, device_id: str) -> int:
    added = 0
    all_reqs = PULSE_REQUIREMENTS + GENERIC_REQUIREMENTS
    for req_id, text, standard in all_reqs[:20]:
        existed = store.get_node(req_id) is not None
        store.upsert_node(
            req_id,
            ["Requirement"],
            text=text,
            standard=standard,
            category="Design Control",
            acceptance_criteria=f"Objective evidence confirms: {text}",
            status="Active",
            demo_disclaimer="Synthetic backfill for hackathon workflow coverage; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        store.upsert_edge(device_id, req_id, "CONTAINS", rationale="Device design input scope")
        added += 0 if existed else 1
    return added


def _ensure_risks(store: JsonGraphStore, device_id: str) -> int:
    hazards = [
        "Inaccurate SpO2 reading delays clinical intervention",
        "Low perfusion produces unstable pulse rate",
        "Alarm threshold misconfiguration",
        "Probe disconnect not detected",
        "Battery depletion during monitoring",
        "Firmware communication fault",
        "Display ambiguity causes user error",
        "Cybersecurity update tampering",
        "Post-market complaint recurrence",
        "Supplier sensor material change",
    ]
    added = 0
    reqs = DEMO_REQUIREMENT_IDS
    for index, hazard in enumerate(hazards, start=1):
        risk_id = f"RISK-{index:03d}"
        existed = store.get_node(risk_id) is not None
        severity = 2 + (index % 4)
        probability = 1 + (index % 5)
        store.upsert_node(
            risk_id,
            ["Risk"],
            hazard=hazard,
            severity=severity,
            probability=probability,
            risk_level="High" if severity * probability >= 12 else "Medium",
            mitigation=f"Mitigation controls for {hazard.lower()}",
            demo_disclaimer="Synthetic backfill for hackathon workflow coverage; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        store.upsert_edge(device_id, risk_id, "HAS_RISK", rationale="Risk management file")
        if reqs:
            store.upsert_edge(reqs[(index - 1) % len(reqs)], risk_id, "MITIGATED_BY", rationale="Requirement mitigates hazard")
        added += 0 if existed else 1
    return added


def _ensure_tests(store: JsonGraphStore, device_id: str, current_firmware: str) -> int:
    reqs = DEMO_REQUIREMENT_IDS
    evidence = [node.id for node in store.nodes_by_label("Evidence")]
    if not evidence:
        evidence_id = "EVID-SYN-001"
        store.upsert_node(
            evidence_id,
            ["Evidence"],
            title="Synthetic DHF Evidence Pack",
            category="evidence",
            demo_disclaimer="Synthetic fallback evidence; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        store.upsert_edge(device_id, evidence_id, "HAS_DOCUMENT", rationale="Synthetic fallback evidence")
        evidence = [evidence_id]

    added = 0
    for index in range(1, 31):
        test_id = f"TEST-{index:03d}"
        existed = store.get_node(test_id) is not None
        firmware_tested = "v2.1" if index in {7, 18, 27} else current_firmware
        result = "Pass" if index not in {12, 23} else "Needs Review"
        store.upsert_node(
            test_id,
            ["Test"],
            name=f"Verification Test {index:03d}",
            method="Protocol-driven verification",
            result=result,
            firmware_tested=firmware_tested,
            acceptance_criteria_met=result == "Pass",
            date=f"2026-0{1 + (index % 6)}-{10 + (index % 18):02d}",
            demo_disclaimer="Synthetic backfill for hackathon workflow coverage; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        if reqs:
            req_id = reqs[(index - 1) % len(reqs)]
            store.upsert_edge(req_id, test_id, "VERIFIED_BY", rationale="Traceability matrix")
        store.upsert_edge(test_id, evidence[(index - 1) % len(evidence)], "SUPPORTED_BY", rationale="Objective evidence link")
        added += 0 if existed else 1
    return added


def _ensure_complaints(store: JsonGraphStore, device_id: str) -> int:
    added = 0
    descriptions = [
        "Patient reported intermittent low SpO2 readings during home monitoring.",
        "Clinician observed probe disconnect warning did not appear immediately.",
        "Battery indicator dropped rapidly during extended monitoring.",
        "Pulse rate display froze until the sensor was reconnected.",
        "Alarm sounded late during simulated desaturation.",
        "User reported confusion between perfusion indicator and battery icon.",
        "Device returned after post-market recall communication.",
        "Field report indicates inconsistent readings in low temperature environment.",
        "Customer reported app pairing failure after firmware update.",
        "Complaint references suspected sensor cable degradation.",
        "Distributor noted repeated false low saturation alerts.",
        "MAUDE-style adverse event record linked to pulse oximeter use.",
        "Recall search result references possible measurement inaccuracy.",
        "Healthcare professional noted limitations in darker skin pigmentation cohorts.",
        "Support ticket indicates unexpected reboot during charging.",
    ]
    for index, description in enumerate(descriptions, start=1):
        complaint_id = f"CMP-{index:03d}"
        existed = store.get_node(complaint_id) is not None
        store.upsert_node(
            complaint_id,
            ["Complaint"],
            description=description,
            severity=["Low", "Medium", "High"][index % 3],
            status="Closed" if index % 5 else "Open",
            date=f"2026-0{1 + (index % 6)}-{index + 3:02d}",
            demo_disclaimer="Synthetic backfill for hackathon workflow coverage; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        store.upsert_edge(complaint_id, device_id, "REPORTED_ON", rationale="Post-market surveillance")
        added += 0 if existed else 1
    return added


def _ensure_capas(store: JsonGraphStore) -> int:
    added = 0
    complaints = [node.id for node in store.nodes_by_label("Complaint")][:15]
    risks = [node.id for node in store.nodes_by_label("Risk")][:10]
    for index in range(1, 6):
        capa_id = f"CAPA-{index:03d}"
        existed = store.get_node(capa_id) is not None
        store.upsert_node(
            capa_id,
            ["CAPA"],
            title=f"CAPA for recurring pulse oximeter signal issue {index}",
            root_cause="Under investigation" if index in {2, 5} else "Verified process or design contributor",
            action="Update verification protocol and perform effectiveness check",
            status="Open" if index in {2, 5} else "Closed",
            due_date=f"2026-08-{10 + index:02d}",
            demo_disclaimer="Synthetic backfill for hackathon workflow coverage; not controlled DocPlus+ proof.",
            **SYNTHETIC_METADATA,
        )
        if complaints:
            store.upsert_edge(complaints[(index - 1) % len(complaints)], capa_id, "TRIGGERS", rationale="Complaint investigation")
        if risks:
            store.upsert_edge(capa_id, risks[(index - 1) % len(risks)], "ADDRESSES", rationale="Corrective action risk linkage")
        added += 0 if existed else 1
    return added


def _ensure_components_and_sbom(store: JsonGraphStore, device_id: str) -> None:
    components = [
        ("COMP-SENSOR", "Optical Sensor", "Sensor"),
        ("COMP-MCU", "Microcontroller", "Electronics"),
        ("COMP-BATTERY", "Battery Pack", "Power"),
        ("COMP-FIRMWARE", "Measurement Firmware", "Software"),
    ]
    reqs = [node.id for node in store.nodes_by_label("Requirement")][:20]
    for index, (component_id, name, part_type) in enumerate(components):
        store.upsert_node(
            component_id,
            ["Component"],
            name=name,
            part_type=part_type,
            supplier_id=f"SUP-{index + 1:03d}",
            demo_disclaimer="Synthetic component backfill for hackathon workflow coverage.",
            **SYNTHETIC_METADATA,
        )
        store.upsert_edge(device_id, component_id, "HAS_COMPONENT", rationale="Device bill of materials")
        if reqs:
            store.upsert_edge(component_id, reqs[index % len(reqs)], "AFFECTS", rationale="Component affects requirement")

    store.upsert_node("SBOM-OPENSSL", ["SBOM_Component"], name="OpenSSL", version="3.0.x", package_type="library", **SYNTHETIC_METADATA)
    store.upsert_node("CVE-2026-DEMO", ["CVE"], cve_id="CVE-2026-DEMO", severity="Medium", affected_component="OpenSSL", **SYNTHETIC_METADATA)
    store.upsert_edge("SBOM-OPENSSL", device_id, "AFFECTS_COMPONENT_IN", rationale="SBOM package used by device")
    store.upsert_edge("CVE-2026-DEMO", device_id, "AFFECTS_COMPONENT_IN", rationale="Cybersecurity watchlist")
