from __future__ import annotations

import re
from typing import Any

from backend.graph.schema import EvidenceClass, SourceType
from m2_agents.agents.base_agent import BaseAgent
from m2_agents.core import dynamic
from m2_agents.core.observability import traced
from m2_agents.core.state import GraphState
from m2_agents.tools import graph_tools


class FirmwareTraceabilityRippleCheckAgent(BaseAgent):
    name = "firmware_traceability_ripple_check"
    firmware_core_terms = [
        "firmware",
        "software",
        "update",
        "upgrade",
        "version",
        "release",
        "patch",
        "regression",
    ]
    firmware_adjacent_terms = [
        "ble",
        "bluetooth",
        "connect",
        "connectivity",
        "pair",
        "sync",
        "app",
        "application",
        "wireless",
        "wifi",
    ]
    unsupported_scenario_rules = [
        (
            "environmental_or_physical_conditions",
            "environmental, altitude, motion, or physical-condition verification method",
            [
                "environment",
                "environmental",
                "temperature",
                "humidity",
                "altitude",
                "airplane",
                "aeroplane",
                "flight",
                "motion",
                "vibration",
                "drop",
                "shock",
                "lighting",
                "ambient",
                "water",
            ],
        ),
        (
            "measurement_calibration_or_physiology",
            "optical bench, calibration, or physiology verification method",
            [
                "measurement",
                "measure",
                "reading",
                "readings",
                "sensor",
                "spo2",
                "oxygen",
                "saturation",
                "pulse",
                "calibration",
                "calibrated",
                "drift",
                "optical",
                "perfusion",
                "artifact",
            ],
        ),
        (
            "display_timing_or_ui",
            "display refresh, UI timing, or screen-rendering verification method",
            [
                "display",
                "screen",
                "flicker",
                "flickering",
                "freeze",
                "frozen",
                "ui",
                "refresh",
                "pixel",
                "lcd",
                "render",
            ],
        ),
        (
            "signal_path_or_connector_hardware",
            "connector, signal-path, solder-joint, or hardware continuity verification method",
            [
                "connector",
                "connection",
                "signal",
                "intermittent",
                "loose",
                "cable",
                "contact",
                "solder",
                "joint",
            ],
        ),
        (
            "alarm_timing_or_clinical_notification",
            "alarm timing, threshold, or notification verification method",
            [
                "alarm",
                "alert",
                "notification",
                "delay",
                "delayed",
                "threshold",
                "audible",
                "notify",
            ],
        ),
        (
            "power_battery_electrical",
            "battery discharge, voltage, current, or electrical-load verification method",
            [
                "battery",
                "power",
                "charge",
                "charging",
                "voltage",
                "current",
                "overheat",
                "thermal",
                "electrical",
            ],
        ),
        (
            "connectivity_protocol_execution",
            "BLE, Wi-Fi, companion-app, or wireless protocol execution harness",
            [
                "ble",
                "bluetooth",
                "connect",
                "connectivity",
                "pair",
                "sync",
                "wifi",
                "wireless",
                "app",
                "application",
            ],
        ),
    ]

    def run(self, state: GraphState) -> GraphState:
        with traced(state, self.name, "run firmware traceability ripple checks"):
            complaint = state.structured_complaint
            state.digital_twin_results = []
            if complaint is None:
                state.errors.append("FirmwareTraceabilityRippleCheckAgent requires structured_complaint")
                return state

            capability_report = self._capability_report()
            target_version = self._target_firmware(state)
            ripple_result: dict[str, Any] | None = None
            selected_hypotheses = sorted(
                state.hypotheses,
                key=lambda item: item.base_probability,
                reverse=True,
            )[:3]
            capability_checks: list[dict[str, Any]] = []
            for hypothesis in selected_hypotheses:
                capability_check = self._evaluate_capability(state, hypothesis)
                capability_checks.append(capability_check)
                state.add_event(
                    self.name,
                    "hypothesis capability check",
                    "info",
                    hypothesis_id=hypothesis.id,
                    required_scenario_type=capability_check["required_scenario_type"],
                    capability=capability_check["capability"],
                    supported=capability_check["supported"],
                    matched_terms=capability_check["matched_terms"],
                )
                if capability_check["supported"] and capability_check["capability"] == "firmware_ripple_graph_twin":
                    if ripple_result is None:
                        ripple_result = graph_tools.run_ripple(
                            state.device_id,
                            target_version,
                            [complaint.affected_component],
                        )
                    state.digital_twin_results.append(
                        self._firmware_ripple_result(state, hypothesis, target_version, ripple_result, capability_check)
                    )
                    continue
                state.digital_twin_results.append(self._unsupported_result(hypothesis, capability_check))

            if not state.digital_twin_results:
                state.digital_twin_results.append(
                    self._no_hypotheses_result()
                )

            executed = [item for item in state.digital_twin_results if item.get("status") == "executed"]
            unsupported = [item for item in state.digital_twin_results if item.get("status") != "executed"]
            state.agent_debug[self.name] = {
                "outcome": {
                    "executed_count": len(executed),
                    "unsupported_count": len(unsupported),
                    "results": state.digital_twin_results,
                },
                "capability_report": capability_report,
                "capability_checks": capability_checks,
                "input_summary": {
                    "affected_component": complaint.affected_component,
                    "affected_component_name": complaint.affected_component_name,
                    "symptom_codes": complaint.symptom_codes,
                    "hypothesis_count": len(state.hypotheses),
                },
            }
            state.status = "firmware_traceability_check_completed"
        return state

    def _evaluate_capability(self, state: GraphState, hypothesis: Any) -> dict[str, Any]:
        hypothesis_text = self._hypothesis_primary_text(hypothesis)
        hypothesis_reasoning_text = self._hypothesis_reasoning_text(hypothesis)
        complaint_text = self._complaint_context_text(state)
        firmware_matches = self._matched_terms(hypothesis_text, self.firmware_core_terms)
        adjacent_matches = self._matched_terms(hypothesis_text, self.firmware_adjacent_terms)
        complaint_firmware_matches = self._matched_terms(complaint_text, self.firmware_core_terms)
        if not firmware_matches and not adjacent_matches:
            firmware_matches = self._matched_terms(hypothesis_reasoning_text, self.firmware_core_terms)
            adjacent_matches = self._matched_terms(hypothesis_reasoning_text, self.firmware_adjacent_terms)

        if firmware_matches or (adjacent_matches and complaint_firmware_matches):
            matched_terms = list(dict.fromkeys([*firmware_matches, *adjacent_matches, *complaint_firmware_matches]))
            return {
                "hypothesis_id": hypothesis.id,
                "hypothesis_title": hypothesis.title,
                    "supported": True,
                    "capability": "firmware_ripple_graph_twin",
                    "required_scenario_type": "firmware_what_if_ripple",
                    "matched_terms": matched_terms,
                    "reason": (
                        "This hypothesis is framed as a firmware/software/change-control failure mode, which matches the "
                        "current firmware traceability ripple-check capability."
                    ),
                }

        category, harness, matches = self._unsupported_scenario(hypothesis_text)
        if not matches:
            category, harness, matches = self._unsupported_scenario(hypothesis_reasoning_text)
        if not matches:
            category, harness, matches = self._unsupported_scenario(complaint_text)
        if not matches:
            category = "specialized_device_behavior"
            harness = "validated device-behavior simulator for this failure mode"

        return {
            "hypothesis_id": hypothesis.id,
            "hypothesis_title": hypothesis.title,
            "supported": False,
            "capability": f"unsupported_by_current_twin:{category}",
            "required_scenario_type": category,
            "matched_terms": matches,
            "reason": (
                f"This hypothesis requires this verification method outside the current graph-check scope: {harness}; "
                "the current check only examines firmware-to-requirement-to-test traceability links."
            ),
        }

    def _contains_term(self, text: str, terms: list[str]) -> bool:
        return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) for term in terms)

    def _matched_terms(self, text: str, terms: list[str]) -> list[str]:
        return [term for term in terms if self._contains_term(text, [term])]

    def _unsupported_scenario(self, text: str) -> tuple[str, str, list[str]]:
        for category, harness, terms in self.unsupported_scenario_rules:
            matches = self._matched_terms(text, terms)
            if matches:
                return category, harness, matches
        return "", "", []

    def _hypothesis_primary_text(self, hypothesis: Any) -> str:
        values = [
            getattr(hypothesis, "id", ""),
            getattr(hypothesis, "title", ""),
            getattr(hypothesis, "description", ""),
            getattr(hypothesis, "affected_component", ""),
            getattr(hypothesis, "probability_rationale", ""),
        ]
        return " ".join(str(value) for value in values if value).lower()

    def _hypothesis_reasoning_text(self, hypothesis: Any) -> str:
        return " ".join(str(value) for value in getattr(hypothesis, "why_chain", []) or [] if value).lower()

    def _complaint_context_text(self, state: GraphState) -> str:
        complaint = state.structured_complaint
        values = [
            state.raw_complaint or "",
            complaint.affected_component if complaint else "",
            complaint.affected_component_name if complaint else "",
            " ".join(complaint.symptom_codes if complaint else []),
        ]
        return " ".join(str(value) for value in values if value).lower()

    def _firmware_ripple_result(
        self,
        state: GraphState,
        hypothesis: Any,
        target_version: str,
        ripple_result: dict[str, Any],
        capability_check: dict[str, Any],
    ) -> dict[str, Any]:
        complaint = state.structured_complaint
        hypothesis_id = getattr(hypothesis, "id", "") or "GENERAL"
        hypothesis_label = self._hypothesis_label(hypothesis)
        stale_count = int(ripple_result.get("stale_test_count") or 0)
        affected_count = int(ripple_result.get("affected_requirement_count") or 0)
        result = "fail" if stale_count else "pass" if affected_count else "inconclusive"
        if result == "fail":
            interpretation = (
                f"{hypothesis_id} evaluates {hypothesis_label}. The firmware traceability ripple check found "
                f"{stale_count} linked verification test record(s) that do not match candidate firmware {target_version}; "
                "this flags stale or mismatched verification coverage for this hypothesis, not device behavior and not CAPA closure."
            )
        elif result == "pass":
            interpretation = (
                f"{hypothesis_id} evaluates {hypothesis_label}. The firmware traceability ripple check found affected "
                "requirements but no stale linked test records for the candidate version. This is an informational graph "
                "traceability signal and is not controlled verification for this hypothesis."
            )
        else:
            interpretation = (
                f"{hypothesis_id} evaluates {hypothesis_label}. The firmware traceability ripple check did not find a "
                "component-to-requirement path specific enough to assess linked verification freshness for this hypothesis."
            )
        return {
            "id": f"DTWIN-{hypothesis_id or 'GENERAL'}-FW-RIPPLE",
            "hypothesis_id": hypothesis_id,
            "status": "executed",
            "capability": "firmware_ripple_graph_twin",
            "required_scenario_type": capability_check["required_scenario_type"],
            "matched_terms": capability_check["matched_terms"],
            "scenario": (
                f"Firmware traceability ripple check for {hypothesis_id}: {hypothesis_label} "
                f"on {complaint.affected_component if complaint else state.device_id}"
            ),
            "simulated_result": result,
            "target_firmware": target_version,
            "changed_components": [complaint.affected_component] if complaint else [],
            "affected_requirement_count": affected_count,
            "stale_test_count": stale_count,
            "affected_requirements": ripple_result.get("affected_requirements", [])[:12],
            "stale_tests": ripple_result.get("stale_tests", [])[:6],
            "interpretation": interpretation,
            "evidence_class": EvidenceClass.SIMULATED.value,
            "source_type": SourceType.INTERNAL.value,
            "confidence": 0.58 if result != "inconclusive" else 0.42,
            "limitations": [
                "This graph check examines firmware trace links only; it is not a device-behavior model or software execution harness.",
                "The traceability signal cannot replace controlled verification, returned-unit testing, or Quality-approved evidence.",
            ],
        }

    def _unsupported_result(self, hypothesis: Any, capability_check: dict[str, Any]) -> dict[str, Any]:
        hypothesis_id = getattr(hypothesis, "id", "") or "GENERAL"
        hypothesis_label = self._hypothesis_label(hypothesis)
        required_type = capability_check["required_scenario_type"]
        interpretation = (
            f"{hypothesis_id} evaluates {hypothesis_label}. The firmware traceability ripple check was not run because "
            f"this failure mode maps to '{required_type}', outside firmware-to-test traceability freshness review. "
            f"{capability_check['reason']}"
        )
        return {
            "id": f"DTWIN-{hypothesis_id or 'GENERAL'}-UNSUPPORTED",
            "hypothesis_id": hypothesis_id,
            "status": "unsupported",
            "capability": capability_check["capability"],
            "required_scenario_type": required_type,
            "matched_terms": capability_check["matched_terms"],
            "scenario": f"No firmware traceability ripple check was applicable for {hypothesis_id}: {hypothesis_label}.",
            "simulated_result": "not_run",
            "interpretation": interpretation,
            "evidence_class": EvidenceClass.SIMULATED.value,
            "source_type": SourceType.INTERNAL.value,
            "confidence": 0.0,
            "limitations": [
                "The repo currently exposes firmware traceability ripple checks only for this agent output.",
                "No controlled bench, device-behavior, or protocol verification method is executed by this graph check.",
            ],
        }

    def _no_hypotheses_result(self) -> dict[str, Any]:
        return {
            "id": "DTWIN-GENERAL-UNSUPPORTED",
            "hypothesis_id": "",
            "status": "unsupported",
            "capability": "unsupported_by_current_twin:no_hypotheses",
            "required_scenario_type": "no_hypotheses",
            "matched_terms": [],
            "scenario": "No firmware traceability ripple check was run because no root-cause hypotheses were available.",
            "simulated_result": "not_run",
            "interpretation": "No root-cause hypotheses were available for firmware traceability ripple checking.",
            "evidence_class": EvidenceClass.SIMULATED.value,
            "source_type": SourceType.INTERNAL.value,
            "confidence": 0.0,
            "limitations": [
                "The repo currently exposes firmware traceability ripple checks only for this agent output.",
                "Firmware traceability ripple checking requires at least one scoped root-cause hypothesis.",
            ],
        }

    def _hypothesis_label(self, hypothesis: Any) -> str:
        title = str(getattr(hypothesis, "title", "") or "").strip()
        description = str(getattr(hypothesis, "description", "") or "").strip()
        label = title or description or "the proposed failure mode"
        return re.sub(r"\s+", " ", label)[:180]

    def _target_firmware(self, state: GraphState) -> str:
        complaint = state.structured_complaint
        current = (
            (complaint.firmware_version if complaint else "")
            or state.complaint_firmware_version
            or state.graph_context.get("device", {}).get("current_firmware")
            or "v3.4"
        )
        return dynamic.next_version(current)

    def _capability_report(self) -> dict[str, Any]:
        return {
            "supported_scenario_types": [
                "graph_topology_context_fetch",
                "firmware_what_if_ripple",
            ],
            "executable_graph_checks": [
                "firmware_ripple_graph_twin",
            ],
            "existing_capabilities_found": [
                {
                    "name": "store.device_twin",
                    "type": "graph topology context",
                    "usable_for": "fetching current device nodes, edges, component, requirement, risk, evidence, and complaint context",
                },
                {
                    "name": "propagate_firmware_change",
                    "type": "firmware traceability ripple check",
                    "usable_for": "checking affected requirements and stale verification links for a proposed firmware version",
                },
            ],
            "not_found": [
                "validated mechanical shock verification runner",
                "display refresh timing verification runner",
                "battery discharge verification runner",
                "optical bench/SpO2 physiology verification runner",
                "BLE protocol execution harness",
            ],
        }
