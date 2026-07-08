from __future__ import annotations

from io import BytesIO
from textwrap import wrap
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from m2_agents.core.state import GraphState


def complaint_report_pdf(state: GraphState) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"DocPlus+ Complaint Report - {state.device_id}",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="SmallBody",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=11,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=styles["Heading2"],
            fontSize=12,
            leading=15,
            spaceBefore=8,
            spaceAfter=5,
            textColor=colors.HexColor("#12355b"),
            keepWithNext=1,
        )
    )

    story: list[Any] = [
        Paragraph("DocPlus+ Complaint Investigation Report", styles["Title"]),
        Paragraph(f"Device: {state.device_id or 'N/A'}", styles["SmallBody"]),
        Paragraph(f"Framework: {state.regulatory_label or state.regulatory_framework}", styles["SmallBody"]),
        Paragraph(f"Status: {state.status}", styles["SmallBody"]),
        Spacer(1, 0.12 * inch),
    ]

    complaint = state.structured_complaint
    if complaint:
        story.append(Paragraph("Complaint Summary", styles["SectionTitle"]))
        story.append(
            _table(
                [
                    ["Severity", complaint.severity],
                    ["Affected component", complaint.affected_component_name or complaint.affected_component or "N/A"],
                    ["Symptoms", ", ".join(complaint.symptom_codes) or "N/A"],
                    ["Timeline", complaint.timeline],
                    ["Raw summary", _short(complaint.raw_summary, 600)],
                ],
                styles,
            )
        )

    if state.hypotheses:
        story.append(Paragraph("Root Cause Hypotheses", styles["SectionTitle"]))
        for item in state.hypotheses[:3]:
            story.append(Paragraph(f"<b>{item.id}: {item.title}</b>", styles["SmallBody"]))
            story.append(Paragraph(_short(item.description, 700), styles["SmallBody"]))
            story.append(Paragraph(item.citation, styles["SmallBody"]))

    if state.evidence_collected:
        story.append(Paragraph("M4 Evidence Retrieved", styles["SectionTitle"]))
        evidence_rows = [["Evidence", "Source", "Confidence", "Snippet"]]
        for item in state.evidence_collected[:8]:
            evidence_rows.append([item.id, _short(item.source, 70), str(item.confidence), _short(item.snippet, 260)])
        story.append(_table(evidence_rows, styles, header=True))

    if state.risk_assessment:
        risk = state.risk_assessment
        story.append(Paragraph("Risk Assessment", styles["SectionTitle"]))
        story.append(
            _table(
                [
                    ["Severity", str(risk.severity)],
                    ["Probability", str(risk.probability)],
                    ["RPN", str(risk.rpn)],
                    ["Risk level", risk.risk_level],
                    ["Reportable", str(risk.reportable)],
                    ["Rationale", _short(risk.rationale, 600)],
                    ["Citation", risk.citation],
                ],
                styles,
            )
        )

    if state.capa_sections:
        story.append(Paragraph("CAPA Draft", styles["SectionTitle"]))
        for section in state.capa_sections:
            story.append(Paragraph(f"<b>{section.title}</b>", styles["SmallBody"]))
            story.append(Paragraph(f"{section.body} {section.citation}", styles["SmallBody"]))

    if state.ai_reasoning:
        story.append(Paragraph("AI Reasoning Summary", styles["SectionTitle"]))
        for key, value in state.ai_reasoning.items():
            story.append(Paragraph(f"<b>{key.replace('_', ' ').title()}</b>: {_short(str(value), 800)}", styles["SmallBody"]))

    doc.build(story)
    return buffer.getvalue()


def _table(rows: list[list[str]], styles: Any, header: bool = False) -> Table:
    prepared = [[Paragraph(_escape(str(cell)), styles["SmallBody"]) for cell in row] for row in rows]
    table = Table(prepared, colWidths=[1.25 * inch, 1.75 * inch, 0.75 * inch, 3.05 * inch] if header else [1.45 * inch, 5.35 * inch])
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d3df")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#12355b")),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def _short(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _escape(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "<br/>".join(wrap(text, width=85)) if len(text) > 100 else text
