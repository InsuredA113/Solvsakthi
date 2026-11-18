from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from gridfs import GridFSBucket
from pymongo.errors import PyMongoError
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from .auth import require_roles
from .claims_analytics import is_in_scope_claim
from .database import database_is_available, get_database

router = APIRouter()

EXPORT_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
RELATED_COLLECTIONS = (
    ("adjuster_reports", "Adjuster Reports"),
    ("financial_estimates", "Financial Estimates"),
    ("damage_assessments", "Damage Assessments"),
    ("coverage_assessments", "Coverage Assessments"),
    ("comparison_comments", "Comparison Comments"),
    ("email_intake", "Email Intake"),
)
MAX_EXPORT_DOCUMENTS = 200
MAX_EXPORT_DOCUMENT_BYTES = 100 * 1024 * 1024


def _plain(value: Any) -> str:
    if value is None:
        return "Not Available"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(child, name))
    elif isinstance(value, list):
        if not value:
            rows.append((prefix, "[]"))
        else:
            for index, child in enumerate(value):
                rows.extend(_flatten(child, f"{prefix}[{index}]"))
    else:
        rows.append((prefix or "value", _plain(value)))
    return rows


def _csv_bytes(records: list[tuple[str, dict]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Record Type", "Field", "Value"])
    for record_type, record in records:
        for field, value in _flatten(record):
            writer.writerow([record_type, field, value])
    return output.getvalue().encode("utf-8-sig")


def _xlsx_bytes(records: list[tuple[str, dict]]) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Claim Export"
    summary.append(["Record Type", "Field", "Value"])
    for record_type, record in records:
        for field, value in _flatten(record):
            summary.append([record_type, field, value])
    summary.freeze_panes = "A2"
    summary.auto_filter.ref = summary.dimensions
    summary.column_dimensions["A"].width = 26
    summary.column_dimensions["B"].width = 42
    summary.column_dimensions["C"].width = 90
    for cell in summary[1]:
        cell.font = cell.font.copy(bold=True, color="FFFFFF")
        cell.fill = __import__("openpyxl").styles.PatternFill("solid", fgColor="17365D")
    for record_type, record in records:
        if record_type in {"Claim", "Policy"}:
            sheet = workbook.create_sheet(record_type[:31])
            sheet.append(["Field", "Value"])
            for field, value in _flatten(record):
                sheet.append([field, value])
            sheet.freeze_panes = "A2"
            sheet.column_dimensions["A"].width = 42
            sheet.column_dimensions["B"].width = 90
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _pdf_bytes(records: list[tuple[str, dict]], claim_id: str) -> bytes:
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm, title=f"Solv-In Claim {claim_id}"
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SolvTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#17365D")))
    styles.add(ParagraphStyle(name="SolvCell", parent=styles["BodyText"], fontSize=8, leading=10, wordWrap="CJK"))
    story = [Paragraph("Solv-In | Claim Export", styles["SolvTitle"]), Paragraph(f"Claim ID: {claim_id}", styles["Heading2"])]
    story.append(Paragraph(f"Generated: {datetime.now(timezone.utc).isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 8))
    for record_type, record in records:
        story.append(Paragraph(record_type.replace("&", "&amp;"), styles["Heading2"]))
        data = [[Paragraph("<b>Field</b>", styles["SolvCell"]), Paragraph("<b>Value</b>", styles["SolvCell"])]]
        for field, value in _flatten(record):
            safe_field = field.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe_value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
            data.append([Paragraph(safe_field, styles["SolvCell"]), Paragraph(safe_value, styles["SolvCell"])])
        table = Table(data, colWidths=[52 * mm, 122 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8C4D1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.extend([table, Spacer(1, 8)])
    doc.build(story)
    return output.getvalue()


def _safe_filename(claim_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", claim_id)


def _load_claim_export(claim_id: str) -> tuple[Any, list[tuple[str, dict]], list[tuple[str, bytes]]]:
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim = db["claims"].find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim) and claim.get("auto_intake_status") != "Provisional":
        raise HTTPException(status_code=422, detail="Exports are limited to Property and Commercial Casualty claims.")
    records: list[tuple[str, dict]] = [("Claim", claim)]
    policy_number = claim.get("policy_number")
    if policy_number:
        policy = db["policies"].find_one({"policy_number": policy_number}, {"_id": 0})
        if policy:
            records.append(("Policy", policy))
    for collection_name, label in RELATED_COLLECTIONS:
        rows = list(db[collection_name].find({"claim_id": claim_id}, {"_id": 0}).sort("created_at", 1).limit(500))
        records.extend((f"{label} {index + 1}", row) for index, row in enumerate(rows))
    documents: list[tuple[str, bytes]] = []
    bucket = GridFSBucket(db, bucket_name="claim_documents")
    total_bytes = 0
    for index, stored in enumerate(bucket.find({"metadata.claim_id": claim_id}).sort("uploadDate", 1).limit(MAX_EXPORT_DOCUMENTS)):
        if stored.length > MAX_EXPORT_DOCUMENT_BYTES or total_bytes + stored.length > MAX_EXPORT_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="Claim evidence exceeds the 100 MB ZIP export limit. Download large evidence files separately.")
        total_bytes += stored.length
        content = bucket.open_download_stream(stored._id).read()
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", stored.filename or f"document-{index + 1}")
        documents.append((f"evidence/{index + 1:03d}_{filename}", content))
    if bucket.find({"metadata.claim_id": claim_id}).limit(MAX_EXPORT_DOCUMENTS + 1).count() > MAX_EXPORT_DOCUMENTS:
        raise HTTPException(status_code=413, detail="This claim has more than 200 evidence files. Download evidence files separately.")
    return db, records, documents


@router.get("/api/claims/{claim_id}/export")
def export_claim(
    claim_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|xlsx|csv|zip)$"),
    user: dict = Depends(require_roles(*EXPORT_READ_ROLES))
):
    db, records, documents = _load_claim_export(claim_id)
    claim_name = _safe_filename(claim_id)
    pdf_content = _pdf_bytes(records, claim_id)
    xlsx_content = _xlsx_bytes(records)
    csv_content = _csv_bytes(records)
    generated_at = datetime.now(timezone.utc).isoformat()
    if format == "pdf":
        content, media_type, extension = pdf_content, "application/pdf", "pdf"
    elif format == "xlsx":
        content, media_type, extension = xlsx_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    elif format == "csv":
        content, media_type, extension = csv_content, "text/csv; charset=utf-8", "csv"
    else:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr(f"{claim_name}/claim-report.pdf", pdf_content)
            package.writestr(f"{claim_name}/claim-data.xlsx", xlsx_content)
            package.writestr(f"{claim_name}/claim-data.csv", csv_content)
            package.writestr(f"{claim_name}/manifest.json", json.dumps({
                "application": "Solv-In", "claim_id": claim_id, "generated_at": generated_at,
                "included_evidence_files": len(documents),
                "note": "Only records linked to this Claim ID are included. Missing data is not fabricated."
            }, indent=2))
            for filename, file_content in documents:
                package.writestr(f"{claim_name}/{filename}", file_content)
        content, media_type, extension = archive.getvalue(), "application/zip", "zip"
    try:
        db["claim_export_audit"].insert_one({
            "claim_id": claim_id, "format": format, "generated_at": generated_at,
            "generated_by": user["email"], "generated_by_role": user["role"],
            "file_size": len(content), "included_evidence_files": len(documents) if format == "zip" else 0
        })
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Export audit could not be saved; the export was not released.") from exc
    return StreamingResponse(
        io.BytesIO(content), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{claim_name}-export.{extension}"',
                 "Cache-Control": "no-store"}
    )
