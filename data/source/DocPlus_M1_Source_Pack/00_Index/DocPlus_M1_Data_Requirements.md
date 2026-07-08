# DocPlus+ M1 Professional Data Requirements

Prepared for: DocPlus+ M1 rebuild  
Device focus: Pulse Oximeter with display freeze, software update, and delayed alarm complaint  
Goal: Build an M1 graph that lets M2 investigate actual causes, not broad evidence gaps.

## 1. Brutal Summary

M1 must stop acting like a folder-to-graph demo and become a controlled traceability graph.

Right now M1 has documents, but many real extracted requirements only have `SUPPORTED_BY` links. That means "a document is related to this requirement." AuditShadow needs `VERIFIED_BY` links. That means "this requirement was verified by a specific test case/test run on a specific firmware version with a pass/fail result and objective evidence."

Do not collect random PDFs only. We need structured artifacts that answer:

- What requirement exists?
- Which component does it affect?
- Which risk/hazard does it control?
- Which test verifies it?
- Which firmware version was tested?
- What was the result?
- What artifact proves the result?
- Was this test rerun after the software update?
- If not rerun, is there an approved equivalence rationale?

## 2. Required Folder Structure

When you collect files, organize them like this before uploading/re-ingesting:

```text
DocPlus_M1_Source_Pack/
  00_Index/
    Source_Index.xlsx
    Document_Control_Register.xlsx
  01_Regulatory_Baseline/
    FDA/
    EU_MDR/
    ISO_IEC_Standards/
  02_Device_Profile/
    Device_Master_Record/
    Intended_Use_Labeling/
    Product_Classification/
  03_Requirements/
    System_Requirements/
    Software_Requirements/
    Alarm_Requirements/
    Display_UI_Requirements/
    Firmware_Update_Requirements/
  04_Risk_Management/
    Hazard_Analysis/
    FMEA/
    Risk_Control_Matrix/
  05_Traceability/
    RTM_Master.xlsx
    Component_Requirement_Map.xlsx
    Risk_Requirement_Test_Map.xlsx
  06_Verification_Validation/
    Test_Protocols/
    Test_Cases/
    Test_Runs/
    Test_Reports/
    Raw_Test_Logs/
  07_Firmware_Change_Control/
    Release_Notes/
    Change_Impact_Assessments/
    Commit_Diffs_or_Change_Summaries/
    Regression_Test_Plans/
  08_Complaints_Postmarket/
    Complaint_Records/
    MAUDE_Searches/
    Recall_Searches/
    Similar_Complaint_Trend/
  09_CAPA/
    CAPA_Records/
    Effectiveness_Checks/
  10_Runtime_Telemetry/
    Device_Event_Logs/
    Alarm_Logs/
    Display_Task_Logs/
    Watchdog_Crash_Logs/
    Update_Install_Logs/
  11_Cybersecurity_SBOM/
    SBOM/
    Vulnerability_Assessment/
    Threat_Model/
```

## 3. Public Sources To Surf And Save

Save each web-sourced item as PDF or HTML and record it in `00_Index/Source_Index.xlsx`.

### 3.1 FDA and US Regulatory Sources

| Needed File Name | Source To Search | Why M1 Needs It | M1 Node Type |
|---|---|---|---|
| `FDA_Device_Software_Functions_Guidance_2023.pdf` | FDA page: `Content of Premarket Submissions for Device Software Functions` | Defines recommended documentation for device software functions. Use to build required software-documentation fields. | RegulatoryGuidance |
| `FDA_Cybersecurity_Medical_Devices_Guidance_2026.pdf` | FDA page: `Cybersecurity in Medical Devices: Quality Management System Considerations and Content of Premarket Submissions` | Needed if firmware, update mechanism, connectivity, SBOM, or software vulnerability can affect safety. | RegulatoryGuidance |
| `FDA_Pulse_Oximeters_Page_2025.pdf` | FDA page: `Pulse Oximeters` | Gives pulse oximeter context, FDA efforts, and draft guidance links for pulse oximeter testing and labeling. | RegulatoryGuidance |
| `FDA_Pulse_Oximeter_Draft_Guidance_2025.pdf` | Search FDA: `Pulse Oximeters for Medical Purposes Non-Clinical and Clinical Performance Testing Labeling Premarket Submission Recommendations` | Device-specific performance testing and labeling guidance. Very important for pulse oximeter M1. | RegulatoryGuidance |
| `FDA_Pulse_Oximeters_510k_Guidance_2013.pdf` | Search FDA: `Pulse Oximeters Premarket Notification Submissions 510(k) Guidance` | Older pulse oximeter 510(k) guidance; useful until newer draft is final. | RegulatoryGuidance |
| `FDA_510k_Database_Search_Results.pdf` | FDA 510(k) Premarket Notification database | Find predicate devices and cleared pulse oximeter summaries. Search terms: `pulse oximeter`, product code `DQA`, `K232975`, `alarm`, `wireless`, `software`. | PredicateDevice |
| `FDA_MAUDE_Search_Display_Alarm_PulseOx.pdf` | FDA MAUDE database | Find public adverse event patterns: delayed alarm, no display, frozen display, software problem, alarm failure. | PostMarketSignal |
| `FDA_Recall_Search_PulseOx_Alarm_Software.pdf` | FDA Medical Device Recalls and Early Alerts | Find recall signals for pulse oximeter alarm, display, software, inaccurate readings. | RecallSignal |
| `eCFR_21_CFR_820_QMSR_or_QSR.pdf` | eCFR Title 21 Part 820 | Baseline for quality system/design controls/CAPA/complaints. Save current official text. | Regulation |
| `21_CFR_803_MDR.pdf` | eCFR Title 21 Part 803 | Needed for reportability decision. | Regulation |
| `21_CFR_11_Electronic_Records.pdf` | eCFR Title 21 Part 11 | Needed if DocPlus+ keeps generated PDFs, signatures, audit trails. | Regulation |

### 3.2 International Standards Sources

Some standards are paid. If you cannot obtain full licensed standards, at least collect official standard pages and public summaries. Do not upload pirated copies.

| Needed File Name | Source To Search | Why M1 Needs It | M1 Node Type |
|---|---|---|---|
| `ISO_13485_2016_QMS_Standard.pdf` | ISO store: `ISO 13485:2016 Medical devices quality management systems` | Quality management, design and development, document control, supplier/process controls. | Standard |
| `ISO_14971_2019_Risk_Management.pdf` | ISO store: `ISO 14971:2019 Medical devices application of risk management` | Hazards, risk controls, residual risk, production/post-production feedback. | Standard |
| `ISO_TR_24971_2020_Guidance.pdf` | ISO store: `ISO/TR 24971:2020` | Practical guidance for applying ISO 14971. | StandardGuidance |
| `IEC_62304_Software_Lifecycle.pdf` | IEC webstore: `IEC 62304 medical device software software life cycle processes` | Software requirements, architecture, unit/integration/system testing, maintenance, problem resolution. | Standard |
| `IEC_62366_1_Usability.pdf` | IEC webstore: `IEC 62366-1 usability engineering medical devices` | Display/user-interface hazards, alarm visibility/audibility usability issues. | Standard |
| `IEC_60601_1_General_Safety.pdf` | IEC webstore: `IEC 60601-1 medical electrical equipment general safety essential performance` | General electrical safety and essential performance. | Standard |
| `IEC_60601_1_8_Alarms.pdf` | IEC webstore: `IEC 60601-1-8 alarm systems medical electrical equipment` | Alarm timing, alarm signal behavior, alarm audibility/visibility. Critical for delayed alarm complaint. | Standard |
| `IEC_60601_1_2_EMC.pdf` | IEC webstore: `IEC 60601-1-2 electromagnetic disturbances` | Display freeze or alarm delay may be environmental/EMC-related. | Standard |
| `ISO_80601_2_61_Pulse_Oximeter.pdf` | ISO store: `ISO 80601-2-61 pulse oximeter equipment` | Pulse oximeter-specific basic safety and essential performance. | Standard |

### 3.3 Public Technical / Reference Sources

These are useful for demo evidence and engineering context, but they are not enough by themselves for regulatory verification.

| Needed File Name | Source To Search | Why M1 Needs It | Caution |
|---|---|---|---|
| `TI_TIDA_00311_Miniaturized_Pulse_Oximeter_Reference_Design.pdf` | Texas Instruments: `TIDA-00311 miniaturized pulse oximeter reference design` | Helpful design context, test guide, hardware/software architecture. | Not proof that our device passed tests. |
| `Microchip_Pulse_Oximeter_Demo_Guide.pdf` | Microchip: `pulse oximeter demo guide` | Helpful firmware/demo context. | Demo reference only. |
| `Open_Source_Pulse_Oximeter_Design_Files.zip` | Search GitHub / vendor sources for pulse oximeter design files | Useful to seed design artifacts for hackathon. | Must label as reference/synthetic if not actual device. |
| `FDA_Executive_Summary_Pulse_Oximeter_Advisory_Committee.pdf` | FDA advisory committee / pulse oximeter public meetings | Good clinical/regulatory background. | Not device-specific verification evidence. |

## 4. Controlled Internal Documents We Need From You

These are more important than public web PDFs. Public sources help regulatory context; internal controlled artifacts prove the device.

### 4.1 Device Profile

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `Device_Profile_DEV-PULSE-OX.xlsx` | device_id, model, manufacturer, intended_use, environment_of_use, patient_population, device_class, product_code, UDI, current_firmware | Device |
| `Device_BOM_Component_Map.xlsx` | component_id, component_name, type, supplier, version, safety_relevance | Component |
| `Firmware_Version_History.xlsx` | firmware_id, version, release_date, release_status, supersedes, checksum/build_id | FirmwareVersion |
| `Intended_Use_Labeling.pdf` | intended use, indications, contraindications, warnings, operating environment | Labeling, IntendedUse |

### 4.2 Requirements

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `SYSREQ_PulseOx_System_Requirements.xlsx` | requirement_id, text, rationale, source, acceptance_criteria, component_id, risk_id, status | Requirement |
| `SRS_Measurement_Firmware_Requirements.xlsx` | software_requirement_id, text, module, safety_class, acceptance_criteria, firmware_applicability | Requirement |
| `REQ_Alarm_Behavior_and_Latency.xlsx` | requirement_id, alarm_condition, max_latency_ms, audible_required, visual_required, escalation_rule, acceptance_criteria | Requirement |
| `REQ_Display_UI_Behavior.xlsx` | requirement_id, display_state, refresh_rate, freeze_recovery_requirement, error_message_requirement, acceptance_criteria | Requirement |
| `REQ_Software_Update_Regression.xlsx` | requirement_id, update_condition, rollback_behavior, post_update_self_test, regression_scope, acceptance_criteria | Requirement |
| `REQ_Watchdog_Error_Recovery.xlsx` | requirement_id, fault_condition, watchdog_timeout, recovery_behavior, log_requirement, acceptance_criteria | Requirement |

### 4.3 Risk Management

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `Risk_Management_Plan.pdf` | risk process, severity scale, probability scale, acceptability matrix | RiskManagementPlan |
| `Hazard_Analysis_PulseOx.xlsx` | hazard_id, hazardous_situation, sequence_of_events, harm, severity, probability, risk_control_id | Risk |
| `FMEA_Measurement_Firmware.xlsx` | failure_mode_id, module, failure_mode, cause, effect, detection, severity, occurrence, risk_priority, mitigation | Risk, FailureMode |
| `Risk_Control_Verification_Matrix.xlsx` | risk_control_id, requirement_id, test_id, verification_method, pass_fail | RiskControl |
| `Residual_Risk_Evaluation.pdf` | residual risk decision, benefit-risk rationale, reviewer, approval date | RiskEvaluation |

Complaint-specific risks to include:

- delayed alarm during monitoring
- alarm not visible/audible
- frozen display during active monitoring
- incorrect or stale displayed SpO2 value
- software update regression
- watchdog failure
- event queue overflow
- missed desaturation notification
- user misinterpretation due to frozen UI

### 4.4 Traceability

This is the most important part for fixing AuditShadow.

| File Name To Provide | Required Columns | M1 Links Created |
|---|---|---|
| `RTM_Master.xlsx` | requirement_id, requirement_text, component_id, risk_id, test_case_id, test_protocol_id, test_report_id, evidence_artifact_id, firmware_version, result | Requirement -> VERIFIED_BY -> TestCase |
| `Component_Requirement_Map.xlsx` | component_id, component_name, requirement_id, impact_type, rationale | Component -> AFFECTS -> Requirement |
| `Risk_Requirement_Test_Map.xlsx` | risk_id, risk_control_id, requirement_id, test_case_id, evidence_id | Risk -> MITIGATED_BY -> Requirement -> VERIFIED_BY -> Test |
| `Evidence_Register.xlsx` | evidence_id, file_name, artifact_type, controlled_status, source_path, revision, hash, owner, approval_status | EvidenceArtifact |

Required graph relationship target:

```text
Component -> AFFECTS -> Requirement
Requirement -> MITIGATES -> Risk
Requirement -> VERIFIED_BY -> TestCase
TestCase -> EXECUTED_AS -> TestRun
TestRun -> TESTED_ON -> FirmwareVersion
TestRun -> PRODUCED -> EvidenceArtifact
EvidenceArtifact -> STORED_AS -> SourceDocument
```

### 4.5 Verification and Validation

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `TEST_PROTOCOL_Display_Freeze_Recovery.pdf` | protocol_id, requirement_ids, setup, steps, expected_result, acceptance_criteria | TestProtocol |
| `TEST_PROTOCOL_Alarm_Latency.pdf` | protocol_id, alarm trigger, max latency, measurement method, pass/fail criteria | TestProtocol |
| `TEST_PROTOCOL_Post_Update_Regression.pdf` | protocol_id, firmware_from, firmware_to, regression cases, acceptance criteria | TestProtocol |
| `TEST_CASES_Display_Alarm_Firmware.xlsx` | test_case_id, requirement_id, preconditions, steps, expected_result, acceptance_criteria | TestCase |
| `TESTRUN_AlarmLatency_FW-v3.4.csv` | test_run_id, test_case_id, firmware_version, timestamp_start, timestamp_alarm_condition, timestamp_notification, latency_ms, result | TestRun |
| `TESTRUN_DisplayFreeze_FW-v3.4.csv` | test_run_id, test_case_id, firmware_version, display_state, last_refresh_time, recovery_time_ms, watchdog_event, result | TestRun |
| `TEST_REPORT_Alarm_Display_Regression_FW-v3.4.pdf` | summary, requirement coverage, failures, deviations, reviewer approval | TestReport |
| `RAWLOG_Alarm_Event_Queue_FW-v3.4.log` | event timestamps, queue state, alarm trigger, notification dispatch, UI update | TelemetryLog |
| `RAWLOG_Display_Task_FW-v3.4.log` | UI thread/task state, refresh cycles, freeze/recovery events | TelemetryLog |
| `RAWLOG_Watchdog_Crash_FW-v3.4.log` | watchdog resets, stack traces, error codes | TelemetryLog |

Minimum acceptance criteria examples:

- Alarm notification latency must be <= configured threshold, e.g. `<= 5 seconds` or your actual requirement.
- Display refresh must continue during monitoring, e.g. no stale reading older than accepted interval.
- Post-update regression must pass alarm, display, sensor, watchdog, and event logging cases.
- Any failed test must link to deviation, CAPA, or approved risk acceptance.

### 4.6 Firmware Change / Trace Decay Data

Trace Decay cannot be professional without this.

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `Firmware_Release_Notes_FW-v3.4.pdf` | version, release date, changed modules, bug fixes, known issues, upgrade notes | FirmwareVersion, FirmwareChange |
| `Firmware_Change_Impact_Assessment_FW-v3.4.xlsx` | change_id, changed_component, affected_requirement_id, affected_test_id, regression_required, regression_completed | FirmwareChange |
| `Firmware_Diff_Summary_FW-v3.3_to_FW-v3.4.md` | changed files/modules, functions touched, alarm/display/scheduler impacts | FirmwareChange |
| `Regression_Test_Scope_FW-v3.4.xlsx` | requirement_id, previous_test_fw, current_test_fw, retest_required, retest_result, equivalence_rationale | TraceDecayAssessment |
| `Approved_Equivalence_Rationale_FW-v3.4.pdf` | if tests were not rerun, why old evidence still applies | EquivalenceRationale |

Trace Decay should be able to answer:

```text
Requirement REQ-ALARM-001 was last tested on FW-v3.3.
Complaint occurred after update to FW-v3.4.
Alarm module changed in FW-v3.4.
No FW-v3.4 alarm latency test run exists.
Therefore evidence has decayed and release/complaint investigation requires retest or approved equivalence.
```

### 4.7 Complaint and Postmarket Data

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `COMPLAINT_DisplayFreeze_AlarmDelay_001.xlsx` | complaint_id, date, device_id, serial, firmware_at_event, narrative, patient_impact, reporter, severity | Complaint |
| `Complaint_Investigation_Notes_001.docx` | investigation steps, interviews, reproduction attempts, suspected modules | Investigation |
| `Similar_Complaints_Trend.xlsx` | complaint_id, symptom, device_id, firmware, component, date, outcome | ComplaintTrend |
| `MAUDE_Search_Alarm_Display_PulseOx.xlsx` | report_number, event_type, device_problem_code, narrative, manufacturer, date | PostMarketSignal |
| `Recall_Search_PulseOx_Software_Alarm.xlsx` | recall_id, reason, product, date, root cause, action | RecallSignal |
| `Medical_Device_Reportability_Assessment_001.pdf` | MDR/reportability decision, rationale, reviewer, date | ReportabilityAssessment |

Useful MAUDE search terms:

- `pulse oximeter delayed alarm`
- `pulse oximeter display freeze`
- `pulse oximeter no display`
- `pulse oximeter software problem`
- `pulse oximeter alarm system failure`
- `pulse oximeter application program freezes`
- `pulse oximeter display or visual feedback problem`

### 4.8 CAPA

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `CAPA_DisplayFreeze_AlarmDelay_001.docx` | capa_id, linked_complaint_id, root_cause, correction, corrective_action, preventive_action, owner, due_date, status | CAPA |
| `CAPA_Effectiveness_Verification_001.xlsx` | capa_id, verification_method, requirement_id, test_case_id, result, reviewer | EffectivenessCheck |
| `CAPA_Closure_Approval_001.pdf` | closure rationale, residual risk, approvals | ApprovalRecord |

CAPA must not close unless:

- failed/stale tests are rerun or justified
- alarm/display requirements pass on current firmware
- risk file is updated
- similar complaint trend is reviewed
- effectiveness verification passes

### 4.9 Cybersecurity and SBOM

| File Name To Provide | Minimum Required Fields | M1 Nodes Created |
|---|---|---|
| `SBOM_FW-v3.4.xlsx` | package, version, supplier, license, vulnerability status | SBOMComponent |
| `Cybersecurity_Threat_Model_FW-v3.4.pdf` | assets, threats, mitigations, update security, logging | ThreatModel |
| `Vulnerability_Assessment_FW-v3.4.xlsx` | CVE, affected package, exploitability, patient safety impact, mitigation | CVE, CyberRisk |
| `Software_Update_Security_Controls.pdf` | signing, authentication, rollback, integrity verification | SecurityControl |

This matters because a software update pathway can create safety risk if the update is corrupt, partial, incompatible, unauthorized, or missing rollback.

## 5. Minimum Metadata Every File Should Have

Every collected artifact should have a row in `Source_Index.xlsx`:

| Field | Example |
|---|---|
| `source_id` | SRC-REG-FDA-SOFTWARE-2023 |
| `file_name` | FDA_Device_Software_Functions_Guidance_2023.pdf |
| `source_type` | regulatory / standard / internal / public_reference / synthetic |
| `controlled_status` | approved / draft / external_reference / synthetic |
| `url_or_origin` | FDA page, internal path, supplier package |
| `retrieved_date` | 2026-06-29 |
| `revision_or_publication_date` | 2023-06 |
| `device_specific` | yes/no |
| `can_be_used_as_objective_evidence` | yes/no |
| `notes` | Context only, not direct verification evidence |

## 6. M1 Accuracy Rules

These rules should be enforced during rebuild.

### Rule 1: Do not treat all documents as requirements

Only these create `Requirement` nodes:

- System Requirements Specification
- Software Requirements Specification
- Alarm requirements
- Display/UI requirements
- Firmware update requirements
- Usability requirements
- Risk control requirements

Regulatory guidance creates `RegulatoryGuidance`, not `Requirement`.

Test reports create `TestRun` / `TestReport`, not `Requirement`.

PCB/BOM/design files create `DesignOutput` or `Component`, not `Requirement`.

### Rule 2: Separate evidence classes

Use:

```text
SUPPORTED_BY = contextual/source support
VERIFIED_BY = formal test verification
TESTED_ON = firmware version used in test execution
PRODUCED = objective artifact produced by test run
MITIGATES = requirement or control reduces risk
AFFECTS = component impacts requirement
OCCURRED_AFTER = complaint happened after firmware change
```

### Rule 3: Label data confidence

Every graph node should include:

```text
source_type: controlled | extracted | public_reference | synthetic | inferred
review_status: approved | needs_review | draft
confidence_score: 0.0-1.0
objective_evidence: true | false
```

### Rule 4: Synthetic data must never look real

Synthetic nodes must be obvious:

```text
source_type: synthetic
controlled_status: demo_only
objective_evidence: false
```

M2 should not use synthetic nodes as final proof.

### Rule 5: Trace Decay must use firmware lineage

Every test run must include:

```text
firmware_tested
test_date
test_result
requirement_id
test_case_id
evidence_id
```

Every complaint must include:

```text
firmware_at_event
event_date
update_date_if_known
affected_component
```

## 7. Investigation Data Needed For Actual Root Cause

To move from broad CAPA language to real root cause, provide logs and engineering evidence.

### For Display Freeze

| File Name | Required Data |
|---|---|
| `RAWLOG_Display_Task_FW-v3.4.log` | display refresh timestamp, UI state, stuck task, watchdog reset, error code |
| `Display_Module_Design_Description.pdf` | display architecture, task priority, refresh loop, failure handling |
| `Display_Freeze_Reproduction_Report.pdf` | setup, steps, firmware, observation, expected vs actual |
| `Display_Unit_Test_Results_FW-v3.4.csv` | test case, result, coverage, failure details |

### For Alarm Delay

| File Name | Required Data |
|---|---|
| `RAWLOG_Alarm_Event_Queue_FW-v3.4.log` | alarm trigger timestamp, queue timestamp, notification timestamp, latency |
| `Alarm_Handler_Design_Description.pdf` | alarm architecture, priority, queue, scheduler |
| `Alarm_Latency_Test_Report_FW-v3.4.pdf` | measured latency, threshold, pass/fail |
| `Alarm_Audible_Visual_Verification_FW-v3.4.csv` | audible signal, visual signal, timing, result |

### For Software Update Link

| File Name | Required Data |
|---|---|
| `Update_Install_Log_FW-v3.4.log` | install timestamp, update success/failure, rollback status |
| `FW-v3.4_Change_List.xlsx` | changed modules, changed functions, requirement impact |
| `FW-v3.4_Known_Issues.xlsx` | issue_id, symptom, affected module, workaround, status |
| `Regression_Gap_Assessment_FW-v3.4.xlsx` | requirements affected by update, tests rerun, missing tests |

## 8. Exact M1 Graph Output We Want

After rebuild, M1 should let us query:

```text
Complaint COMPLAINT-001
  -> IMPLICATES -> COMP-FIRMWARE
  -> OCCURRED_AFTER -> FW-CHANGE-v3.4

COMP-FIRMWARE
  -> AFFECTS -> REQ-ALARM-LATENCY-001
  -> AFFECTS -> REQ-DISPLAY-REFRESH-001
  -> AFFECTS -> REQ-UPDATE-REGRESSION-001

REQ-ALARM-LATENCY-001
  -> MITIGATES -> RISK-DELAYED-ALARM-001
  -> VERIFIED_BY -> TEST-ALARM-LATENCY-014

TEST-ALARM-LATENCY-014
  -> EXECUTED_AS -> RUN-ALARM-LATENCY-FW-v3.4-20260615

RUN-ALARM-LATENCY-FW-v3.4-20260615
  -> TESTED_ON -> FW-v3.4
  -> PRODUCED -> TEST_REPORT_AlarmLatency_FW-v3.4.pdf
  result: pass/fail
  latency_ms: actual value
```

## 9. Priority Collection Checklist

### Must Have For Professional M1

- `RTM_Master.xlsx`
- `SYSREQ_PulseOx_System_Requirements.xlsx`
- `SRS_Measurement_Firmware_Requirements.xlsx`
- `REQ_Alarm_Behavior_and_Latency.xlsx`
- `REQ_Display_UI_Behavior.xlsx`
- `TEST_CASES_Display_Alarm_Firmware.xlsx`
- `TEST_REPORT_Alarm_Display_Regression_FW-v3.4.pdf`
- `Firmware_Release_Notes_FW-v3.4.pdf`
- `Firmware_Change_Impact_Assessment_FW-v3.4.xlsx`
- `COMPLAINT_DisplayFreeze_AlarmDelay_001.xlsx`
- `Hazard_Analysis_PulseOx.xlsx`
- `Risk_Control_Verification_Matrix.xlsx`

### Strongly Needed For Actual Cause Investigation

- `RAWLOG_Alarm_Event_Queue_FW-v3.4.log`
- `RAWLOG_Display_Task_FW-v3.4.log`
- `Update_Install_Log_FW-v3.4.log`
- `FW-v3.4_Change_List.xlsx`
- `FW-v3.4_Known_Issues.xlsx`
- `Display_Freeze_Reproduction_Report.pdf`
- `Alarm_Latency_Test_Report_FW-v3.4.pdf`

### Useful Public / Regulatory Context

- FDA software guidance
- FDA cybersecurity guidance
- FDA pulse oximeter page and draft guidance
- FDA 510(k) predicate summaries
- FDA MAUDE reports
- FDA recall search exports
- ISO 13485 page or licensed standard
- ISO 14971 page or licensed standard
- ISO 80601-2-61 page or licensed standard
- IEC 62304, IEC 62366-1, IEC 60601-1-8 pages or licensed standards

## 10. Suggested Source Index Template

Create `00_Index/Source_Index.xlsx` with these columns:

```text
source_id
file_name
folder
artifact_type
source_type
controlled_status
url_or_origin
publication_or_revision_date
retrieved_or_exported_date
device_specific
firmware_specific
firmware_version
related_component_id
related_requirement_id
related_risk_id
related_test_id
objective_evidence
approved_by
approval_date
notes
```

## 11. Suggested RTM Template

Create `05_Traceability/RTM_Master.xlsx` with these columns:

```text
requirement_id
requirement_text
requirement_type
component_id
component_name
risk_id
risk_control_id
test_case_id
test_protocol_id
test_run_id
test_report_id
evidence_artifact_id
firmware_version_tested
current_firmware_version
test_result
acceptance_criteria_met
trace_status
trace_gap_reason
review_status
```

Allowed `trace_status`:

```text
verified_current
verified_stale
missing_test
missing_report
failed_test
needs_quality_review
not_applicable_with_rationale
```

## 12. Suggested Test Run Template

Create `06_Verification_Validation/Test_Runs/TESTRUN_Template.csv`:

```text
test_run_id
test_case_id
requirement_id
protocol_id
operator
test_date
test_environment
device_serial
firmware_version
firmware_build_id
input_condition
expected_result
actual_result
measured_latency_ms
display_refresh_age_ms
alarm_trigger_timestamp
alarm_notification_timestamp
pass_fail
deviation_id
evidence_artifact_id
reviewer
review_date
```

## 13. Suggested Complaint Template

Create `08_Complaints_Postmarket/Complaint_Records/COMPLAINT_Template.xlsx`:

```text
complaint_id
received_date
reporter_type
device_id
serial_number
udi
firmware_at_event
last_update_date
event_date
event_environment
complaint_text
symptom_display_freeze
symptom_alarm_delay
patient_impact
injury_or_death
reportability_initial_decision
affected_component
linked_requirement_ids
linked_risk_ids
linked_test_ids
investigation_status
final_root_cause
capa_id
```

## 14. What Not To Give As Final Proof

These can be included as context, but not treated as final objective verification:

- FDA general webpages
- generic articles
- unrelated 510(k) summaries
- reference designs from TI/Microchip
- open-source demo code
- standards pages alone
- extracted first sentences from PDFs
- PCB/BOM files without verification records
- synthetic test data

They can support background, but they do not prove the DocPlus+ device passed alarm/display/firmware requirements.

## 15. Definition Of Done For M1 Rebuild

M1 is acceptable when these pass:

- 100% of complaint-relevant requirements have component links.
- 100% of complaint-relevant risk controls map to requirements.
- 100% of complaint-relevant requirements have either current verification, failed verification, or an explicit justified gap.
- Every verification test has firmware version, test date, result, acceptance criteria, and evidence artifact.
- Synthetic data is excluded from final readiness scoring or clearly weighted as demo-only.
- Trace Decay can compare previous/current/candidate firmware against actual test runs.
- M2 can produce a root cause that names a concrete module, requirement, test gap, failed test, or telemetry signal.

## 16. Immediate Next Step

Collect these first:

1. `RTM_Master.xlsx`
2. `SRS_Measurement_Firmware_Requirements.xlsx`
3. `REQ_Alarm_Behavior_and_Latency.xlsx`
4. `REQ_Display_UI_Behavior.xlsx`
5. `Firmware_Release_Notes_FW-v3.4.pdf`
6. `Firmware_Change_Impact_Assessment_FW-v3.4.xlsx`
7. `TEST_CASES_Display_Alarm_Firmware.xlsx`
8. `TEST_REPORT_Alarm_Display_Regression_FW-v3.4.pdf`
9. `COMPLAINT_DisplayFreeze_AlarmDelay_001.xlsx`
10. `RAWLOG_Alarm_Event_Queue_FW-v3.4.log`
11. `RAWLOG_Display_Task_FW-v3.4.log`
12. `Hazard_Analysis_PulseOx.xlsx`
13. `Risk_Control_Verification_Matrix.xlsx`

If these 13 are solid, DocPlus+ can move from "professional-looking report" to "professional investigative evidence graph."

