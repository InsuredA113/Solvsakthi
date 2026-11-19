from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from .auth import require_permissions, require_roles
from .claims_analytics import is_in_scope_claim
from .database import database_is_available, get_database
from .organisations import scoped_claim_filter

router = APIRouter()

COMMENT_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
COMMENT_WRITE_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst", "Approver"
)
ALLOWED_FIELD_KEYS = {
    "customer_name", "loss_cause", "policy_number", "email", "phone",
    "claim_type", "property_occupancy", "business_name", "industry",
    "commercial_liability_type", "incident_date", "estimated_loss", "currency",
    "loss_location", "country_code", "region", "locality", "postal_code",
    "incident_description", "status"
}


class ComparisonCommentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    comment: str = Field(min_length=2, max_length=3000)
    field_key: str | None = Field(default=None, max_length=80)
    field_label: str | None = Field(default=None, max_length=160)

    @field_validator("field_key")
    @classmethod
    def validate_field_key(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_FIELD_KEYS:
            raise ValueError("Unsupported comparison field.")
        return value


def _serialise_comment(row: dict) -> dict:
    row["comment_id"] = str(row.pop("_id"))
    return row


def _get_scoped_claim(claim_id: str, user: dict):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim = db["claims"].find_one(
        scoped_claim_filter(claim_id, user),
        {"_id": 0, "claim_id": 1, "claim_type": 1, "casualty_segment": 1, "organisation_id": 1}
    )
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim):
        raise HTTPException(status_code=422, detail="Comparison comments are limited to Property and Commercial Casualty claims.")
    return db, claim


@router.get("/api/claims/{claim_id}/comparison-comments")
def list_comparison_comments(
    claim_id: str,
    user: dict = Depends(require_permissions("reports.view"))
):
    db, claim = _get_scoped_claim(claim_id, user)
    try:
        rows = list(
            db["comparison_comments"].find({
                "claim_id": claim_id,
                "organisation_id": claim.get("organisation_id"),
            }).sort("created_at", DESCENDING).limit(200)
        )
        return {"items": [_serialise_comment(row) for row in rows], "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve comparison comments.") from exc


@router.post("/api/claims/{claim_id}/comparison-comments", status_code=201)
def create_comparison_comment(
    claim_id: str,
    payload: ComparisonCommentCreate,
    user: dict = Depends(require_permissions("reports.comment"))
):
    db, claim = _get_scoped_claim(claim_id, user)
    record = {
        "claim_id": claim_id,
        "organisation_id": claim.get("organisation_id"),
        "comment": payload.comment,
        "field_key": payload.field_key,
        "field_label": payload.field_label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user["email"],
        "created_by_role": user["role"]
    }
    try:
        saved = db["comparison_comments"].insert_one(record)
        record["comment_id"] = str(saved.inserted_id)
        record.pop("_id", None)
        return record
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save comparison comment.") from exc
