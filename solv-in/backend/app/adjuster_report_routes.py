from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from .auth import require_roles
from .claims_analytics import is_in_scope_claim
from .database import database_is_available, get_database

router = APIRouter()

REPORT_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
REPORT_WRITE_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor"
)
ALLOWED_REPORT_FIELDS = {
    "customer_name", "loss_cause", "policy_number", "email", "phone",
    "claim_type", "property_occupancy", "business_name", "industry",
    "commercial_liability_type", "incident_date", "estimated_loss", "currency",
    "loss_location", "country_code", "region", "locality", "postal_code",
    "incident_description", "status"
}


class AdjusterReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    report_title: str = Field(default="Adjuster Report", min_length=2, max_length=160)
    report_reference: str | None = Field(default=None, max_length=120)
    report_date: str | None = Field(default=None, max_length=40)
    findings: str | None = Field(default=None, max_length=10000)
    report_fields: dict[str, Any] = Field(default_factory=dict, max_length=40)

    @field_validator("report_fields")
    @classmethod
    def validate_report_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - ALLOWED_REPORT_FIELDS
        if unknown:
            raise ValueError(f"Unsupported report field(s): {', '.join(sorted(unknown))}")
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                cleaned[key] = None
            elif isinstance(item, (str, int, float, bool)):
                cleaned[key] = item
            else:
                raise ValueError(f"Report field '{key}' must be a scalar value.")
        return cleaned


def _serialise_report(row: dict) -> dict:
    row["report_id"] = str(row.pop("_id"))
    return row


@router.get("/api/claims/{claim_id}/adjuster-reports")
def list_adjuster_reports(
    claim_id: str,
    user: dict = Depends(require_roles(*REPORT_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim_query = {"claim_id": claim_id}
    if user.get("role") != "Super Administrator":
        tenant_id = user.get("tenant_id")
        tenant = db["tenants"].find_one({"tenant_id": tenant_id, "status": "Active"}, {"_id": 1}) if tenant_id else None
        if not tenant:
            raise HTTPException(status_code=403, detail="Your insurer tenant is inactive or unavailable.")
        claim_query["tenant_id"] = tenant_id
    claim = db["claims"].find_one(claim_query, {"_id": 0, "claim_id": 1, "claim_type": 1, "casualty_segment": 1, "tenant_id": 1})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim):
        raise HTTPException(status_code=422, detail="Adjuster reports are limited to Property and Commercial Casualty claims.")
    try:
        rows = list(db["adjuster_reports"].find({"claim_id": claim_id, "tenant_id": claim.get("tenant_id")}).sort("created_at", DESCENDING).limit(100))
        return {"items": [_serialise_report(row) for row in rows], "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve adjuster reports.") from exc


@router.post("/api/claims/{claim_id}/adjuster-reports", status_code=201)
def create_adjuster_report(
    claim_id: str,
    payload: AdjusterReportCreate,
    user: dict = Depends(require_roles(*REPORT_WRITE_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim_query = {"claim_id": claim_id}
    if user.get("role") != "Super Administrator":
        tenant_id = user.get("tenant_id")
        tenant = db["tenants"].find_one({"tenant_id": tenant_id, "status": "Active"}, {"_id": 1}) if tenant_id else None
        if not tenant:
            raise HTTPException(status_code=403, detail="Your insurer tenant is inactive or unavailable.")
        claim_query["tenant_id"] = tenant_id
    claim = db["claims"].find_one(claim_query, {"_id": 0, "claim_id": 1, "claim_type": 1, "casualty_segment": 1, "tenant_id": 1})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim):
        raise HTTPException(status_code=422, detail="Adjuster reports are limited to Property and Commercial Casualty claims.")
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "claim_id": claim_id,
        "tenant_id": claim.get("tenant_id"),
        "report_title": payload.report_title,
        "report_reference": payload.report_reference,
        "report_date": payload.report_date,
        "findings": payload.findings,
        "report_fields": payload.report_fields,
        "created_at": now,
        "created_by": user["email"],
        "created_by_role": user["role"]
    }
    try:
        saved = db["adjuster_reports"].insert_one(record)
        record["report_id"] = str(saved.inserted_id)
        return record
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save adjuster report.") from exc
