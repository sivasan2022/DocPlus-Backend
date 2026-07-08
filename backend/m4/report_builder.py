from __future__ import annotations

import re
import hashlib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.graph.schema import (
    EvidenceClass,
    SourceType,
    evidence_class_label,
    normalize_evidence_class,
    normalize_source_type,
    source_type_report_tag,
)
from m2_agents.core import trace_ai
from m2_agents.core.state import GraphState


PROJECT_NAME = "DocPlus+"
OUTPUT_DIR = Path("output/pdf")
LOGO_PATH = Path("assets/docplus_logo.png")
BRAND_PATTERN = re.compile("medtraceai", re.IGNORECASE)
SECTION_HEADING_KEEP_HEIGHT = 34 * mm
SUBSECTION_HEADING_KEEP_HEIGHT = 24 * mm
CONTENT_WIDTH = 172 * mm
DOCPLUS_BLUE = colors.HexColor("#102A43")
TABLE_STRIPE = colors.HexColor("#EEF2F6")
FONT_REGULAR = "Times-Roman"
FONT_BOLD = "Times-Bold"
FONT_ITALIC = "Times-Italic"


@dataclass(frozen=True)
class GeneratedReport:
    document_id: str
    filename: str
    output_path: Path
    generated_at: str
    pages_estimate: int


class Rule(Flowable):
    def __init__(self, width: float, color: colors.Color = colors.HexColor("#17A398")):
        super().__init__()
        self.width = width
        self.color = color
        self.height = 1

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(1.2)
        self.canv.line(0, 0, self.width, 0)


def _paginate_story(story: list[Any]) -> list[Any]:
    paginated: list[Any] = []
    for flowable in story:
        if _is_heading(flowable, "h1"):
            if _is_top_level_heading(flowable):
                if paginated and not isinstance(paginated[-1], PageBreak):
                    paginated.append(PageBreak())
                paginated.append(CondPageBreak(SECTION_HEADING_KEEP_HEIGHT))
                paginated.append(_heading_band(flowable))
                continue
            paginated.append(CondPageBreak(SUBSECTION_HEADING_KEEP_HEIGHT))
            paginated.append(flowable)
            continue
        if _is_heading(flowable, "h2"):
            paginated.append(CondPageBreak(SUBSECTION_HEADING_KEEP_HEIGHT))
        paginated.append(flowable)
    return paginated


def _is_top_level_heading(flowable: Any) -> bool:
    if not _is_heading(flowable, "h1"):
        return False
    return re.match(r"^\d+\.\s+", _plain_paragraph_text(flowable)) is not None


def _plain_paragraph_text(flowable: Paragraph) -> str:
    if hasattr(flowable, "getPlainText"):
        return str(flowable.getPlainText())
    return str(getattr(flowable, "text", ""))


def _is_heading(flowable: Any, style_name: str) -> bool:
    return isinstance(flowable, Paragraph) and getattr(flowable.style, "name", "") == style_name


def _register_times_new_roman() -> None:
    global FONT_REGULAR, FONT_BOLD, FONT_ITALIC

    font_dir = Path("C:/Windows/Fonts")
    fonts = {
        "TimesNewRoman": font_dir / "times.ttf",
        "TimesNewRoman-Bold": font_dir / "timesbd.ttf",
        "TimesNewRoman-Italic": font_dir / "timesi.ttf",
        "TimesNewRoman-BoldItalic": font_dir / "timesbi.ttf",
    }
    if not all(path.exists() for path in fonts.values()):
        return
    for font_name, font_path in fonts.items():
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    pdfmetrics.registerFontFamily(
        "TimesNewRoman",
        normal="TimesNewRoman",
        bold="TimesNewRoman-Bold",
        italic="TimesNewRoman-Italic",
        boldItalic="TimesNewRoman-BoldItalic",
    )
    FONT_REGULAR = "TimesNewRoman"
    FONT_BOLD = "TimesNewRoman-Bold"
    FONT_ITALIC = "TimesNewRoman-Italic"


def _cover_logo(width: float = 138 * mm) -> RLImage | None:
    if not LOGO_PATH.exists():
        return None
    logo = RLImage(str(LOGO_PATH))
    aspect = logo.imageHeight / max(float(logo.imageWidth), 1.0)
    logo.drawWidth = width
    logo.drawHeight = width * aspect
    logo.hAlign = "CENTER"
    return logo


def _heading_band(flowable: Paragraph) -> KeepTogether:
    band_style = ParagraphStyle(
        "h1_band",
        parent=flowable.style,
        fontName=FONT_BOLD,
        fontSize=15,
        leading=18,
        textColor=colors.white,
        spaceBefore=0,
        spaceAfter=0,
        alignment=TA_LEFT,
    )
    band = Table(
        [[Paragraph(_escape(_plain_paragraph_text(flowable)), band_style)]],
        colWidths=[CONTENT_WIDTH],
        hAlign="LEFT",
    )
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), DOCPLUS_BLUE),
                ("BOX", (0, 0), (-1, -1), 0, DOCPLUS_BLUE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return KeepTogether([band, Spacer(1, 7)])


def build_complaint_report(
    complaint_state: GraphState,
    audit_state: GraphState | None = None,
    trace_state: GraphState | None = None,
    cybersecurity_state: GraphState | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> GeneratedReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at_dt = datetime.utcnow()
    generated_at = generated_at_dt.isoformat(timespec="seconds") + "Z"
    document_id = _document_id(complaint_state)
    filename = f"DocPlus_Complaint_CAPA_{document_id}.pdf"
    output_path = output_dir / filename
    output_path = _writable_pdf_path(output_path)
    filename = output_path.name

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"{PROJECT_NAME} Complaint Investigation Report",
        author=PROJECT_NAME,
        subject="Complaint investigation, evidence summary, CAPA, and audit corrective actions",
    )

    styles = _styles()
    story: list[Any] = []
    ids = _record_ids(complaint_state, generated_at)
    _cover(story, styles, complaint_state, document_id, generated_at)
    story.append(PageBreak())
    _table_of_contents(story, styles)
    story.append(PageBreak())
    _document_profile(story, styles, complaint_state, document_id, generated_at, ids)
    story.append(PageBreak())
    _regulatory_controls(story, styles, complaint_state)
    story.append(PageBreak())
    _executive_summary(story, styles, complaint_state, audit_state, trace_state)
    story.append(PageBreak())
    _complaint_investigation_record(story, styles, complaint_state, ids)
    story.append(PageBreak())
    _requirements_traceability_matrix(story, styles, complaint_state, audit_state, trace_state, ids)
    story.append(PageBreak())
    _dhf_index(story, styles, complaint_state, ids)
    story.append(PageBreak())
    _mdr_technical_documentation(story, styles, complaint_state, ids)
    story.append(PageBreak())
    story.append(Paragraph("8. Complaint Investigation Workflow and Execution Trace", styles["h1"]))
    _complaint_intake(story, styles, complaint_state)
    _investigation_chronology(story, styles, complaint_state, audit_state, trace_state, cybersecurity_state)
    story.append(PageBreak())
    _root_cause(story, styles, complaint_state)
    _root_cause_narrative(story, styles, complaint_state)
    story.append(PageBreak())
    _evidence(story, styles, complaint_state)
    story.append(PageBreak())
    _risk_and_capa(story, styles, complaint_state, cybersecurity_state)
    _capa_action_plan(story, styles, complaint_state, ids)
    story.append(PageBreak())
    _cybersecurity_sbom_narrative(story, styles, complaint_state, cybersecurity_state)
    story.append(PageBreak())
    _part11_controls(story, styles, complaint_state, ids)
    story.append(PageBreak())
    _audit_and_trace(story, styles, complaint_state, audit_state, trace_state)
    _audit_detail_appendix(story, styles, audit_state)
    _trace_detail_appendix(story, styles, trace_state)
    story.append(PageBreak())
    _cross_standard_rendering(story, styles, complaint_state, ids)
    story.append(PageBreak())
    _approval_block(story, styles, complaint_state, generated_at, ids)

    doc.build(_paginate_story(story), onFirstPage=_decorate_page, onLaterPages=_decorate_page)
    pages_estimate = 14
    return GeneratedReport(document_id, filename, output_path, generated_at, pages_estimate)


def report_summary(
    complaint_state: GraphState,
    audit_state: GraphState | None = None,
    trace_state: GraphState | None = None,
    cybersecurity_state: GraphState | None = None,
) -> dict[str, Any]:
    risk = complaint_state.risk_assessment
    complaint = complaint_state.structured_complaint
    device = complaint_state.graph_context.get("device", {})
    data_quality = _data_quality_summary(complaint_state)
    trace_ai_bundle = trace_ai.trace_summary(complaint_state, audit_state, trace_state, cybersecurity_state)
    return {
        "project": PROJECT_NAME,
        "device_id": complaint_state.device_id,
        "device_name": device.get("name", complaint_state.device_id),
        "current_firmware": device.get("current_firmware"),
        "regulatory_framework": complaint_state.regulatory_label or complaint_state.regulatory_framework,
        "complaint_severity": complaint.severity if complaint else None,
        "affected_component": complaint.affected_component_name if complaint else None,
        "risk_level": risk.risk_level if risk else None,
        "rpn": risk.rpn if risk else None,
        "reportable": risk.reportable if risk else None,
        "evidence_confidence": risk.confidence_in_evidence if risk else None,
        "evidence_confidence_score": risk.evidence_confidence_score if risk else None,
        "evidence_confidence_basis": risk.evidence_confidence_basis if risk else None,
        "evidence_class_breakdown": risk.evidence_class_breakdown if risk else {},
        "capa_closure_status": complaint_state.capa_closure_status,
        "capa_closure_tier": complaint_state.capa_closure_tier,
        "capa_closure_rationale": complaint_state.capa_closure_rationale,
        "evidence_items": len(_unique_evidence_items(complaint_state)),
        "audit_findings": len(audit_state.audit_findings) if audit_state else 0,
        "audit_findings_detail": (
            [
                {
                    "ids": finding["ids"],
                    "requirements": finding["requirements"],
                    "references": finding["references"],
                    "risk_level": finding["risk_level"],
                    "observation": finding["observation"],
                    "remediation": finding["remediation"],
                }
                for finding in _group_audit_findings(audit_state.audit_findings)
            ]
            if audit_state
            else []
        ),
        "trace_decay_alerts": len(trace_state.trace_decay_alerts) if trace_state else 0,
        "trace_decay_alerts_detail": (
            _group_trace_alerts(trace_state.trace_decay_alerts)
            if trace_state
            else []
        ),
        "sbom_components": len(cybersecurity_state.sbom_components) if cybersecurity_state else 0,
        "sbom_components_detail": (
            cybersecurity_state.sbom_components
            if cybersecurity_state
            else []
        ),
        "cybersecurity_findings": len(cybersecurity_state.cybersecurity_findings) if cybersecurity_state else 0,
        "cybersecurity_findings_detail": (
            cybersecurity_state.cybersecurity_findings
            if cybersecurity_state
            else []
        ),
        "cybersecurity_severity_rollup": (
            _cybersecurity_severity_rollup(cybersecurity_state.cybersecurity_findings)
            if cybersecurity_state
            else {}
        ),
        "trace_ai": trace_ai_bundle.get("summary", {}),
        "data_quality": data_quality,
        "status": complaint_state.status,
    }


def _styles() -> dict[str, ParagraphStyle]:
    _register_times_new_roman()
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=20,
            leading=25,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#102A43"),
            spaceAfter=6,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=11.2,
            leading=15.2,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#334E68"),
            spaceAfter=10,
        ),
        "cover_note": ParagraphStyle(
            "cover_note",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=10.2,
            leading=14.2,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#243B53"),
            spaceAfter=0,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName=FONT_BOLD,
            fontSize=15,
            leading=18.5,
            textColor=colors.HexColor("#102A43"),
            spaceBefore=8,
            spaceAfter=6,
            keepWithNext=1,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=11.4,
            leading=14.5,
            textColor=colors.HexColor("#243B53"),
            spaceBefore=6,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9.6,
            leading=13.2,
            textColor=colors.HexColor("#243B53"),
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "small",
            parent=base["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9.6,
            leading=13.2,
            textColor=colors.HexColor("#334E68"),
            alignment=TA_JUSTIFY,
        ),
        "table_header": ParagraphStyle(
            "table_header",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=10.4,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.0,
            leading=10.4,
            textColor=colors.HexColor("#243B53"),
        ),
        "badge": ParagraphStyle(
            "badge",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9.2,
            leading=12,
            textColor=colors.HexColor("#0B7285"),
            alignment=TA_CENTER,
        ),
    }


def _cover(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, document_id: str, generated_at: str) -> None:
    device = state.graph_context.get("device", {})
    story.append(Spacer(1, 8 * mm))
    logo = _cover_logo()
    if logo:
        story.append(logo)
        story.append(Spacer(1, 7 * mm))
    else:
        story.append(Paragraph(PROJECT_NAME, styles["cover_title"]))
        story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("Regulatory Complaint Investigation and CAPA Report", styles["cover_title"]))
    story.append(Paragraph("Confidential - AI Draft", styles["cover_subtitle"]))
    story.append(Spacer(1, 9 * mm))
    rows = [
        ["Document No.", document_id],
        ["Classification", "Confidential - AI Draft"],
        ["Generated", generated_at],
        ["Device", f"{device.get('name', state.device_id)} ({state.device_id})"],
        ["Firmware", str(device.get("current_firmware", "Not specified"))],
        ["Framework", state.regulatory_label or state.regulatory_framework],
        ["Prepared By", "DocPlus+ M4 Evidence and Documentation Layer"],
    ]
    cover_table = _kv_table(rows, styles, widths=[42 * mm, 104 * mm])
    cover_table.hAlign = "CENTER"
    story.append(cover_table)
    story.append(Spacer(1, 10 * mm))
    story.append(
        Paragraph(
            "This document was generated from live M1 graph facts and M2 agent outputs. "
            "Every summarized claim is tied to captured agent state, evidence citations, or audit findings.",
            styles["cover_note"],
        )
    )


def _table_of_contents(story: list[Any], styles: dict[str, ParagraphStyle]) -> None:
    story.append(Paragraph("Table of Contents", styles["h1"]))
    story.append(Spacer(1, 3 * mm))
    rows = [
        ["1", "Document Profile and Controlled Metadata"],
        ["2", "Regulatory Format Principles - ALCOA+ Framework"],
        ["3", "Executive Summary"],
        ["4", "Complaint Investigation Record"],
        ["5", "Requirements Traceability Matrix"],
        ["6", "Design History File Index"],
        ["7", "EU MDR Technical Documentation - Annex II / III"],
        ["8", "Complaint Investigation Workflow and Execution Trace"],
        ["9", "Root Cause Analysis"],
        ["10", "Evidence and Provenance"],
        ["11", "CAPA Report"],
        ["12", "Cybersecurity / SBOM Narrative"],
        ["13", "Electronic Records - 21 CFR Part 11"],
        ["14", "AuditShadow and Trace Decay"],
        ["15", "Cross-Standard Rendering Matrix"],
        ["16", "Document Control and Approval"],
    ]
    story.append(_table([["Section", "Title"], *rows], styles, widths=[28 * mm, 144 * mm]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "This output follows a formal regulatory-document pattern: controlled metadata first, "
        "then complaint facts, investigation logic, objective evidence, risk decision, CAPA plan, "
        "review gates, and approval placeholders.",
        styles["body"],
    ))


def _document_profile(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    document_id: str,
    generated_at: str,
    ids: dict[str, str],
) -> None:
    story.append(Paragraph("1. Document Profile and Controlled Metadata", styles["h1"]))
    device = state.graph_context.get("device", {})
    complaint = state.structured_complaint
    rows = [
        ["Document Title", "DocPlus+ Regulatory Complaint Investigation and CAPA Report"],
        ["Document Number", document_id],
        ["Revision", "A - AI Generated Draft"],
        ["Complaint ID", ids["complaint_id"]],
        ["CAPA ID", ids["capa_id"]],
        ["RTM ID", ids["rtm_id"]],
        ["Classification", "Confidential - AI Draft for quality and regulatory review"],
        ["Generated Timestamp", generated_at],
        ["System of Record", "DocPlus+ M4 Evidence and Documentation Layer"],
        ["Device", f"{device.get('name', state.device_id)} ({state.device_id})"],
        ["Current Firmware", str(device.get("current_firmware", "Not specified"))],
        ["Affected Component", complaint.affected_component_name if complaint else "Not classified"],
        ["Regulatory Framework", state.regulatory_label or state.regulatory_framework],
    ]
    story.append(_kv_table(rows, styles))
    _data_quality_section(story, styles, state)
    if _uses_simulated_evidence(state):
        story.append(Paragraph("Digital-Twin Prototype Disclaimer", styles["h2"]))
        story.append(Paragraph(_escape(_simulated_evidence_disclaimer(state)), styles["body"]))
    story.append(Paragraph("Document Purpose", styles["h2"]))
    story.append(Paragraph(
        "This report documents the complaint intake, AI-assisted investigation record, retrieved objective evidence, "
        "risk decision, CAPA recommendations, audit-style gaps, and corrective action controls. It is intended as a "
        "controlled draft that a Quality or Regulatory reviewer can verify, approve, or return for correction.",
        styles["body"],
    ))


def _complaint_investigation_record(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    ids: dict[str, str],
) -> None:
    story.append(Paragraph("4. Complaint Investigation Record", styles["h1"]))
    complaint = state.structured_complaint
    device = state.graph_context.get("device", {})
    risk = state.risk_assessment
    rows = [
        ["Complaint ID", ids["complaint_id"], "ISO 13485 8.2.2"],
        ["Date Received", ids["received_at"], "21 CFR Part 11"],
        ["Device UDI", ids["udi"], "EU MDR Article 27 / 21 CFR 830"],
        ["Model / Version", f"{_device_model(state)} / {_software_version(state)}", "IEC 62304 Section 8"],
        ["Complaint Source", ids["source"], "ISO 13485 8.2.2"],
        ["Complaint Narrative", _clean(state.raw_complaint, 260), "21 CFR 820 complaint file"],
        ["Patient Impact", ids["patient_impact"], "EU MDR Article 87"],
        ["MDR Reportable?", "Yes" if risk and risk.reportable else "No", "EU MDR Article 87 / 21 CFR 803"],
        ["CAPA Initiated?", f"Yes - {ids['capa_id']}", "21 CFR 820.100"],
        ["Affected Component", complaint.affected_component_name if complaint else "Not classified", "ISO 13485 8.5.2"],
        ["Closure Status", _closure_status_text(state), "ISO 13485 8.2.2"],
    ]
    story.append(Spacer(1, 1 * mm))
    story.append(_table([["Field", "Generated Value", "Regulatory Basis"], *rows], styles, widths=[39 * mm, 91 * mm, 42 * mm]))
    decision = _reportability_decision_rows(state)
    story.append(Spacer(1, 1 * mm))
    story.append(KeepTogether([
        Paragraph("Reportability Decision Logic", styles["h2"]),
        _table([["Condition", "DocPlus+ Assessment", "Action"], *decision], styles, widths=[44 * mm, 78 * mm, 50 * mm]),
    ]))
    _related_prior_complaints(story, styles, state)


def _requirements_traceability_matrix(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    audit_state: GraphState | None,
    trace_state: GraphState | None,
    ids: dict[str, str],
) -> None:
    story.append(Paragraph("5. Requirements Traceability Matrix", styles["h1"]))
    scope = state.investigation_scope or {}
    requirements = scope.get("requirements_scope", [])
    verification_rows = scope.get("verification_scope", [])
    trace_alerts = trace_state.trace_decay_alerts if trace_state else []
    audit_findings = audit_state.audit_findings if audit_state else []
    verifications_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in verification_rows:
        verifications_by_req[str(row.get("linked_requirement_id", ""))].append(row)
    audit_by_req: dict[str, list[str]] = defaultdict(list)
    for finding in audit_findings:
        audit_by_req[finding.requirement_id].append(finding.id)
    trace_by_req = {
        str(alert.get("requirement_id"))
        for alert in trace_alerts
        if alert.get("requirement_id")
    }

    rows: list[list[str]] = []
    for req in requirements:
        req_id = str(req.get("requirement_id", ""))
        linked_tests = verifications_by_req.get(req_id) or [{}]
        row_source_types = _row_source_types(req, linked_tests)
        evidence_classes = [
            normalize_evidence_class(item.get("evidence_class"))
            for item in linked_tests
            if item.get("evidence_class")
        ]
        evidence_class_text = _join_limited(
            [evidence_class_label(item) for item in evidence_classes] or ["Evidence class not assigned"],
            90,
        )
        statuses = [
            str(item.get("trace_decay_status") or req.get("evidence_status") or "candidate")
            for item in linked_tests
        ]
        selected_status = next((status for status in statuses if status != "current and usable"), statuses[0])
        if req_id in trace_by_req and selected_status == "current and usable":
            selected_status = "trace review"
        status_result = _join_limited(
            [str(item.get("result") or req.get("evidence_status") or "review") for item in linked_tests],
            60,
        )
        rows.append([
            req_id,
            _append_source_tag(_clean(str(req.get("requirement_text") or "Requirement text missing from M1 scope."), 150), row_source_types),
            _append_source_tag(
                _clean(
                    f"{str(req.get('link_strength') or 'scoped')} - {str(req.get('source_artifact') or 'M1 requirement scope')}",
                    95,
                ),
                row_source_types,
            ),
            _clean(str(req.get("relevance_reason") or "Complaint-scoped requirement."), 120),
            _append_source_tag(_clean(f"{str(req.get('evidence_status') or 'candidate')} - {evidence_class_text}", 120), row_source_types),
            _join_limited([str(item.get("test_case_id") or "missing") for item in linked_tests], 70),
            _clean(f"{selected_status} - {status_result}", 90),
        ])
        if len(rows) >= 12:
            break
    if not rows:
        story.append(Paragraph("No complaint-scoped requirements were available from M2.", styles["body"]))
        return
    story.append(_table(
        [[
            "Req ID",
            "Requirement",
            "Scope / Source",
            "Relevance",
            "Evidence",
            "Test Case",
            "Status / Result",
        ], *rows],
        styles,
        widths=[18 * mm, 40 * mm, 24 * mm, 29 * mm, 25 * mm, 18 * mm, 18 * mm],
    ))
    story.append(Paragraph("Trace Decay Risk Statement", styles["h2"]))
    story.append(Paragraph(
        "Rows marked STALE are not filler rows: they are generated from the live Trace Decay result and indicate "
        "verification links that require review before the device can claim current-firmware evidence.",
        styles["body"],
    ))


def _dhf_index(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, ids: dict[str, str]) -> None:
    story.append(Paragraph("6. Design History File Index", styles["h1"]))
    device = state.graph_context.get("device", {})
    issue = _complaint_issue_text(state, 90)
    affected = _affected_area_label(state)
    version = _software_version(state)
    elements = [
        ["1", "Planning", f"DDP-DP-{ids['hash']}", "Design plan for complaint-impact review", "Open"],
        ["2", "Design Input", f"URS-DP-{ids['hash']}", f"Complaint-linked requirement review for {issue}", "Draft"],
        ["3", "Design Output", f"SDD-DP-{ids['hash']}", f"{affected} design output references mapped", "Draft"],
        ["4", "Design Review", f"DRM-DP-{ids['hash']}", "Cross-functional review required", "Pending"],
        ["5", "Verification", f"VPR-DP-{ids['hash']}", f"Objective evidence required for {affected} on {version}", "Open"],
        ["6", "Validation", f"VAL-DP-{ids['hash']}", f"Use-condition validation required for {issue}", "Open"],
        ["7", "Design Transfer", f"DTR-DP-{ids['hash']}", "Release readiness blocked until evidence reconciled", "Pending"],
        ["8", "Design Changes", f"DCR-DP-{ids['hash']}", f"Change/service impact assessment linked to {affected}", "Open"],
        ["9", "DHF Index", f"DHFI-DP-{ids['hash']}", f"DocPlus+ index for {device.get('name', state.device_id)}", "Generated"],
    ]
    story.append(_table([["#", "Element", "Document ID", "Generated Unique Content", "Status"], *elements], styles, widths=[10 * mm, 30 * mm, 32 * mm, 78 * mm, 22 * mm]))
    story.append(Paragraph("Validation Gap Check", styles["h2"]))
    story.append(Paragraph(
        f"The validation section remains open because the complaint describes a user-reported condition involving {affected}. "
        "DocPlus+ therefore does not mark the DHF complete until validation, verification, or justified non-reproducibility evidence is linked.",
        styles["body"],
    ))


def _mdr_technical_documentation(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, ids: dict[str, str]) -> None:
    story.append(Paragraph("7. EU MDR Technical Documentation - Annex II / III", styles["h1"]))
    affected = _affected_area_label(state)
    issue = _complaint_issue_text(state, 110)
    risk = state.risk_assessment
    annex_rows = [
        ["Annex II Section 1", "Device Description", f"UDI {ids['udi']} scoped to complaint involving {affected}."],
        ["Annex II Section 3", "Design and Manufacturing Information", f"Design/change impact for {affected} is carried into design output review."],
        ["Annex II Section 4", "GSPR Compliance", f"Reported condition maps to safety/performance review: {issue}."],
        ["Annex II Section 5", "Benefit-Risk Analysis", f"{risk.risk_level if risk else 'Unscored'} risk complaint requires residual-risk review before closure."],
        ["Annex II Section 6.1", "Verification and Validation", "Objective verification evidence must be reconciled against the complaint scope."],
        ["Annex III Section 1.3", "PMS / PSUR Input", "Complaint is retained as a post-market signal for trend monitoring."],
        ["Annex III Section 1.5", "Vigilance Reporting", "Reportability remains open until human regulatory review confirms decision."],
    ]
    story.append(_table([["Annex Ref", "Section", "DocPlus+ Generated Content"], *annex_rows], styles, widths=[37 * mm, 45 * mm, 90 * mm]))
    story.append(Paragraph("Team NB Structure Check", styles["h2"]))
    story.append(Paragraph(
        "The package preserves a cohesive structure with section titles, metadata, and cross-references so that information "
        "is locatable by a reviewer rather than buried in a generic AI narrative.",
        styles["body"],
    ))
    story.append(Paragraph("Record Boundary", styles["h2"]))
    story.append(Paragraph(
        "The report is generated only from live application state: M1 graph context, M2 agent outputs, M4 evidence "
        "retrieval, AuditShadow findings, and Trace Decay alerts. It does not certify regulatory closure until the "
        "signature block is completed by authorized reviewers.",
        styles["body"],
    ))


def _regulatory_controls(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    story.append(Paragraph("2. Regulatory Format Controls", styles["h1"]))
    story.append(Paragraph("ALCOA+ Data Integrity Mapping", styles["h2"]))
    rows = [
        ["Attributable", "Prepared by DocPlus+ M4; generated timestamp and device scope are recorded."],
        ["Legible", "A4 PDF with selectable text, structured headings, repeatable tables, and page numbers."],
        ["Contemporaneous", "Generated at the time of complaint-report request from live agent state."],
        ["Original", "Marked as AI Draft until Quality and Regulatory reviewers approve."],
        ["Accurate", "Risk, CAPA, evidence, audit, and trace sections are populated from structured state."],
        ["Complete", "Includes intake, investigation, evidence, risk, CAPA, audit, trace, and approval fields."],
        ["Consistent", "Uses the same DocPlus+ controlled layout for all complaint-report exports."],
        ["Enduring", "Saved as a PDF artifact under the application output directory."],
        ["Available", "Downloadable through the M4 document endpoint and traceable by document number."],
    ]
    story.append(_table([["Principle", "DocPlus+ Implementation"], *rows], styles, widths=[38 * mm, 134 * mm]))
    story.append(Paragraph("Applicable Regulatory Basis", styles["h2"]))
    basis = [
        ["ISO 13485", "Complaint handling, CAPA discipline, documented information, traceable quality records."],
        ["21 CFR Part 820", "Design controls, complaint files, CAPA, and objective evidence expectations."],
        ["EU MDR", "Technical documentation, post-market surveillance, vigilance, and risk-benefit continuity."],
        ["21 CFR Part 11", "Electronic record controls, auditability, attribution, and durable retrieval."],
    ]
    story.append(_table([["Framework", "Relevance to This Report"], *basis], styles, widths=[38 * mm, 134 * mm]))


def _executive_summary(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    audit_state: GraphState | None,
    trace_state: GraphState | None,
) -> None:
    story.append(Paragraph("3. Executive Summary", styles["h1"]))
    summary = report_summary(state, audit_state, trace_state)
    rows = [
        ["Severity", summary.get("complaint_severity") or "Not classified"],
        ["Risk", f"{summary.get('risk_level') or 'Not scored'} / RPN {summary.get('rpn') or 'N/A'}"],
        ["Reportable", str(summary.get("reportable"))],
        ["Evidence Confidence", _risk_confidence_display(state)],
        ["Evidence Class Mix", _risk_evidence_breakdown_text(state)],
        ["Evidence Items", str(summary.get("evidence_items"))],
        ["Audit Findings", str(summary.get("audit_findings"))],
        ["Trace Decay Alerts", str(summary.get("trace_decay_alerts"))],
    ]
    story.append(_status_table(rows, styles))
    body = _clean(state.raw_complaint or "No raw complaint text captured.", 900)
    story.append(Paragraph(f"<b>Complaint:</b> {body}", styles["body"]))
    reasoning = (state.ai_reasoning.get("complaint") or {}).get("executive_summary")
    if reasoning:
        story.append(Paragraph(f"<b>Agent conclusion:</b> {_clean(str(reasoning), 1200)}", styles["body"]))
    if state.risk_assessment:
        story.append(Paragraph("Risk Evidence Confidence Basis", styles["h2"]))
        story.append(Paragraph(_escape(_risk_confidence_basis_text(state)), styles["body"]))
        drivers = _risk_evidence_drivers_text(state)
        if drivers:
            story.append(Paragraph(_escape(drivers), styles["small"]))
    story.append(Paragraph("Quality Review Decision", styles["h2"]))
    story.append(Paragraph(
        "The complaint should remain open until objective evidence is reconciled, CAPA ownership is assigned, "
        "and any major or critical audit findings affecting the complaint scope are remediated or justified.",
        styles["body"],
    ))


def _complaint_intake(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    story.append(Paragraph("8.1 Structured Complaint Intake Detail", styles["h2"]))
    complaint = state.structured_complaint
    if not complaint:
        story.append(Paragraph("No structured complaint object was produced.", styles["body"]))
        return
    rows = [
        ["Device ID", complaint.device_id],
        ["Affected Component", f"{complaint.affected_component_name} ({complaint.affected_component})"],
        ["Severity", complaint.severity],
        ["Timeline", complaint.timeline],
        ["Symptom Codes", ", ".join(complaint.symptom_codes[:8])],
        ["Match Terms", ", ".join(complaint.component_match_terms[:8]) or "Not captured"],
        ["Similar Prior Complaints", str(len(_similar_incidents(state)))],
    ]
    story.append(_kv_table(rows, styles))
    story.append(Paragraph("Complaint Narrative", styles["h2"]))
    story.append(Paragraph(_clean(complaint.raw_summary or state.raw_complaint, 1400), styles["body"]))
    _related_prior_complaints(story, styles, state)


def _investigation_chronology(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    audit_state: GraphState | None = None,
    trace_state: GraphState | None = None,
    cybersecurity_state: GraphState | None = None,
) -> None:
    story.append(Paragraph("8.2 Investigation Chronology", styles["h2"]))
    data = [["Time", "Agent", "Action", "Status", "Detail"]]
    for event in state.trace[:18]:
        data.append([
            _clean(event.timestamp, 34),
            _clean(event.agent, 42),
            _clean(event.action, 96),
            event.status,
            _event_detail_text(event.data),
        ])
    story.append(_table(data, styles, widths=[32 * mm, 29 * mm, 47 * mm, 20 * mm, 44 * mm]))
    story.append(Paragraph("Chronology Interpretation", styles["h2"]))
    story.append(Paragraph(
        "The sequence confirms the complaint passed through intake, root-cause hypothesis generation, firmware traceability ripple checking, "
        "evidence retrieval, risk scoring, CAPA drafting, and final reasoning. This preserves a reviewer-readable process trail for the AI "
        "investigation rather than presenting the CAPA as an unsupported narrative.",
        styles["body"],
    ))
    _trace_ai_observability(story, styles, state, audit_state, trace_state, cybersecurity_state)


def _trace_ai_observability(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    audit_state: GraphState | None = None,
    trace_state: GraphState | None = None,
    cybersecurity_state: GraphState | None = None,
) -> None:
    bundle = trace_ai.trace_summary(state, audit_state, trace_state, cybersecurity_state)
    summary = bundle.get("summary", {})
    steps = bundle.get("steps", [])
    llm_calls = bundle.get("llm_calls", [])
    tool_calls = bundle.get("tool_calls", [])
    aggregate_rows = [
        ["Total wall-clock time", f"{summary.get('total_wall_clock_ms', 0)} ms"],
        ["Agent steps", str(summary.get("agent_step_count", 0))],
        ["LLM calls", str(summary.get("llm_call_count", 0))],
        ["Tool/retrieval calls", str(summary.get("tool_call_count", 0))],
        ["Tokens", f"{summary.get('total_tokens', 0)} total ({summary.get('prompt_tokens', 0)} prompt / {summary.get('completion_tokens', 0)} completion)"],
        ["Longest agent", f"{summary.get('longest_agent') or 'N/A'} ({summary.get('longest_agent_elapsed_ms', 0)} ms)"],
    ]
    story.append(KeepTogether([
        Paragraph("Trace AI - Pipeline Observability", styles["h2"]),
        Paragraph(
            "Trace AI is operational engineering telemetry for this report generation. It records pipeline execution, LLM metadata, "
            "and retrieval/tool metadata only. It is not clinical evidence, not regulatory objective evidence, and not the same as "
            "Trace Decay or the Firmware Traceability Ripple Check.",
            styles["body"],
        ),
        _kv_table(aggregate_rows, styles, widths=[48 * mm, 124 * mm]),
    ]))

    if steps:
        step_rows = [["Step ID", "Agent", "Action", "Status", "Elapsed", "Sub-events"]]
        for step in steps[:16]:
            subevents = f"LLM {len(step.get('llm_call_ids', []))}; tools {len(step.get('tool_call_ids', []))}"
            step_rows.append(
                [
                    _clean(str(step.get("step_id", "")), 22),
                    _clean(str(step.get("agent", "")), 32),
                    _clean(str(step.get("action", "")), 70),
                    _clean(str(step.get("status", "")), 16),
                    f"{step.get('elapsed_ms', 0)} ms",
                    subevents,
                ]
            )
        story.append(Paragraph("Agent Step Trace", styles["h2"]))
        story.append(_table(step_rows, styles, widths=[24 * mm, 27 * mm, 49 * mm, 20 * mm, 24 * mm, 28 * mm]))
        if len(steps) > 16:
            story.append(Paragraph(f"{len(steps) - 16} additional agent step(s) are retained in the machine-readable Trace AI summary.", styles["body"]))

    story.append(Paragraph("LLM Call Metadata", styles["h2"]))
    if llm_calls:
        llm_rows = [["Call ID", "Agent", "Task", "Model", "Status", "Tokens", "Latency"]]
        for call in llm_calls[:12]:
            tokens = _llm_token_text(call)
            llm_rows.append(
                [
                    _clean(str(call.get("call_id", "")), 18),
                    _clean(str(call.get("agent", "")), 28),
                    _clean(str(call.get("task", "")), 42),
                    _clean(str(call.get("model", "")), 34),
                    _clean(str(call.get("status", "")), 14),
                    tokens,
                    f"{call.get('latency_ms', 0)} ms",
                ]
            )
        story.append(_table(llm_rows, styles, widths=[22 * mm, 25 * mm, 38 * mm, 32 * mm, 18 * mm, 20 * mm, 17 * mm]))
        if len(llm_calls) > 12:
            story.append(Paragraph(f"{len(llm_calls) - 12} additional LLM call(s) are retained in structured Trace AI data.", styles["body"]))
    else:
        story.append(Paragraph("No live LLM API call metadata was captured for this run. Deterministic fallback may have been used or OpenAI may have been disabled.", styles["body"]))

    story.append(Paragraph("Tool and Retrieval Call Metadata", styles["h2"]))
    if tool_calls:
        tool_rows = [["Call ID", "Agent", "Tool", "Query / Target", "Results", "Latency"]]
        for call in tool_calls[:20]:
            tool_rows.append(
                [
                    _clean(str(call.get("call_id", "")), 18),
                    _clean(str(call.get("agent", "")), 28),
                    _clean(str(call.get("tool", "")), 38),
                    _clean(str(call.get("query", "")), 74),
                    str(call.get("result_count") if call.get("result_count") is not None else ""),
                    f"{call.get('latency_ms', 0)} ms",
                ]
            )
        story.append(_table(tool_rows, styles, widths=[22 * mm, 26 * mm, 34 * mm, 58 * mm, 14 * mm, 18 * mm]))
        if len(tool_calls) > 20:
            story.append(Paragraph(f"{len(tool_calls) - 20} additional tool/retrieval call(s) are retained in structured Trace AI data.", styles["body"]))
    else:
        story.append(Paragraph("No graph, vector, or NVD retrieval calls were captured for this run.", styles["body"]))


def _llm_token_text(call: dict[str, Any]) -> str:
    total = call.get("total_tokens")
    prompt = call.get("prompt_tokens")
    completion = call.get("completion_tokens")
    if total is None and prompt is None and completion is None:
        return "not returned"
    return f"{total or 0} ({prompt or 0}/{completion or 0})"


def _root_cause(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    story.append(Paragraph("9. Root Cause Analysis", styles["h1"]))
    if not state.hypotheses:
        story.append(Paragraph("No hypotheses were generated.", styles["body"]))
        return
    data = [["ID", "Hypothesis", "Probability", "Affected Area", "Key Why Chain"]]
    for item in state.hypotheses[:4]:
        source_types = getattr(item, "source_types", []) or []
        data.append(
            [
                item.id,
                _append_source_tag(_clean(item.title, 130), source_types),
                f"{round(item.base_probability * 100)}%",
                _clean(item.affected_component, 90),
                _append_source_tag(_clean(" | ".join(item.why_chain[:3]) or item.description, 260), source_types),
            ]
        )
    story.append(_table(data, styles, widths=[18 * mm, 39 * mm, 22 * mm, 32 * mm, 61 * mm]))
    story.append(Spacer(1, 4 * mm))
    for item in state.hypotheses[:4]:
        story.append(Paragraph(f"{_escape(item.id)} Evidence Balance", styles["h2"]))
        detail_rows = [
            [
                "Evidence For",
                _append_source_tag(_clean(" | ".join(getattr(item, "evidence_for", []) or ["No supporting evidence listed."]), 520), getattr(item, "source_types", []) or []),
            ],
            [
                "Evidence Against / Gaps",
                _append_source_tag(_clean(" | ".join(getattr(item, "evidence_against", []) or ["No contradicting evidence listed."]), 520), getattr(item, "source_types", []) or []),
            ],
            [
                "Similar Incident Cross-Check",
                _append_source_tag(_clean(" | ".join(getattr(item, "similar_incident_analysis", []) or ["No similar-incident analysis listed."]), 520), getattr(item, "source_types", []) or []),
            ],
            [
                "Probability Rationale",
                _append_source_tag(_clean(getattr(item, "probability_rationale", "") or "No probability rationale listed.", 360), getattr(item, "source_types", []) or []),
            ],
        ]
        story.append(_table(detail_rows, styles, widths=[42 * mm, 130 * mm]))


def _root_cause_narrative(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    reasoning = state.ai_reasoning.get("complaint") or {}
    story.append(Paragraph("Root Cause Rationale", styles["h2"]))
    lines = reasoning.get("root_cause_reasoning") or []
    if lines:
        for index, line in enumerate(lines[:8], start=1):
            story.append(Paragraph(f"<b>{index}.</b> {_clean(str(line), 700)}", styles["body"]))
    else:
        for item in state.hypotheses:
            source_types = getattr(item, "source_types", []) or []
            story.append(
                Paragraph(
                    f"<b>{item.id} - {_escape(_clean(item.title, 120))}:</b> "
                    f"{_escape(_append_source_tag(_clean(item.description, 700), source_types))}",
                    styles["body"],
                )
            )
    story.append(Paragraph("Primary Root Cause Position", styles["h2"]))
    story.append(Paragraph(
        f"For this generated draft, the leading position is that the complaint is associated with {_affected_area_label(state)} "
        "and the evidence/control set tied to the reported condition. The position remains provisional until engineering review "
        "confirms or rules out the suspected cause with objective evidence.",
        styles["body"],
    ))


def _evidence(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    story.append(Paragraph("10. Evidence and Provenance", styles["h1"]))
    evidence_items = _unique_evidence_items(state)
    controlled_or_candidate_items = [
        item
        for item in evidence_items
        if getattr(item, "evidence_class", "") != EvidenceClass.SIMULATED.value
    ]
    if not evidence_items and not state.digital_twin_results:
        story.append(Paragraph("No evidence was retrieved for this run.", styles["body"]))
        return
    if controlled_or_candidate_items:
        data = [["Source", "Evidence Class", "Support", "Conf.", "Excerpt", "Citation"]]
        for item in controlled_or_candidate_items[:6]:
            source_types = [getattr(item, "source_type", SourceType.INFERRED.value)]
            data.append(
                [
                    _append_source_tag(_clean(item.source, 140), source_types),
                    evidence_class_label(getattr(item, "evidence_class", "candidate")),
                    "Yes" if item.supports else "No",
                    f"{round(item.confidence * 100)}%",
                    _append_source_tag(_clean(item.snippet, 280), source_types),
                    _clean(item.citation, 160),
                ]
            )
        story.append(_table(data, styles, widths=[29 * mm, 32 * mm, 19 * mm, 17 * mm, 47 * mm, 28 * mm]))
    else:
        story.append(Paragraph("No controlled or extracted evidence was retrieved before firmware traceability signals.", styles["body"]))
    _firmware_traceability_ripple_check(story, styles, state)


def _firmware_traceability_ripple_check(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    if not state.digital_twin_results:
        return
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("10.1 Firmware Traceability Ripple Check", styles["h2"]))
    story.append(
        Paragraph(
            "This section reports a firmware-traceability check: for each root-cause hypothesis, it examines whether "
            "linked verification test records match the complaint or candidate firmware version, or are stale/mismatched. "
            "This is a graph-based traceability signal, not a behavioral model or physical test of the device. It identifies "
            "where verification evidence may be out of date, not what the device would actually do under the reported failure condition.",
            styles["body"],
        )
    )
    data = [["Hypothesis", "Analysis Type", "Result", "Interpretation", "Evidence Class"]]
    for item in state.digital_twin_results[:8]:
        data.append(
            [
                _clean(str(item.get("hypothesis_id") or "general"), 36),
                _firmware_traceability_check_type(item),
                _clean(str(item.get("simulated_result") or item.get("status") or ""), 32),
                _clean(str(item.get("interpretation") or ""), 260),
                evidence_class_label(item.get("evidence_class", EvidenceClass.SIMULATED.value)),
            ]
        )
    story.append(_table(data, styles, widths=[24 * mm, 34 * mm, 20 * mm, 69 * mm, 25 * mm]))


def _firmware_traceability_check_type(item: dict[str, Any]) -> str:
    capability = str(item.get("capability") or "")
    required = str(item.get("required_scenario_type") or "")
    if capability == "firmware_ripple_graph_twin":
        return "Firmware traceability ripple check"
    if capability.startswith("unsupported_by_current_twin"):
        return _clean(f"Not applicable - {required or 'outside firmware traceability scope'}", 54)
    return _clean(capability.replace("digital_twin", "graph").replace("current_twin", "traceability_check"), 54)


def _unique_evidence_items(state: GraphState) -> list[Any]:
    seen: set[tuple[str, str]] = set()
    unique: list[Any] = []
    for item in state.evidence_collected:
        key = (str(item.id), str(item.source))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _data_quality_section(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    summary = _data_quality_summary(state)
    story.append(Paragraph("Data Quality and Source Provenance", styles["h2"]))
    counts = summary["counts"]
    count_text = ", ".join(
        f"{counts.get(source_type.value, 0)} {source_type.value}"
        for source_type in SourceType
        if counts.get(source_type.value, 0)
    ) or "no graph facts counted"
    story.append(
        Paragraph(
            f"This report references {summary['total']} graph fact(s): {count_text}.",
            styles["body"],
        )
    )
    if counts.get(SourceType.SYNTHETIC.value, 0) or counts.get(SourceType.INFERRED.value, 0):
        story.append(
            Paragraph(
                "Uncontrolled graph facts appear in this report and are visibly tagged. "
                "[DEMO DATA - NOT CONTROLLED EVIDENCE] means the item cannot support CAPA closure without controlled verification. "
                "[INFERRED DATA - REVIEW REQUIRED] means the item was inferred and requires reviewer confirmation before final use.",
                styles["body"],
            )
        )
    rows = [["Source Type", "Referenced Fact Count"]]
    rows.extend([source_type.value, str(counts.get(source_type.value, 0))] for source_type in SourceType)
    story.append(_table(rows, styles, widths=[60 * mm, 112 * mm]))


def _data_quality_summary(state: GraphState) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    facts = _referenced_source_facts(state)
    for fact in facts:
        counts[normalize_source_type(fact.get("source_type"), SourceType.INFERRED)] += 1
    return {"total": len(facts), "counts": dict(counts), "facts": facts}


def _referenced_source_facts(state: GraphState) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(node_id: Any, source_type: Any, label: str = "") -> None:
        normalized_id = str(node_id or "").strip()
        if not normalized_id:
            return
        key = f"{normalized_id}|{label}"
        if key in seen:
            return
        seen.add(key)
        facts.append(
            {
                "node_id": normalized_id,
                "source_type": normalize_source_type(source_type, SourceType.INFERRED),
                "label": label,
            }
        )

    device = state.graph_context.get("device", {})
    add(device.get("id") or state.device_id, device.get("source_type", SourceType.INFERRED.value), "device")
    complaint = state.structured_complaint
    if complaint:
        add(complaint.affected_component, _component_source_type(state, complaint.affected_component), "affected_component")
    for incident in _similar_incidents(state):
        add(incident.get("id"), incident.get("source_type"), "similar_incident")
    for hypothesis in state.hypotheses:
        for fact in getattr(hypothesis, "supporting_facts", []) or []:
            add(fact.get("node_id"), fact.get("source_type"), "root_cause_fact")
    for item in state.evidence_collected:
        add(item.source_node_id or item.id, item.source_type, "evidence")
    for req in state.investigation_scope.get("requirements_scope", []):
        add(req.get("requirement_id"), req.get("source_type"), "requirement_scope")
    for row in state.investigation_scope.get("verification_scope", []):
        add(row.get("test_case_id") or row.get("linked_requirement_id"), row.get("source_type"), "verification_scope")
    return facts


def _component_source_type(state: GraphState, component_id: str) -> str:
    for component in state.graph_context.get("components", []):
        if component.get("id") == component_id:
            return normalize_source_type(component.get("source_type"), SourceType.INFERRED)
    return SourceType.INFERRED.value


def _similar_incidents(state: GraphState) -> list[dict[str, Any]]:
    complaint = state.structured_complaint
    if complaint and complaint.similar_incidents:
        return complaint.similar_incidents
    return state.similar_incidents or []


def _related_prior_complaints(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState) -> None:
    incidents = _similar_incidents(state)
    if not incidents:
        story.append(Paragraph("Related Prior Complaints", styles["h2"]))
        story.append(Paragraph("No similar prior complaints found in current graph.", styles["body"]))
        return
    rows = [["Complaint ID", "Similarity", "Summary", "Source"]]
    for item in incidents[:5]:
        source_type = normalize_source_type(item.get("source_type"), SourceType.INFERRED)
        summary = item.get("summary") or item.get("description") or item.get("title") or "Prior complaint summary unavailable."
        rows.append(
            [
                str(item.get("id", "N/A")),
                str(item.get("score", "matched")),
                _append_source_tag(_clean(str(summary), 220), [source_type]),
                f"{source_type} {_source_tag_for_values([source_type])}".strip(),
            ]
        )
    story.append(KeepTogether([
        Paragraph("Related Prior Complaints", styles["h2"]),
        _table(rows, styles, widths=[32 * mm, 24 * mm, 84 * mm, 32 * mm]),
    ]))


def _row_source_types(req: dict[str, Any], linked_tests: list[dict[str, Any]]) -> list[str]:
    values = [req.get("source_type")]
    for item in linked_tests:
        values.append(item.get("source_type"))
        values.extend(item.get("source_types") or [])
    return [normalize_source_type(value, SourceType.INFERRED) for value in values if value]


def _source_tag_for_values(values: Any) -> str:
    if isinstance(values, str):
        values = [values]
    source_types = [normalize_source_type(value, SourceType.INFERRED) for value in (values or [])]
    if SourceType.SYNTHETIC.value in source_types:
        return source_type_report_tag(SourceType.SYNTHETIC.value)
    if SourceType.INFERRED.value in source_types:
        return source_type_report_tag(SourceType.INFERRED.value)
    return ""


def _append_source_tag(text: str, source_types: Any) -> str:
    tag = _source_tag_for_values(source_types)
    if not tag or tag in text:
        return text
    return f"{text} {tag}"


def _risk_and_capa(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    state: GraphState,
    cybersecurity_state: GraphState | None = None,
) -> None:
    story.append(Paragraph("11. CAPA Report", styles["h1"]))
    device = state.graph_context.get("device", {})
    complaint = state.structured_complaint
    summary_rows = [
        ["Product", str(device.get("name") or state.device_id or "Pulse oximeter")],
        ["Software Version", str(device.get("current_firmware") or "Under review")],
        ["CAPA Status", state.capa_closure_status or "Open - complaint risk is identified; closure evidence is not yet complete"],
        ["Priority", complaint.severity if complaint else "High"],
        ["Risk Classification", _plain_risk_classification(state)],
        ["Evidence Confidence", _risk_confidence_display(state)],
        ["Evidence Class Mix", _risk_evidence_breakdown_text(state)],
    ]
    story.append(_kv_table(summary_rows, styles, widths=[42 * mm, 130 * mm]))
    if state.risk_assessment:
        story.append(Paragraph("Risk Evidence Confidence Basis", styles["h2"]))
        story.append(Paragraph(_escape(_risk_confidence_basis_text(state)), styles["body"]))
    cyber_note = _cybersecurity_risk_note(state, cybersecurity_state)
    if cyber_note:
        story.append(Paragraph("Cybersecurity Context - Informational Only", styles["h2"]))
        story.append(Paragraph(_escape(cyber_note), styles["body"]))
    gate_text = _capa_gate_text(state)
    if gate_text:
        story.append(Paragraph(_capa_gate_heading(state), styles["h2"]))
        story.append(
            Paragraph(
                _escape(gate_text),
                styles["body"],
            )
        )
    entries = _capa_plan_entries(state)
    if not entries:
        story.append(Paragraph("No CAPA narrative was drafted for this run.", styles["body"]))
        return

    for title in [
        "Problem Statement",
        "Complaint Summary",
        "Immediate Containment Actions",
        "Investigation",
        "Root Cause Analysis",
        "Why CAPA Remains Open",
        "Risk Assessment",
        "Corrective Actions",
        "Preventive Actions",
        "Verification of Effectiveness",
        "CAPA Effectiveness Criteria",
        "Lessons Learned",
        "CAPA Closure",
    ]:
        entry = _capa_entry(entries, title)
        if entry:
            _append_capa_entry(story, styles, entry, max_len=1500, show_citation=False)
    story.append(Paragraph(
        "Technical evidence identifiers, requirement IDs, audit findings, and source citations are retained in the evidence and audit appendices. "
        "They are not repeated here so the CAPA remains readable for Quality, Engineering, Regulatory, and non-technical reviewers.",
        styles["small"],
    ))


def _capa_action_plan(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, ids: dict[str, str]) -> None:
    story.append(Paragraph("11.1 CAPA Action Plan", styles["h1"]))
    risk_scope = _capa_risk_scope(state)
    affected = _affected_area_label(state)
    closure_focus = _closure_focus_text(state)
    containment = _first_text(risk_scope.get("containment_actions")) or (
        f"Preserve complaint evidence for {affected}, confirm configuration and traceability, screen similar complaints, and pause any wider decision until safety impact is assessed."
    )
    corrective = _first_text(risk_scope.get("corrective_actions")) or (
        f"Reproduce or evaluate the reported condition for {affected}, correct the confirmed cause or document non-reproducibility, and verify the corrected behavior."
    )
    preventive = _first_text(risk_scope.get("preventive_actions")) or (
        f"Add required review or regression coverage for future changes affecting {affected}."
    )
    effectiveness = _first_text(risk_scope.get("effectiveness_checks")) or (
        "Confirm the issue does not recur in testing or complaint monitoring and obtain independent Quality approval."
    )
    rows = [
        ["CAPA Record", ids["capa_id"]],
        ["Complaint Reference", ids["complaint_id"]],
        ["CAPA Type", "Corrective and preventive action"],
        ["Current Status", state.capa_closure_status or "Open - the issue requires action, but root-cause proof, completed fix evidence, effectiveness testing, and Quality/Regulatory approval are not yet complete."],
    ]
    if _capa_gate_text(state):
        rows.append([_capa_gate_heading(state), _capa_gate_text(state)])
    story.append(_kv_table(rows, styles, widths=[42 * mm, 130 * mm]))

    story.append(Paragraph("Action Plan", styles["h2"]))
    action_proofs = _capa_action_proof_texts(state)
    action_rows = [
        [
            "Immediate measures",
            "Quality / Support",
            containment,
            action_proofs["containment"],
        ],
        [
            "Fix the issue",
            _engineering_owner_text(state),
            corrective,
            action_proofs["corrective"],
        ],
        [
            "Prevent recurrence",
            "Engineering / Quality",
            preventive,
            action_proofs["preventive"],
        ],
        [
            "Check effectiveness",
            "Quality Assurance",
            effectiveness,
            action_proofs["effectiveness"],
        ],
    ]
    story.append(_table([["Purpose", "Owner", "Required Action", "How Closure Is Proven"], *action_rows], styles, widths=[28 * mm, 34 * mm, 72 * mm, 38 * mm]))

    story.append(Paragraph("Acceptance Criteria for Closure", styles["h2"]))
    close_rows = _capa_acceptance_rows(state, closure_focus)
    story.append(_table([["Closure Gate", "Required Evidence"], *close_rows], styles, widths=[42 * mm, 130 * mm]))


def _capa_risk_scope(state: GraphState) -> dict[str, Any]:
    scope = state.investigation_scope.get("risk_and_capa_scope", {})
    return scope if isinstance(scope, dict) else {}


def _capa_plan_entries(state: GraphState) -> list[dict[str, str]]:
    risk_scope = _capa_risk_scope(state)
    raw_entries = risk_scope.get("professional_capa_plan")
    entries: list[dict[str, str]] = []
    if isinstance(raw_entries, list):
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("section") or "").strip()
            narrative = str(entry.get("narrative") or "").strip()
            if title and narrative:
                entries.append(
                    {
                        "section": title,
                        "narrative": narrative,
                        "citation": str(entry.get("citation") or "").strip(),
                        "source_types": entry.get("source_types") or [],
                        "evidence_classes": entry.get("evidence_classes") or [],
                    }
                )
    if entries:
        return entries
    return [
        {
            "section": section.title,
            "narrative": section.body,
            "citation": section.citation,
            "source_types": section.source_types,
            "evidence_classes": section.evidence_classes,
        }
        for section in state.capa_sections
        if section.title and section.body
    ]


def _capa_entry(entries: list[dict[str, str]], title: str) -> dict[str, str] | None:
    wanted = title.lower()
    for entry in entries:
        if entry.get("section", "").lower() == wanted:
            return entry
    return None


def _append_capa_entry(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    entry: dict[str, str],
    max_len: int = 1400,
    show_citation: bool = True,
) -> None:
    narrative = _append_source_tag(_clean(entry.get("narrative", ""), max_len), entry.get("source_types") or [])
    evidence_classes = [
        evidence_class_label(item)
        for item in entry.get("evidence_classes", [])
        if item
    ]
    flowables: list[Any] = [
        Paragraph(f"<b>{_escape(entry.get('section', 'CAPA Section'))}</b>", styles["body"]),
        Paragraph(_escape(narrative), styles["body"]),
    ]
    if evidence_classes:
        flowables.append(
            Paragraph(
                "Evidence class gate: " + _escape(_join_limited(evidence_classes, 220)),
                styles["small"],
            )
        )
    citation = entry.get("citation")
    if citation and show_citation:
        flowables.append(Paragraph(f"Evidence reference: {_escape(_clean(citation, 220))}", styles["small"]))
    story.append(KeepTogether(flowables))


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _plain_risk_classification(state: GraphState) -> str:
    risk = state.risk_assessment
    if not risk:
        return f"Under review - potential impact to {_affected_area_label(state)}"
    if str(risk.risk_level).lower() in {"high", "critical"} or risk.reportable:
        return f"Major - potential impact to {_affected_area_label(state)}"
    if str(risk.risk_level).lower() == "medium":
        return "Moderate - complaint requires corrective action review"
    return "Low - monitor through complaint and quality review"


def _uses_simulated_evidence(state: GraphState) -> bool:
    if state.capa_closure_tier == "simulated_evidence_approval":
        return True
    return any(getattr(item, "evidence_class", "") == EvidenceClass.SIMULATED.value for item in state.evidence_collected)


def _simulated_evidence_disclaimer(state: GraphState) -> str:
    return (
        state.capa_closure_disclaimer
        or "Hackathon prototype disclaimer: this AI-generated investigation may include digital-twin or firmware-traceability simulation, "
        "not real hardware verification. Simulated results may support investigation triage, but they require formal human engineering "
        "approval before any real-world quality, regulatory, release, or closure reliance."
    )


def _capa_gate_heading(state: GraphState) -> str:
    if state.capa_closure_tier == "simulated_evidence_approval":
        return "Engineering Approval Gate"
    if state.capa_closure_tier == "closed_controlled_verification":
        return "Controlled Closure Gate"
    return "Insufficient Evidence Gate"


def _capa_gate_text(state: GraphState) -> str:
    if state.capa_closure_tier == "closed_controlled_verification":
        return state.capa_closure_rationale or "The scoped evidence chain is entirely current controlled verification evidence."
    if state.capa_closure_tier == "simulated_evidence_approval":
        parts = [
            state.capa_closure_rationale,
            state.capa_closure_required_action,
            _simulated_evidence_disclaimer(state),
        ]
        return " ".join(part for part in parts if part)
    if state.closure_blocked_reason:
        return (
            "This CAPA remains open for insufficient evidence. "
            + state.closure_blocked_reason
            + (" " + state.capa_closure_required_action if state.capa_closure_required_action else "")
        )
    return ""


def _capa_action_proof_texts(state: GraphState) -> dict[str, str]:
    if state.capa_closure_tier == "closed_controlled_verification":
        return {
            "containment": "Evidence is preserved and the controlled closure package remains traceable.",
            "corrective": "The confirmed cause is fixed or ruled out with current controlled verification evidence.",
            "preventive": "Future changes or service decisions retain the approved controls before release.",
            "effectiveness": "Current controlled verification and monitoring support closure.",
        }
    if state.capa_closure_tier == "simulated_evidence_approval":
        return {
            "containment": "Evidence is preserved and simulated findings are available for Engineering review.",
            "corrective": "Engineering approves the digital-twin/simulated finding or records that physical verification is required.",
            "preventive": "Future changes include Engineering review of any simulated evidence before approval.",
            "effectiveness": "Closure is proven by Engineering sign-off on the simulated support plus Quality/Regulatory approval.",
        }
    return {
        "containment": "Evidence is preserved, affected users are protected, and deployment risk is understood.",
        "corrective": "The confirmed cause is fixed or ruled out with approved objective evidence.",
        "preventive": "Future changes or service decisions include controls for this failure mode before approval.",
        "effectiveness": "No repeat issue is seen after targeted verification and evidence gaps are closed.",
    }


def _capa_acceptance_rows(state: GraphState, closure_focus: str) -> list[list[str]]:
    if state.capa_closure_tier == "closed_controlled_verification":
        return [
            ["Reported condition", "The reported condition is corrected or ruled out in the controlled closure package."],
            ["Affected function", f"{closure_focus.capitalize()} is verified by current controlled evidence."],
            ["Verification", "All scoped evidence items are controlled current verification records."],
            ["Complaint monitoring", "No related recurrence is observed during the defined follow-up period."],
            ["Approval", "Quality and Regulatory reviewers maintain the approved completed CAPA record."],
        ]
    if state.capa_closure_tier == "simulated_evidence_approval":
        return [
            ["Reported condition", "The reported condition is supported by controlled records and simulated investigation findings."],
            ["Affected function", f"{closure_focus.capitalize()} is covered by the scoped controlled and simulated evidence chain."],
            ["Engineering review", "Engineering formally approves the digital-twin or firmware-traceability simulation, or requires physical verification."],
            ["Prototype disclaimer", _simulated_evidence_disclaimer(state)],
            ["Approval", "Engineering, Quality, and Regulatory reviewers approve the completed CAPA record before closure."],
        ]
    return [
        ["Reported condition", "The reported condition is reproduced and corrected, or formally ruled out with approved evidence."],
        ["Affected function", f"{closure_focus.capitalize()} is verified against the approved requirement or acceptance criterion."],
        ["Verification", "Missing, candidate, historical, synthetic, or no-evidence items are replaced by approved objective evidence."],
        ["Complaint monitoring", "No related recurrence is observed during the defined follow-up period."],
        ["Approval", "Quality and Regulatory reviewers approve the completed CAPA record."],
    ]


def _risk_confidence_display(state: GraphState) -> str:
    risk = state.risk_assessment
    if not risk:
        return "Not assessed"
    label = str(risk.confidence_in_evidence or "not assessed")
    return f"{label} (score {float(getattr(risk, 'evidence_confidence_score', 0.0)):.2f})"


def _risk_evidence_breakdown_text(state: GraphState) -> str:
    risk = state.risk_assessment
    if not risk:
        return "Not assessed"
    breakdown = getattr(risk, "evidence_class_breakdown", {}) or {}
    if not isinstance(breakdown, dict) or not breakdown:
        classes = getattr(risk, "evidence_classes", []) or []
        if not classes:
            return "No evidence classes recorded"
        breakdown = {normalize_evidence_class(item): 1 for item in classes}
    parts = [
        f"{evidence_class_label(class_name)}: {count}"
        for class_name, count in sorted(breakdown.items())
    ]
    return _join_limited(parts, 260)


def _risk_confidence_basis_text(state: GraphState) -> str:
    risk = state.risk_assessment
    if not risk:
        return "Risk evidence confidence has not yet been calculated."
    basis = getattr(risk, "evidence_confidence_basis", "") or risk.uncertainty_flag
    rule = getattr(risk, "evidence_confidence_rule", "")
    if rule:
        return f"{basis} Rule: {rule}"
    return basis or "No risk evidence confidence basis was recorded."


def _risk_evidence_drivers_text(state: GraphState) -> str:
    risk = state.risk_assessment
    if not risk:
        return ""
    drivers = getattr(risk, "evidence_confidence_drivers", []) or []
    if not drivers:
        return ""
    return "Confidence drivers: " + _join_limited([str(item) for item in drivers[:12]], 360)


def _device_model(state: GraphState) -> str:
    device = state.graph_context.get("device", {})
    return str(device.get("model") or device.get("name") or state.device_id or "Device")


def _software_version(state: GraphState) -> str:
    complaint = state.structured_complaint
    device = state.graph_context.get("device", {})
    return str((complaint.firmware_version if complaint else "") or device.get("current_firmware") or "Not specified")


def _complaint_issue_text(state: GraphState, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", state.raw_complaint or "").strip()
    return _clean((text or "reported complaint condition").rstrip(". "), limit)


def _affected_area_label(state: GraphState) -> str:
    complaint = state.structured_complaint
    scope = state.investigation_scope or {}
    functional = [
        str(item.get("category"))
        for item in scope.get("functional_scope", [])
        if isinstance(item, dict) and item.get("category")
    ]
    if functional:
        return _join_limited(functional, 110)
    if complaint and complaint.affected_component_name:
        return _clean(complaint.affected_component_name, 110)
    return _clean(state.device_id or "affected device function", 110)


def _closure_focus_text(state: GraphState) -> str:
    text = (state.raw_complaint or "").lower()
    if any(term in text for term in ["alarm", "alert", "notification"]):
        return "notification timing and user-visible response"
    if any(term in text for term in ["display", "screen", "ui", "freeze", "blank"]):
        return "display/user-interface behavior"
    if any(term in text for term in ["battery", "power", "charge", "shutdown", "restart"]):
        return "power availability and safe-state behavior"
    if any(term in text for term in ["measurement", "reading", "accuracy", "spo2", "pulse", "sensor"]):
        return "measurement accuracy and signal reliability"
    if any(term in text for term in ["connect", "bluetooth", "pair", "app", "sync", "upload", "data"]):
        return "connectivity and data-transfer behavior"
    return "the affected function under the reported condition"


def _engineering_owner_text(state: GraphState) -> str:
    text = (state.raw_complaint or "").lower()
    if any(term in text for term in ["software", "firmware", "app", "bluetooth", "sync", "display", "alarm"]):
        return "Software/System Engineering"
    if any(term in text for term in ["battery", "power", "charge", "shutdown", "restart"]):
        return "Electrical/Power Engineering"
    if any(term in text for term in ["sensor", "measurement", "accuracy", "fit", "finger", "pediatric"]):
        return "Systems/Verification Engineering"
    if any(term in text for term in ["water", "dust", "drop", "cleaning", "damage"]):
        return "Reliability/Mechanical Engineering"
    return "Engineering"


def _patient_or_user_impact_text(state: GraphState) -> str:
    text = (state.raw_complaint or "").lower()
    if any(term in text for term in ["death", "serious injury", "injury", "harm", "hospitalized"]):
        return "Potential or reported patient/user harm - escalate for regulatory review."
    if any(term in text for term in ["alarm", "alert", "notification"]):
        return "Potential delayed awareness of a safety or status condition."
    if any(term in text for term in ["incorrect", "inaccurate", "wrong", "drift", "unstable", "measurement", "reading"]):
        return "Potential incorrect or unreliable information available to the user."
    if any(term in text for term in ["battery", "power", "shutdown", "restart", "charge"]):
        return "Potential interruption or unavailability of device function."
    if any(term in text for term in ["connect", "bluetooth", "pair", "sync", "upload", "data"]):
        return "Potential loss or delay of connected workflow or device data."
    return "Potential device performance or usability impact requiring quality review."


def _closure_status_text(state: GraphState) -> str:
    if state.capa_closure_tier == "simulated_evidence_approval":
        return f"{state.capa_closure_status}: {state.capa_closure_required_action}"
    if state.capa_closure_tier == "closed_controlled_verification":
        return f"{state.capa_closure_status}: {state.capa_closure_rationale}"
    if state.closure_blocked_reason:
        return f"{state.capa_closure_status or 'Pending - Insufficient Evidence'}: {state.closure_blocked_reason}"
    if state.capa_closure_status:
        return state.capa_closure_status
    return "Open - objective evidence, risk review, and approval are required before closure"


def _reportability_decision_rows(state: GraphState) -> list[list[str]]:
    text = (state.raw_complaint or "").lower()
    risk = state.risk_assessment
    serious = any(term in text for term in ["death", "serious injury", "injury", "harm", "hospitalized"])
    relation = _affected_area_label(state)
    malfunction_signal = "Reported condition indicates possible device malfunction or use-related failure."
    if any(term in text for term in ["uncomfortable", "confusing", "label", "instruction", "fit"]):
        malfunction_signal = "Reported condition indicates possible usability, labeling, fit, or accessory issue."
    return [
        [
            "Death or serious injury",
            "Reported or possible harm is indicated." if serious else "Not stated in complaint text.",
            "Escalate immediately." if serious else "Monitor for escalation during intake and investigation.",
        ],
        [
            "Potential harm or near miss",
            _patient_or_user_impact_text(state),
            "Treat as reportability review required." if (risk and risk.reportable) else "Document rationale and continue risk review.",
        ],
        [
            "Device relationship",
            f"Complaint maps to {relation}.",
            "Keep CAPA open until engineering evidence confirms or rules out relationship.",
        ],
        [
            "Malfunction/use issue",
            malfunction_signal,
            "Verify recurrence risk and effectiveness of controls.",
        ],
    ]


def _cybersecurity_sbom_narrative(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    complaint_state: GraphState,
    cybersecurity_state: GraphState | None,
) -> None:
    story.append(Paragraph("12. Cybersecurity / SBOM Narrative", styles["h1"]))
    if not cybersecurity_state:
        story.append(
            Paragraph(
                "Cybersecurity Agent output was not attached to this report. Run the device-level SBOM/NVD scan to populate this section.",
                styles["body"],
            )
        )
        return

    summary = cybersecurity_state.cybersecurity_summary or {}
    components = summary.get("components") or cybersecurity_state.sbom_components
    findings = cybersecurity_state.cybersecurity_findings
    query_date = str(summary.get("query_finished_at") or summary.get("query_started_at") or "query date unavailable")[:10]
    story.append(
        Paragraph(
            _escape(
                "This section is device-level cybersecurity surveillance. It is not complaint evidence and it does not change "
                "the Risk Agent score automatically. It records what the Cybersecurity Agent found by reading the SBOM and "
                "querying the National Vulnerability Database for the CPE candidates in the workbook."
            ),
            styles["body"],
        )
    )
    summary_rows = [
        ["SBOM components", str(summary.get("sbom_component_count", len(cybersecurity_state.sbom_components)))],
        ["Components queried", str(summary.get("queried_component_count", len(components)))],
        ["NVD findings", str(len(findings))],
        ["Query date", query_date],
        ["Source type", str(summary.get("source_type", SourceType.EXTRACTED.value))],
        ["Cache status", str(summary.get("cache_status", "not recorded"))],
    ]
    story.append(_kv_table(summary_rows, styles, widths=[42 * mm, 130 * mm]))

    story.append(Paragraph("Executive Severity Rollup", styles["h2"]))
    rollup = _cybersecurity_severity_rollup(findings)
    rollup_rows = [["Critical", "High", "Medium", "Low", "None"], [str(rollup[key]) for key in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"]]]
    story.append(_table(rollup_rows, styles, widths=[34 * mm, 34 * mm, 34 * mm, 34 * mm, 36 * mm]))
    story.append(
        Paragraph(
            _escape(
                "VEX-style exploitability status is shown per CVE. The current automated SBOM/NVD match does not include "
                "device-specific exploitability determinations, so findings default to Under Investigation until Engineering "
                "documents whether the vulnerable code path is affected, fixed, not affected, or not reachable in this device configuration."
            ),
            styles["small"],
        )
    )

    story.append(Paragraph("SBOM Component Coverage", styles["h2"]))
    component_rows = [["Component", "Version", "Supplier", "CPE Candidate", "NVD Result"]]
    for component in components:
        status = str(component.get("nvd_status") or "not queried")
        note = str(component.get("nvd_note") or "No NVD result recorded.")
        component_rows.append(
            [
                _clean(str(component.get("component", "")), 90),
                _clean(str(component.get("version", "")), 36),
                _clean(str(component.get("supplier", "")), 90),
                _clean(str(component.get("cpe", "")), 120),
                _clean(f"{status}: {note}", 180),
            ]
        )
    story.append(_table(component_rows, styles, widths=[30 * mm, 18 * mm, 32 * mm, 52 * mm, 40 * mm]))

    story.append(Paragraph("NVD CVE Findings - Structured Vulnerability Records", styles["h2"]))
    if findings:
        for finding in findings[:18]:
            story.append(_cve_record_block(finding, styles))
    else:
        story.append(
            Paragraph(
                _escape(f"No known CVEs found for the queried component/version combinations as of {query_date}."),
                styles["body"],
            )
        )

    if summary.get("errors"):
        story.append(Paragraph("Query Warnings", styles["h2"]))
        rows = [["Component", "CPE", "Issue"]]
        for error in summary.get("errors", [])[:8]:
            rows.append([
                _clean(str(error.get("component", "")), 80),
                _clean(str(error.get("cpe", "")), 100),
                _clean(str(error.get("error", "")), 180),
            ])
        story.append(_table(rows, styles, widths=[40 * mm, 70 * mm, 62 * mm]))


def _cybersecurity_severity_rollup(findings: list[dict[str, Any]]) -> dict[str, int]:
    rollup = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "NONE": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "NONE").strip().upper()
        if severity in rollup:
            rollup[severity] += 1
        else:
            rollup["NONE"] += 1
    return rollup


def _cve_record_block(finding: dict[str, Any], styles: dict[str, ParagraphStyle]) -> KeepTogether:
    cve_id = str(finding.get("cve_id") or "CVE unavailable")
    source_url = str(finding.get("source_url") or f"https://nvd.nist.gov/vuln/detail/{cve_id}")
    score = finding.get("cvss_score")
    severity = str(finding.get("severity") or "UNKNOWN")
    cvss_version = str(finding.get("cvss_version") or "not specified")
    vector = str(finding.get("cvss_vector") or "Vector string not provided by NVD metric selected for this CVE.")
    status = "Open/Patch Required" if finding.get("open_unpatched") else "Not Open"
    patched_status = str(finding.get("patched_status") or "")
    rows = [
        [
            Paragraph("<b>CVE ID / NVD Reference</b>", styles["table_cell"]),
            Paragraph(
                f'<b><link href="{_escape(source_url)}">{_escape(cve_id)}</link></b><br/><font size="6">{_escape(source_url)}</font>',
                styles["table_cell"],
            ),
        ],
        [
            Paragraph("<b>CVSS Score / Severity / Vector</b>", styles["table_cell"]),
            Paragraph(
                _escape(
                    f"{severity} / CVSS {score if score is not None else 'unavailable'} / v{cvss_version}\n"
                    f"Vector: {_wrap_vector(vector)}"
                ),
                styles["table_cell"],
            ),
        ],
        [Paragraph("<b>CWE ID(s)</b>", styles["table_cell"]), Paragraph(_escape(_cwe_display(finding)), styles["table_cell"])],
        [
            Paragraph("<b>Affected Component + Version</b>", styles["table_cell"]),
            Paragraph(_escape(f"{finding.get('component', '')} {finding.get('version', '')}".strip()), styles["table_cell"]),
        ],
        [
            Paragraph("<b>Vulnerability Description</b>", styles["table_cell"]),
            Paragraph(_escape(_finding_vulnerability_description(finding)), styles["table_cell"]),
        ],
        [
            Paragraph("<b>Exploitability / Exposure Context</b>", styles["table_cell"]),
            Paragraph(_escape(_finding_exposure_context(finding)), styles["table_cell"]),
        ],
        [
            Paragraph("<b>Recommended Controls</b>", styles["table_cell"]),
            Paragraph(_escape(_finding_recommended_controls(finding)), styles["table_cell"]),
        ],
        [
            Paragraph("<b>VEX-Style Status</b>", styles["table_cell"]),
            Paragraph(_escape(_finding_vex_status(finding)), styles["table_cell"]),
        ],
        [
            Paragraph("<b>Status</b>", styles["table_cell"]),
            Paragraph(_escape(f"{status}. {patched_status}".strip()), styles["table_cell"]),
        ],
        [
            Paragraph("<b>Source / Reference</b>", styles["table_cell"]),
            Paragraph(
                _escape(
                    f"{finding.get('source_type', SourceType.EXTRACTED.value)}; {finding.get('source', 'NVD CVE API 2.0')}\n"
                    f"{source_url}"
                ),
                styles["table_cell"],
            ),
        ],
    ]
    table = Table(rows, colWidths=[44 * mm, 128 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, TABLE_STRIPE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return KeepTogether([Paragraph(f"Vulnerability Record - {_escape(cve_id)}", styles["h2"]), table, Spacer(1, 4 * mm)])


def _cwe_display(finding: dict[str, Any]) -> str:
    values = finding.get("weaknesses") or []
    if isinstance(values, str):
        values = [values]
    formatted = [_format_cwe(str(value)) for value in values if value]
    return ", ".join(formatted) if formatted else "No CWE provided by NVD for this CVE."


def _format_cwe(value: str) -> str:
    known = {
        "CWE-284": "CWE-284: Improper Access Control",
        "CWE-287": "CWE-287: Improper Authentication",
        "CWE-295": "CWE-295: Improper Certificate Validation",
        "CWE-310": "CWE-310: Cryptographic Issues",
        "NVD-CWE-Other": "NVD-CWE-Other: Other weakness classification",
        "NVD-CWE-noinfo": "NVD-CWE-noinfo: No CWE information",
    }
    return known.get(value, value)


def _finding_vulnerability_description(finding: dict[str, Any]) -> str:
    note = str(finding.get("exploitability_note") or "")
    return (
        _note_segment(note, "Vulnerability", ["Weakness", "Exposure", "Controls"])
        or str(finding.get("description_summary") or "")
        or "Limited public description available for this CVE; review the NVD record before assigning device impact."
    )


def _finding_exposure_context(finding: dict[str, Any]) -> str:
    note = str(finding.get("exploitability_note") or "")
    return _note_segment(note, "Exposure", ["Controls"]) or "Exposure context requires engineering review for this device configuration."


def _finding_recommended_controls(finding: dict[str, Any]) -> str:
    note = str(finding.get("exploitability_note") or "")
    return _note_segment(note, "Controls", []) or str(finding.get("patched_status") or "Review vendor fix status and compensating controls.")


def _finding_vex_status(finding: dict[str, Any]) -> str:
    return str(finding.get("vex_status") or "Under Investigation")


def _note_segment(note: str, label: str, next_labels: list[str]) -> str:
    marker = f"{label}:"
    start = note.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = len(note)
    for next_label in next_labels:
        next_pos = note.find(f"{next_label}:", start)
        if next_pos >= 0:
            end = min(end, next_pos)
    return note[start:end].strip()


def _wrap_vector(vector: str) -> str:
    return str(vector or "").replace("/", "/ ")


def _cybersecurity_risk_note(state: GraphState, cybersecurity_state: GraphState | None) -> str:
    if not cybersecurity_state or not cybersecurity_state.cybersecurity_findings:
        return ""
    complaint = state.structured_complaint
    text = " ".join(
        [
            state.raw_complaint or "",
            complaint.affected_component if complaint else "",
            complaint.affected_component_name if complaint else "",
            " ".join(complaint.symptom_codes if complaint else []),
        ]
    ).lower()
    complaint_terms = _term_set(text)
    matched = []
    for finding in cybersecurity_state.cybersecurity_findings:
        component_text = " ".join(
            [
                str(finding.get("component", "")),
                str(finding.get("supplier", "")),
                str(finding.get("cpe", "")),
                str(finding.get("purl", "")),
            ]
        ).lower()
        component_terms = _term_set(component_text)
        if complaint_terms.intersection(component_terms) or _cyber_special_overlap(text, component_text):
            matched.append(finding)
    if not matched:
        return ""
    cves = _join_limited([str(item.get("cve_id", "")) for item in matched[:6]], 120)
    components = _join_limited([str(item.get("component", "")) for item in matched[:6]], 120)
    return (
        f"Cybersecurity Agent found {len(matched)} NVD finding(s) with terminology overlap to this complaint scope "
        f"({components}; {cves}). This is an informational reviewer prompt only; Risk Agent scoring was not changed."
    )


def _term_set(text: str) -> set[str]:
    stop = {
        "component",
        "device",
        "module",
        "manager",
        "service",
        "software",
        "pulseox",
        "pulse",
        "oximeter",
        "symptom",
    }
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 3 and token not in stop}


def _cyber_special_overlap(complaint_text: str, component_text: str) -> bool:
    mappings = [
        (["connect", "bluetooth", "pair", "wireless", "ble"], ["bluetooth", "communication", "connectivity"]),
        (["wifi", "wi-fi", "cloud", "upload"], ["wi", "wpa", "cloud", "communication"]),
        (["firmware", "update", "version"], ["firmware", "bootloader", "update", "openssl", "crypto"]),
        (["security", "auth", "login", "key", "certificate", "tls"], ["auth", "key", "openssl", "crypto", "secure"]),
    ]
    return any(
        any(term in complaint_text for term in complaint_terms)
        and any(term in component_text for term in component_terms)
        for complaint_terms, component_terms in mappings
    )


def _part11_controls(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, ids: dict[str, str]) -> None:
    story.append(Paragraph("13. Electronic Records - 21 CFR Part 11", styles["h1"]))
    controls = [
        ["System Validation", "DocPlus+ generation path is deterministic and testable through FastAPI route and PDF render checks.", "11.10(a)"],
        ["Audit Trail", f"Generation record links {ids['complaint_id']}, {ids['capa_id']}, timestamp, and agent trace events.", "11.10(e)"],
        ["Access Controls", "AI output is marked draft and requires QE/RA approval before controlled use.", "11.10(d)"],
        ["Electronic Signatures", "Signature block captures reviewer, approver, date, and meaning of signature.", "11.50"],
        ["Record Integrity", "PDF is generated as a stable artifact with a deterministic document number and retrievable filename.", "11.10(c)"],
        ["Human-Readable Copy", "Selectable-text PDF is available through the document download endpoint.", "11.10(b)"],
        ["Retention", "Artifact is retained under output/pdf and can be indexed by M5 or a QMS repository.", "11.10(c)"],
    ]
    story.append(_table([["Requirement", "DocPlus+ Implementation", "Part 11"], *controls], styles, widths=[40 * mm, 103 * mm, 29 * mm]))
    story.append(Paragraph("Generation Log Summary", styles["h2"]))
    rows = [
        ["Generation Hash", ids["hash"]],
        ["Input Fingerprint", ids["input_fingerprint"]],
        ["AI Draft Status", "Pending human quality and regulatory review"],
        ["Low Confidence Handling", "Evidence below 70% must be reviewed before release; current generated evidence remains visible with confidence values."],
    ]
    story.append(_kv_table(rows, styles))


def _audit_and_trace(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    complaint_state: GraphState,
    audit_state: GraphState | None,
    trace_state: GraphState | None,
) -> None:
    story.append(Paragraph("14. AuditShadow and Trace Decay", styles["h1"]))
    if audit_state and audit_state.audit_findings:
        data = [["Finding(s)", "Requirement(s)", "Risk", "Observation", "Remediation"]]
        for finding in _group_audit_findings(audit_state.audit_findings)[:6]:
            data.append(
                [
                    _join_limited(finding["ids"], 80),
                    _join_limited(finding["requirements"], 100),
                    finding["risk_level"],
                    finding["observation"],
                    finding["remediation"],
                ]
            )
        story.append(Paragraph("AuditShadow findings", styles["h2"]))
        story.append(_table(data, styles, widths=[23 * mm, 30 * mm, 19 * mm, 59 * mm, 41 * mm]))
    else:
        story.append(Paragraph("AuditShadow produced no findings for this report scope or was not requested.", styles["body"]))

    if trace_state and trace_state.trace_decay_alerts:
        data = [["Requirement(s)", "Test(s)", "Tested FW", "Required FW", "Reason"]]
        for alert in _group_trace_alerts(trace_state.trace_decay_alerts)[:6]:
            data.append(
                [
                    _join_limited(alert["requirements"], 100),
                    _join_limited(alert["tests"], 90),
                    _clean(str(alert.get("tested_firmware", "N/A")), 60),
                    _clean(str(alert.get("required_firmware", "N/A")), 60),
                    _clean(str(alert.get("reason", "Evidence must be refreshed for the target firmware.")), 260),
                ]
            )
        story.append(Paragraph("Trace Decay alerts", styles["h2"]))
        story.append(_table(data, styles, widths=[32 * mm, 28 * mm, 28 * mm, 22 * mm, 62 * mm]))
    else:
        story.append(Paragraph("Trace Decay produced no alerts for this report scope or was not requested.", styles["body"]))

    _audit_data_interpretation(story, styles, complaint_state, audit_state, trace_state)


def _audit_data_interpretation(
    story: list[Any],
    styles: dict[str, ParagraphStyle],
    complaint_state: GraphState,
    audit_state: GraphState | None,
    trace_state: GraphState | None,
) -> None:
    candidate_evidence_count = len(complaint_state.evidence_collected)
    graph_gap_count = (
        sum(1 for finding in audit_state.audit_findings if _is_graph_evidence_gap(finding.finding))
        if audit_state
        else 0
    )
    trace_alert_count = len(trace_state.trace_decay_alerts) if trace_state else 0

    story.append(Paragraph("14.1 Audit Data Interpretation and Corrective Action", styles["h2"]))
    story.append(Paragraph(
        "AuditShadow and Trace Decay use the live M1 traceability graph as the controlled source for verification status. "
        "When the report flags missing evidence, it means the requirement does not have a formal VERIFIED_BY test link in "
        "that graph. It does not mean M4 found no supporting material. In this run, M4 retrieved "
        f"{candidate_evidence_count} candidate evidence item(s), but candidate snippets remain draft support until a Quality "
        "reviewer links them to a requirement, test record, firmware version, and controlled source identifier.",
        styles["body"],
    ))

    rows = [
        ["Signal", "What It Means", "Source", "Corrective Action"],
        [
            "AuditShadow graph evidence gap",
            f"{graph_gap_count} requirement finding(s) lack a VERIFIED_BY test link in M1.",
            "M1 readiness score and traceability matrix",
            "Create or link verification test evidence to each requirement and rescore readiness.",
        ],
        [
            "M4 retrieved evidence",
            f"{candidate_evidence_count} candidate item(s) exist, but are not automatically controlled objective evidence.",
            "M2 complaint pipeline and M4 evidence retrieval",
            "Promote accepted snippets into controlled evidence nodes with requirement IDs, source, confidence, and firmware.",
        ],
        [
            "Trace Decay",
            f"{trace_alert_count} firmware freshness alert(s) require review before release or approval.",
            "M2 Trace Decay over M1 firmware/test links",
            "Rerun verification on current firmware or document an approved equivalence rationale.",
        ],
        [
            "CAPA linkage",
            "Open CAPA or complaint risk can keep readiness from being clean even when evidence exists.",
            "M1 graph relationships and M2 risk assessment",
            "Close, justify, or explicitly reference CAPA impact before final regulatory approval.",
        ],
    ]
    story.append(_table(rows, styles, widths=[34 * mm, 46 * mm, 43 * mm, 49 * mm]))
    story.append(Paragraph("Reason and Corrective Action", styles["h2"]))
    story.append(Paragraph(
        "The root issue is a traceability-linkage gap rather than a simple absence of all evidence. Corrective action is to "
        "synchronize M4 retrieved evidence back into the M1 graph as controlled verification evidence, then rerun AuditShadow "
        "and Trace Decay. Until that link exists, the report correctly treats the material as candidate support and keeps the "
        "audit finding open.",
        styles["body"],
    ))


def _audit_detail_appendix(story: list[Any], styles: dict[str, ParagraphStyle], audit_state: GraphState | None) -> None:
    story.append(Paragraph("14.2 AuditShadow Detail Appendix", styles["h1"]))
    if not audit_state or not audit_state.audit_findings:
        story.append(Paragraph("No AuditShadow findings were attached.", styles["body"]))
        return
    data = [["Finding(s)", "Requirement(s)", "Reference", "Risk", "Observation", "Remediation"]]
    for finding in _group_audit_findings(audit_state.audit_findings)[:14]:
        data.append([
            _join_limited(finding["ids"], 80),
            _join_limited(finding["requirements"], 110),
            _join_limited(finding["references"], 90),
            finding["risk_level"],
            finding["observation"],
            finding["remediation"],
        ])
    story.append(_table(data, styles, widths=[20 * mm, 26 * mm, 28 * mm, 17 * mm, 48 * mm, 33 * mm]))
    story.append(Paragraph("Audit Interpretation", styles["h2"]))
    story.append(Paragraph(
        "Major and critical findings should be triaged before report approval. A graph-linked evidence gap means the "
        "requirement is present in the M1 graph while still lacking a controlled verification test relationship acceptable "
        "for submission or inspection. Candidate RAG evidence must be reviewed, accepted, and linked before the finding can close.",
        styles["body"],
    ))


def _trace_detail_appendix(story: list[Any], styles: dict[str, ParagraphStyle], trace_state: GraphState | None) -> None:
    story.append(Paragraph("14.3 Trace Decay Detail Appendix", styles["h1"]))
    if not trace_state or not trace_state.trace_decay_alerts:
        story.append(Paragraph("No Trace Decay alerts were attached.", styles["body"]))
        return
    data = [["Requirement(s)", "Test(s)", "Tested Firmware", "Required Firmware", "Reason"]]
    for alert in _group_trace_alerts(trace_state.trace_decay_alerts)[:16]:
        data.append([
            _join_limited(alert["requirements"], 120),
            _join_limited(alert["tests"], 100),
            _clean(str(alert.get("tested_firmware", "N/A")), 60),
            _clean(str(alert.get("required_firmware", "N/A")), 60),
            _clean(str(alert.get("reason", "Evidence must be refreshed for the target firmware.")), 180),
        ])
    story.append(_table(data, styles, widths=[32 * mm, 26 * mm, 31 * mm, 31 * mm, 52 * mm]))
    story.append(Paragraph("Traceability Impact Statement", styles["h2"]))
    story.append(Paragraph(
        "Trace Decay identifies verification evidence that may no longer represent the active or proposed firmware state. "
        "These alerts are not final nonconformities by themselves, but they are mandatory review inputs for release, CAPA, "
        "and audit-readiness decisions.",
        styles["body"],
    ))


def _group_audit_findings(findings: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for finding in findings:
        source_types = getattr(finding, "source_types", []) or [getattr(finding, "source_type", SourceType.INFERRED.value)]
        observation = _audit_observation_text(finding)
        remediation = _audit_remediation_text(finding)
        key = (finding.risk_level, observation, remediation)
        if key not in grouped:
            grouped[key] = {
                "ids": [],
                "requirements": [],
                "references": [],
                "risk_level": finding.risk_level,
                "observation": observation,
                "remediation": remediation,
                "source_types": [],
            }
        grouped[key]["ids"].append(finding.id)
        grouped[key]["requirements"].append(finding.requirement_id)
        grouped[key]["references"].append(finding.regulatory_reference)
        grouped[key]["source_types"].extend(source_types)
    for item in grouped.values():
        item["observation"] = _append_source_tag(item["observation"], item.get("source_types", []))
        item["remediation"] = _append_source_tag(item["remediation"], item.get("source_types", []))
    return list(grouped.values())


def _group_trace_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for alert in alerts:
        tested = str(alert.get("tested_firmware") or "N/A")
        required = str(alert.get("required_firmware") or "N/A")
        reason = str(alert.get("reason") or "Evidence must be refreshed for the target firmware.")
        key = (tested, required, reason)
        if key not in grouped:
            grouped[key] = {
                "requirements": [],
                "tests": [],
                "tested_firmware": tested,
                "required_firmware": required,
                "reason": reason,
                "source_types": [],
            }
        grouped[key]["requirements"].append(str(alert.get("requirement_id") or "N/A"))
        grouped[key]["tests"].append(str(alert.get("test_id") or "N/A"))
        grouped[key]["source_types"].extend(alert.get("source_types") or [alert.get("source_type", SourceType.INFERRED.value)])
    for item in grouped.values():
        item["reason"] = _append_source_tag(str(item.get("reason", "")), item.get("source_types", []))
    return list(grouped.values())


def _join_limited(values: list[str], limit: int) -> str:
    unique = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return _clean(", ".join(unique), limit)


def _is_graph_evidence_gap(text: str) -> bool:
    normalized = text.lower()
    return (
        "no linked verification" in normalized
        or "no graph-linked verification" in normalized
        or "objective evidence is missing" in normalized
    )


def _audit_observation_text(finding: Any) -> str:
    if _is_graph_evidence_gap(str(finding.finding)):
        return (
            "M1 graph has no VERIFIED_BY test evidence linked for this requirement. M4 may have retrieved candidate "
            "evidence, but it is not controlled objective verification evidence until Quality links it to the requirement, "
            "test record, source, and version/configuration metadata."
        )
    return _clean(str(finding.finding), 300)


def _audit_remediation_text(finding: Any) -> str:
    if _is_graph_evidence_gap(str(finding.finding)):
        return "Link or create verification test evidence with current version/configuration metadata, then rerun AuditShadow."
    return _clean(str(finding.remediation), 220)


def _event_detail_text(data: Any) -> str:
    if not data:
        return "Recorded"
    if isinstance(data, dict):
        details = []
        if "elapsed_ms" in data:
            details.append(f"Elapsed {float(data['elapsed_ms']):.1f} ms")
        for key in ("findings", "readiness_score", "alerts", "evidence_items", "risk_level", "rpn"):
            if key in data:
                label = key.replace("_", " ").title()
                details.append(f"{label}: {data[key]}")
        return _clean("; ".join(details) if details else "Structured event metadata recorded", 150)
    return _clean(str(data), 150)


def _cross_standard_rendering(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, ids: dict[str, str]) -> None:
    story.append(Paragraph("15. Cross-Standard Rendering Matrix", styles["h1"]))
    affected = _affected_area_label(state)
    rows = [
        ["Safety Requirement", "Design input under QMSR; linked to verification evidence.", "GSPR and Annex I safety/performance claim.", "ISO 13485 Clause 7.3.3 design input."],
        ["Test Evidence", "DHF verification record; current configuration/version required.", "Annex II Section 6.1 V&V evidence.", "Clause 7.3.6 design verification."],
        ["Complaint Record", f"Complaint file {ids['complaint_id']} with malfunction assessment.", "Post-market vigilance signal under Articles 83/87.", "Complaint handling under Clause 8.2.2."],
        ["CAPA Record", f"{ids['capa_id']} opened for {affected}.", "Trend/CAPA input for PMS and vigilance review.", "Corrective action under Clause 8.5.2."],
        ["Risk Documentation", "Risk analysis tied to design controls and complaint file.", "Benefit-risk update in Annex II Section 5.", "Risk management continuity through production/post-production."],
        ["Product Documentation", f"Lifecycle impact for {affected}.", "Design information and change-control evidence.", "Validation and change-control evidence required."],
    ]
    story.append(_table([["Element", "FDA / QMSR", "EU MDR", "ISO 13485"], *rows], styles, widths=[34 * mm, 46 * mm, 46 * mm, 46 * mm]))
    story.append(Paragraph("Regulatory Lens Note", styles["h2"]))
    story.append(Paragraph(
        "The same live graph and agent state are rendered under three regulatory lenses. This report therefore does not "
        "duplicate content blindly; it reformats the same evidence chain for FDA/QMSR, EU MDR, and ISO 13485 review contexts.",
        styles["body"],
    ))


def _approval_block(story: list[Any], styles: dict[str, ParagraphStyle], state: GraphState, generated_at: str, ids: dict[str, str]) -> None:
    story.append(Paragraph("16. Document Control and Approval", styles["h1"]))
    rows = [
        ["Document No.", ids["document_control_no"]],
        ["Revision", "A"],
        ["Revision Date", generated_at[:10]],
        ["Complaint ID", ids["complaint_id"]],
        ["CAPA ID", ids["capa_id"]],
        ["Prepared by", "DocPlus+ M4 Evidence and Documentation Layer"],
        ["Generated from", "Live M2 complaint pipeline, M1 graph context, M4 evidence retrieval, and device-level SBOM/NVD scan when attached"],
        ["Generated at", generated_at],
        ["Agent status", state.status],
        ["Classification", "Confidential - AI Generated Draft"],
        ["Next Review", ids["next_review"]],
    ]
    story.append(_kv_table(rows, styles))
    story.append(Spacer(1, 4 * mm))
    data = [["Role", "Name", "Signature", "Date"], ["Quality Reviewer", "", "", ""], ["Regulatory Reviewer", "", "", ""]]
    story.append(_table(data, styles, widths=[43 * mm, 43 * mm, 43 * mm, 43 * mm], row_heights=[8 * mm, 12 * mm, 12 * mm]))


def _decorate_page(canvas: Any, doc: Any) -> None:
    page_width, page_height = A4
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#102A43"))
    canvas.rect(0, page_height - 12 * mm, page_width, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(18 * mm, page_height - 7.5 * mm, PROJECT_NAME)
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(page_width - 18 * mm, page_height - 7.5 * mm, "Confidential - AI Draft")
    canvas.setFillColor(colors.HexColor("#627D98"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(18 * mm, 10 * mm, "Generated by DocPlus+ M4 from live agent state")
    canvas.drawRightString(page_width - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _kv_table(rows: list[list[str]], styles: dict[str, ParagraphStyle], widths: list[float] | None = None) -> Table:
    widths = widths or [42 * mm, 130 * mm]
    data = [[Paragraph(f"<b>{_escape(k)}</b>", styles["table_cell"]), Paragraph(_escape(v), styles["table_cell"])] for k, v in rows]
    table = Table(data, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF2F6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _status_table(rows: list[list[str]], styles: dict[str, ParagraphStyle]) -> Table:
    cells = []
    for key, value in rows:
        cells.append(Paragraph(f"{_escape(key)}<br/><b>{_escape(value)}</b>", styles["badge"]))
    table_rows = [cells[index : index + 3] for index in range(0, len(cells), 3)]
    while table_rows and len(table_rows[-1]) < 3:
        table_rows[-1].append(Paragraph("", styles["badge"]))
    table = Table(table_rows, colWidths=[54 * mm, 54 * mm, 54 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _table(
    data: list[list[str]],
    styles: dict[str, ParagraphStyle],
    widths: list[float],
    row_heights: list[float] | None = None,
) -> Table:
    paragraph_rows = []
    for row_index, row in enumerate(data):
        style = styles["table_header"] if row_index == 0 else styles["table_cell"]
        paragraph_rows.append([Paragraph(_escape(str(cell)), style) for cell in row])
    table = Table(paragraph_rows, colWidths=widths, rowHeights=row_heights, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102A43")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_STRIPE]),
            ]
        )
    )
    return table


def _document_id(state: GraphState) -> str:
    basis = state.raw_complaint or state.device_id or datetime.utcnow().isoformat()
    compact = re.sub(r"[^A-Za-z0-9]+", "", basis.upper())
    suffix = compact[:8] if compact else datetime.utcnow().strftime("%H%M%S")
    return f"DP-M4-{datetime.utcnow().strftime('%Y%m%d')}-{suffix}"


def _writable_pdf_path(path: Path) -> Path:
    if _can_write_pdf(path):
        return path
    for index in range(1, 100):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if _can_write_pdf(candidate):
            return candidate
    timestamp = datetime.utcnow().strftime("%H%M%S")
    return path.with_name(f"{path.stem}-{timestamp}{path.suffix}")


def _can_write_pdf(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        with path.open("r+b"):
            return True
    except PermissionError:
        return False


def _record_ids(state: GraphState, generated_at: str) -> dict[str, str]:
    basis = "|".join(
        [
            state.device_id or "DEVICE",
            state.raw_complaint or "COMPLAINT",
            generated_at[:10],
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest().upper()
    short = digest[:8]
    date_compact = generated_at[:10].replace("-", "")
    date_dashed = generated_at[:10]
    next_review = (datetime.fromisoformat(generated_at.replace("Z", "")) + timedelta(days=180)).date().isoformat()
    lower = state.raw_complaint.lower()
    source = "Healthcare Professional" if any(term in lower for term in ["patient", "clinician", "nurse", "hospital", "clinic"]) else "Post-market complaint intake"
    patient_impact = _patient_or_user_impact_text(state)
    return {
        "hash": short,
        "input_fingerprint": digest[:16],
        "complaint_id": f"CPL-{date_compact}-{short[:4]}",
        "capa_id": f"CAPA-{date_compact}-{short[4:8]}",
        "rtm_id": f"RTM-DP-{short}",
        "udi": f"UDI-DI-08717648-{int(digest[:6], 16) % 900000 + 100000}",
        "received_at": generated_at,
        "source": source,
        "patient_impact": patient_impact,
        "document_control_no": f"DOCPLUS-M4-{date_compact}-{short}",
        "next_review": next_review,
    }


def _clean(value: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", _display_text(value)).strip()
    text = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2192", "->")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)].rstrip() + "..."


def _escape(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _display_text(value)).encode("ascii", "ignore").decode("ascii")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _display_text(value: Any) -> str:
    return BRAND_PATTERN.sub(PROJECT_NAME, str(value or ""))
