from datetime import date, datetime, timezone
import csv
import io
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response, File, UploadFile
from fastapi.responses import StreamingResponse
from gridfs import GridFSBucket
from pydantic import BaseModel, Field
from bson import ObjectId
from bson.errors import InvalidId
from fastapi.middleware.cors import CORSMiddleware
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError
from .auth import (
    ROLES, create_access_token, get_current_user, hash_password,
    normalize_email, require_roles, verify_password
)
from .bootstrap import seed_initial_admin
from .config import CORS_ORIGINS, ACCESS_TOKEN_MINUTES
from .database import get_database, database_is_available
from .models import ClaimCreate, ClaimStatusUpdate
from .claims_analytics import build_claims_analytics, is_in_scope_claim, safe_csv_cell
from .user_models import LoginRequest, UserCreate
from .repair_rate_models import RepairRateCreate, RepairRateStatusUpdate
from .email_intake import EmailIntakeCreate, EmailIntakeReview, extract_email_intake
from .email_connector_routes import router as email_connector_router
from .adjuster_report_routes import router as adjuster_report_router
from .comparison_comment_routes import router as comparison_comment_router

app = FastAPI(
    title="Solv-In API",
    description="Solv-In Property & Casualty claims platform API",
    version="0.3.0"
)
app.include_router(email_connector_router)
app.include_router(adjuster_report_router)
app.include_router(comparison_comment_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
CREATE_CLAIM_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster"
)
STATUS_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Approver"
)
ADMIN_ROLES = ("Super Administrator",)

DAMAGE_REVIEW_ROLES = ("Super Administrator", "Insurance Company Administrator", "Claims Manager", "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst", "Approver")
DAMAGE_APPROVAL_ROLES = ("Super Administrator", "Insurance Company Administrator", "Claims Manager", "Approver")

class DamageReviewUpdate(BaseModel):
    status: str = Field(..., max_length=40)
    notes: str = Field(default="", max_length=2000)



class AdjusterAssignment(BaseModel):
    adjuster_email: str = Field(..., min_length=5, max_length=254)

class FinancialEstimateReviewUpdate(BaseModel):
    status: str = Field(..., max_length=40)
    notes: str = Field(default="", max_length=2000)


@app.on_event("startup")
def startup():
    seed_initial_admin()

@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "application": "Solv-In",
        "database": "connected" if database_is_available() else "disconnected"
    }

@app.post("/api/auth/login")
def login(payload: LoginRequest):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="Authentication service is unavailable because MongoDB is not connected.")
    email = normalize_email(str(payload.email))
    user = get_database()["users"].find_one({"email": email, "is_active": True})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    safe_user = {
        "full_name": user["full_name"],
        "email": user["email"],
        "role": user["role"]
    }
    return {
        "access_token": create_access_token(safe_user),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
        "user": safe_user
    }

@app.get("/api/auth/me")
def who_am_i(user: dict = Depends(get_current_user)):
    return user

@app.get("/api/users/roles")
def list_roles(user: dict = Depends(require_roles(*ADMIN_ROLES))):
    return {"roles": list(ROLES)}

@app.get("/api/users")
def list_users(user: dict = Depends(require_roles(*ADMIN_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        rows = list(get_database()["users"].find({}, {"_id": 0, "password_hash": 0}).sort("created_at", DESCENDING))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve users.") from exc

@app.post("/api/users", status_code=201)
def create_user(payload: UserCreate, actor: dict = Depends(require_roles(*ADMIN_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    email = normalize_email(str(payload.email))
    if payload.role == "Super Administrator" and actor["role"] != "Super Administrator":
        raise HTTPException(status_code=403, detail="Only a Super Administrator can create another Super Administrator.")
    users = get_database()["users"]
    if users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="A user with this email already exists.")
    record = {
        "full_name": payload.full_name,
        "email": email,
        "password_hash": hash_password(payload.password),
        "role": payload.role,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": actor["email"]
    }
    try:
        users.insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to create user.") from exc
    return {key: value for key, value in record.items() if key != "password_hash"}

def _load_claims_analytics(
    from_date: date | None,
    to_date: date | None,
    country_code: str | None,
    currency: str | None,
    portfolio: str
):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="From date must be on or before to date.")

    common_filters = []
    date_filter = {}
    if from_date:
        date_filter["$gte"] = from_date.isoformat()
    if to_date:
        date_filter["$lte"] = to_date.isoformat()
    if date_filter:
        common_filters.append({"incident_date": date_filter})
    if country_code:
        common_filters.append({"country_code": country_code.upper()})
    if currency:
        common_filters.append({"currency": currency.upper()})

    scope_filter = {"$or": [
        {"claim_type": "Property"},
        {"claim_type": "Casualty", "casualty_segment": "Commercial"}
    ]}
    if portfolio == "Property":
        scope_filter = {"claim_type": "Property"}
    elif portfolio == "Commercial Casualty":
        scope_filter = {"claim_type": "Casualty", "casualty_segment": "Commercial"}

    query_parts = [scope_filter, *common_filters]
    query = {"$and": query_parts}
    db = get_database()
    try:
        claims_collection = db["claims"]
        matching_count = claims_collection.count_documents(query)
        if matching_count > 50000:
            raise HTTPException(
                status_code=413,
                detail="This report matches more than 50,000 claims. Narrow the date, country or currency filters; no partial report was generated."
            )
        claims = list(claims_collection.find(query, {"_id": 0}).sort("incident_date", 1))
        claim_ids = [claim.get("claim_id") for claim in claims if claim.get("claim_id")]
        estimates = []
        if claim_ids:
            estimates = list(db["financial_estimates"].aggregate([
                {"$match": {"claim_id": {"$in": claim_ids}}},
                {"$sort": {"created_at": -1}},
                {"$group": {"_id": "$claim_id", "latest": {"$first": "$$ROOT"}}},
                {"$replaceRoot": {"newRoot": "$latest"}}
            ]))

        result = build_claims_analytics(claims, estimates)
        unclassified_query_parts = [
            {"claim_type": "Casualty", "casualty_segment": {"$ne": "Commercial"}},
            *common_filters
        ]
        result["summary"]["unclassified_legacy_casualty_claims"] = claims_collection.count_documents(
            {"$and": unclassified_query_parts}
        )
        result["filters"] = {
            "from_date": from_date.isoformat() if from_date else None,
            "to_date": to_date.isoformat() if to_date else None,
            "country_code": country_code.upper() if country_code else None,
            "currency": currency.upper() if currency else None,
            "portfolio": portfolio
        }
        result["data_complete"] = True
        result["claim_count_checked"] = matching_count
        return result
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to build claims analytics.") from exc

@app.get("/api/reports/claims-analytics")
def claims_analytics(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    portfolio: str = Query(default="All", pattern="^(All|Property|Commercial Casualty)$"),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    return _load_claims_analytics(from_date, to_date, country_code, currency, portfolio)


@app.get("/api/reports/claims-analytics.csv")
def export_claims_analytics_csv(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    portfolio: str = Query(default="All", pattern="^(All|Property|Commercial Casualty)$"),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    data = _load_claims_analytics(from_date, to_date, country_code, currency, portfolio)
    output = io.StringIO()
    writer = csv.writer(output)
    def write_row(values):
        writer.writerow([safe_csv_cell(value) for value in values])
    write_row(["Section", "Dimension", "Currency", "Claim count", "Amount"])
    summary = data["summary"]
    for key, value in summary.items():
        write_row(["Summary", key, "", value, ""])
    for row in data["by_status"]:
        write_row(["Status", row["status"], "", row["claim_count"], ""])
    for row in data["by_country"]:
        write_row(["Country", row["country_code"], "", row["claim_count"], ""])
    for row in data["by_month"]:
        write_row(["Month", row["month"], "", row["claim_count"], ""])
    for row in data["by_claim_line"]:
        write_row(["Claim line", row["claim_line"], "", row["claim_count"], ""])
    for row in data["by_industry"]:
        write_row(["Commercial casualty industry", row["industry"], "", row["claim_count"], ""])
    for row in data["reported_exposure_by_currency"]:
        write_row(["Reported exposure", "Claim-reported estimated loss", row["currency"], row["claim_count"], row["reported_estimated_loss"]])
    for row in data["latest_preliminary_estimates_by_currency"]:
        write_row(["Latest preliminary estimates", "Latest estimate per claim", row["currency"], row["claim_count"], row["latest_preliminary_estimate_total"]])
    write_row(["Scope", data["scope"], "", "", ""])
    return Response(
        content=output.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="solv-in-claims-analytics.csv"'}
    )



@app.get("/api/adjusters")
def list_adjusters(user: dict = Depends(require_roles(*READ_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        rows = list(get_database()["users"].find(
            {"is_active": True, "role": {"$in": ["Property Adjuster", "Casualty Adjuster"]}},
            {"_id": 0, "full_name": 1, "email": 1, "role": 1}
        ).sort("full_name", 1))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve adjusters.") from exc


@app.patch("/api/claims/{claim_id}/assign-adjuster")
def assign_claim_adjuster(
    claim_id: str,
    payload: AdjusterAssignment,
    user: dict = Depends(require_roles("Super Administrator", "Insurance Company Administrator", "Claims Manager"))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim = db["claims"].find_one({"claim_id": claim_id})
    if not claim or (not is_in_scope_claim(claim) and claim.get("auto_intake_status") != "Provisional"):
        raise HTTPException(status_code=404, detail="Claim not found.")
    adjuster_email = normalize_email(payload.adjuster_email)
    adjuster = db["users"].find_one({"email": adjuster_email, "is_active": True})
    if not adjuster or adjuster.get("role") not in {"Property Adjuster", "Casualty Adjuster"}:
        raise HTTPException(status_code=422, detail="Select an active Property or Casualty Adjuster.")
    expected_role = "Property Adjuster" if claim.get("claim_type") == "Property" else "Casualty Adjuster" if claim.get("claim_type") == "Casualty" else None
    if expected_role and adjuster.get("role") != expected_role:
        raise HTTPException(status_code=422, detail=f"This claim must be assigned to a {expected_role}.")
    now = datetime.now(timezone.utc).isoformat()
    try:
        updated = db["claims"].find_one_and_update(
            {"claim_id": claim_id},
            {"$set": {
                "assigned_adjuster_email": adjuster_email,
                "assigned_adjuster_name": adjuster.get("full_name") or adjuster_email,
                "assigned_adjuster_role": adjuster["role"],
                "assigned_at": now,
                "assigned_by": user["email"],
                "updated_at": now,
                "updated_by": user["email"]
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0}
        )
        return updated
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to assign claim.") from exc


@app.get("/api/dashboard/analytics")
def dashboard_analytics(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    status: str | None = Query(default=None, max_length=40),
    claim_type: str = Query(default="All", pattern="^(All|Property|Casualty)$"),
    adjuster_email: str | None = Query(default=None, max_length=254),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    now = datetime.now(timezone.utc)
    query_parts = [{"$or": [
        {"claim_type": "Property"},
        {"claim_type": "Casualty", "casualty_segment": "Commercial"}
    ]}]
    if claim_type == "Property":
        query_parts.append({"claim_type": "Property"})
    elif claim_type == "Casualty":
        query_parts.append({"claim_type": "Casualty", "casualty_segment": "Commercial"})
    if status:
        query_parts.append({"status": status})
    if adjuster_email:
        query_parts.append({"assigned_adjuster_email": normalize_email(adjuster_email)})
    date_query = {}
    if year is not None:
        date_query["$gte"] = f"{year:04d}-{month or 1:02d}-01T00:00:00"
        if month is not None:
            if month == 12:
                date_query["$lt"] = f"{year + 1:04d}-01-01T00:00:00"
            else:
                date_query["$lt"] = f"{year:04d}-{month + 1:02d}-01T00:00:00"
        else:
            date_query["$lt"] = f"{year + 1:04d}-01-01T00:00:00"
        query_parts.append({"created_at": date_query})
    elif month is not None:
        query_parts.append({"created_at": {"$regex": f"^[0-9]{{4}}-{month:02d}-"}})
    try:
        claims = list(get_database()["claims"].find({"$and": query_parts}, {"_id": 0}).sort("created_at", 1).limit(50000))
        by_month = {}
        by_status = {}
        by_month_status = {}
        available_years = set()
        adjusters = {}
        overdue_count = 0
        handling_days = []
        for claim in claims:
            created = claim.get("created_at")
            created_dt = None
            if isinstance(created, str):
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            month_key = created_dt.strftime("%Y-%m") if created_dt else "Unknown"
            if created_dt:
                available_years.add(created_dt.year)
            by_month[month_key] = by_month.get(month_key, 0) + 1
            claim_status = claim.get("status") or "Unspecified"
            by_status[claim_status] = by_status.get(claim_status, 0) + 1
            by_month_status.setdefault(month_key, {})
            by_month_status[month_key][claim_status] = by_month_status[month_key].get(claim_status, 0) + 1
            email = claim.get("assigned_adjuster_email")
            key = email or "__unassigned__"
            row = adjusters.setdefault(key, {
                "adjuster_email": email,
                "adjuster_name": claim.get("assigned_adjuster_name") or ("Unassigned" if not email else email),
                "adjuster_role": claim.get("assigned_adjuster_role") or "",
                "assigned": 0, "open": 0, "pending": 0, "closed": 0,
                "overdue": 0, "completed_handling_days_total": 0.0, "completed_handling_count": 0
            })
            row["assigned"] += 1
            if claim_status == "Closed":
                row["closed"] += 1
                closed_value = claim.get("closed_at")
                if created_dt and isinstance(closed_value, str):
                    try:
                        closed_dt = datetime.fromisoformat(closed_value.replace("Z", "+00:00"))
                        if closed_dt.tzinfo is None:
                            closed_dt = closed_dt.replace(tzinfo=timezone.utc)
                        days = max(0.0, (closed_dt - created_dt).total_seconds() / 86400)
                        row["completed_handling_days_total"] += days
                        row["completed_handling_count"] += 1
                        handling_days.append(days)
                    except ValueError:
                        pass
            else:
                row["open"] += 1
                if claim_status == "Pending Approval":
                    row["pending"] += 1
                if created_dt and (now - created_dt).total_seconds() > 30 * 86400:
                    row["overdue"] += 1
                    overdue_count += 1
        adjuster_rows = []
        for row in adjusters.values():
            count = row.pop("completed_handling_count")
            total = row.pop("completed_handling_days_total")
            row["average_handling_days"] = round(total / count, 1) if count else None
            adjuster_rows.append(row)
        adjuster_rows.sort(key=lambda item: (item["adjuster_name"] == "Unassigned", item["adjuster_name"].casefold()))
        year_rows = get_database()["claims"].find(
            {"$or": [{"claim_type": "Property"}, {"claim_type": "Casualty", "casualty_segment": "Commercial"}]},
            {"_id": 0, "created_at": 1}
        )
        for year_row in year_rows:
            created_value = year_row.get("created_at")
            if isinstance(created_value, str) and len(created_value) >= 4 and created_value[:4].isdigit():
                available_years.add(int(created_value[:4]))
        return {
            "data_mode": "live",
            "filters": {"year": year, "month": month, "status": status, "claim_type": claim_type, "adjuster_email": adjuster_email},
            "summary": {
                "total_claims": len(claims),
                "open_claims": sum(1 for c in claims if c.get("status") != "Closed"),
                "closed_claims": sum(1 for c in claims if c.get("status") == "Closed"),
                "pending_claims": sum(1 for c in claims if c.get("status") == "Pending Approval"),
                "overdue_claims": overdue_count,
                "average_handling_days": round(sum(handling_days) / len(handling_days), 1) if handling_days else None
            },
            "by_month": [{"month": k, "claim_count": v} for k, v in sorted(by_month.items())],
            "by_month_status": [{"month": k, "statuses": v} for k, v in sorted(by_month_status.items())],
            "by_status": [{"status": k, "claim_count": v} for k, v in sorted(by_status.items())],
            "by_adjuster": adjuster_rows,
            "available_years": sorted(available_years, reverse=True)
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to build dashboard analytics.") from exc


@app.get("/api/dashboard/summary")
def dashboard_summary(user: dict = Depends(require_roles(*READ_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        claims = get_database()["claims"]
        scope_query = {"$or": [
            {"claim_type": "Property"},
            {"claim_type": "Casualty", "casualty_segment": "Commercial"}
        ]}
        def scoped_count(status_query=None):
            filters = [scope_query]
            if status_query:
                filters.append(status_query)
            return claims.count_documents({"$and": filters})
        return {
            "data_mode": "live",
            "total_claims": scoped_count(),
            "new_claims": scoped_count({"status": "FNOL Received"}),
            "under_investigation": scoped_count({"status": {"$in": ["Under Review", "Investigation", "Assessment"]}}),
            "pending_approval": scoped_count({"status": "Pending Approval"}),
            "settled_claims": scoped_count({"status": "Closed"}),
            "unclassified_legacy_casualty_claims": claims.count_documents({
                "claim_type": "Casualty", "casualty_segment": {"$ne": "Commercial"}
            })
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to load dashboard metrics.") from exc

@app.get("/api/claims")
def list_claims(
    search: str | None = Query(default=None, max_length=120),
    claim_type: str | None = Query(default=None, pattern="^(Property|Casualty|Unclassified)$"),
    status: str | None = Query(default=None, max_length=40),
    loss_cause: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=250),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {"$and": [{"$or": [
        {"claim_type": "Property"},
        {"claim_type": "Casualty", "casualty_segment": "Commercial"},
        {"auto_intake_status": "Provisional"}
    ]}]}
    if claim_type:
        query["$and"].append({"claim_type": claim_type})
    if status:
        query["$and"].append({"status": status})
    if loss_cause:
        query["$and"].append({"loss_cause": {"$regex": loss_cause, "$options": "i"}})
    if search:
        query["$and"].append({"$or": [
            {"claim_id": {"$regex": search, "$options": "i"}},
            {"customer_name": {"$regex": search, "$options": "i"}},
            {"loss_cause": {"$regex": search, "$options": "i"}},
            {"policy_number": {"$regex": search, "$options": "i"}}
        ]})
    try:
        rows = list(get_database()["claims"].find(query, {"_id": 0}).sort("created_at", DESCENDING).limit(limit))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to read claims from MongoDB.") from exc

@app.get("/api/claims/loss-causes")
def list_recorded_loss_causes(
    user: dict = Depends(require_roles(*READ_ROLES))
):
    """Return only non-empty loss causes already recorded on in-scope claims."""
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    scope_query = {"$or": [
        {"claim_type": "Property"},
        {"claim_type": "Casualty", "casualty_segment": "Commercial"},
        {"auto_intake_status": "Provisional"}
    ]}
    try:
        recorded_values = get_database()["claims"].distinct("loss_cause", scope_query)
        options = sorted({
            value.strip()
            for value in recorded_values
            if isinstance(value, str)
            and value.strip()
            and value.strip().casefold() not in {"not found", "unknown", "undetermined"}
        }, key=str.casefold)
        return {"items": options, "count": len(options)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to read recorded loss causes.") from exc


@app.get("/api/claims/{claim_id}")
def get_claim_details(
    claim_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        claim = get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0})
        if not claim or (not is_in_scope_claim(claim) and claim.get("auto_intake_status") != "Provisional"):
            raise HTTPException(status_code=404, detail="Claim not found.")
        return claim
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to read claim details from MongoDB.") from exc


@app.post("/api/claims", status_code=201)
def create_claim(payload: ClaimCreate, user: dict = Depends(require_roles(*CREATE_CLAIM_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. No claim was saved.")
    try:
        db = get_database()
        year = datetime.now(timezone.utc).year
        sequence = db["counters"].find_one_and_update(
            {"_id": f"claim_id_{year}"},
            {"$inc": {"sequence": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        claim_id = f"CLM-{year}-{sequence['sequence']:06d}"
        record = payload.model_dump(mode="json")
        now = datetime.now(timezone.utc).isoformat()
        record.update({
            "claim_id": claim_id,
            "status": "FNOL Received",
            "created_at": now,
            "updated_at": now,
            "created_by": user["email"],
            "created_by_name": user.get("full_name") or user["email"],
            "created_by_email": user["email"],
            "source": "Manual FNOL",
            "source_mode": "upload"
        })
        db["claims"].insert_one(record)
        return record
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save claim to MongoDB.") from exc


CLAIM_EDIT_ROLES = CREATE_CLAIM_ROLES
CLAIM_DELETE_ROLES = ("Super Administrator", "Insurance Company Administrator", "Claims Manager")
EDITABLE_CLAIM_FIELDS = {
    "customer_name", "loss_cause", "email", "phone", "policy_number",
    "claim_type", "property_occupancy", "casualty_segment", "business_name",
    "industry", "commercial_liability_type", "incident_date",
    "incident_description", "loss_location", "country_code", "region",
    "locality", "postal_code", "estimated_loss", "currency"
}

@app.patch("/api/claims/{claim_id}")
def edit_claim(
    claim_id: str,
    payload: dict = Body(...),
    user: dict = Depends(require_roles(*CLAIM_EDIT_ROLES))
):
    """Edit claim intake/business fields while preserving claim identity and audit metadata."""
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    unknown = set(payload) - EDITABLE_CLAIM_FIELDS - {"status"}
    if unknown:
        raise HTTPException(status_code=422, detail=f"Fields cannot be edited: {', '.join(sorted(unknown))}.")
    if not payload:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    collection = get_database()["claims"]
    existing = collection.find_one({"claim_id": claim_id})
    if not existing or (not is_in_scope_claim(existing) and existing.get("auto_intake_status") != "Provisional"):
        raise HTTPException(status_code=404, detail="Claim not found.")
    merged = {key: existing.get(key) for key in EDITABLE_CLAIM_FIELDS}
    merged.update({key: value for key, value in payload.items() if key in EDITABLE_CLAIM_FIELDS})
    try:
        validated = ClaimCreate.model_validate(merged).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    changes = {key: validated.get(key) for key in EDITABLE_CLAIM_FIELDS}
    if "status" in payload:
        allowed_statuses = {"FNOL Received", "Under Review", "Investigation", "Assessment", "Pending Approval", "Approved", "Closed"}
        if payload["status"] not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Invalid claim status.")
        changes["status"] = payload["status"]
    now = datetime.now(timezone.utc).isoformat()
    changes.update({"updated_at": now, "updated_by": user["email"]})
    try:
        updated = collection.find_one_and_update(
            {"claim_id": claim_id},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0}
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Claim not found.")
        return updated
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to update claim.") from exc




FIELD_EDITABLE_CLAIM_FIELDS = EDITABLE_CLAIM_FIELDS | {"status"}
FIELD_REQUIRED_CLAIM_FIELDS = {
    "customer_name", "policy_number", "claim_type", "incident_date",
    "incident_description", "loss_location", "country_code", "currency", "status"
}

@app.patch("/api/claims/{claim_id}/field/{field_name}")
def update_claim_field(
    claim_id: str,
    field_name: str,
    payload: dict = Body(...),
    user: dict = Depends(require_roles(*CLAIM_EDIT_ROLES))
):
    """Update or clear one claim field without changing any other field."""
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if field_name not in FIELD_EDITABLE_CLAIM_FIELDS:
        raise HTTPException(status_code=403, detail="This claim field is protected or cannot be edited.")
    if "value" not in payload:
        raise HTTPException(status_code=422, detail="Provide a value for this field.")
    value = payload["value"]
    if value is None or (isinstance(value, str) and not value.strip()):
        if field_name in FIELD_REQUIRED_CLAIM_FIELDS:
            raise HTTPException(status_code=422, detail="This is a required claim field and cannot be cleared.")
        value = None
    collection = get_database()["claims"]
    existing = collection.find_one({"claim_id": claim_id})
    if not existing or (not is_in_scope_claim(existing) and existing.get("auto_intake_status") != "Provisional"):
        raise HTTPException(status_code=404, detail="Claim not found.")
    if field_name == "status":
        try:
            value = ClaimStatusUpdate.model_validate({"status": value}).status
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif field_name == "estimated_loss":
        if value is not None:
            try:
                value = float(value)
                if value < 0:
                    raise ValueError("Estimated loss cannot be negative.")
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="Estimated loss must be a non-negative number.") from exc
    elif field_name == "incident_date":
        try:
            value = date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Incident date must be a valid ISO date.") from exc
    elif field_name == "currency":
        try:
            value = ClaimCreate.model_validate({
                **{key: existing.get(key) for key in EDITABLE_CLAIM_FIELDS},
                "currency": str(value).upper()
            }).currency
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif field_name == "country_code":
        try:
            value = ClaimCreate.model_validate({
                **{key: existing.get(key) for key in EDITABLE_CLAIM_FIELDS},
                "country_code": str(value).upper()
            }).country_code
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif field_name == "claim_type":
        if value not in {"Property", "Casualty"}:
            raise HTTPException(status_code=422, detail="Claim type must be Property or Casualty.")
        if value != existing.get("claim_type"):
            raise HTTPException(status_code=422, detail="Changing claim type requires updating its related claim fields together. Use the full Edit Claim form.")
    elif field_name == "commercial_liability_type":
        allowed = {"Commercial General Liability", "Professional Liability", "Product Liability", "Employers Liability", "Public Liability", "Other Commercial Liability"}
        if value is not None and value not in allowed:
            raise HTTPException(status_code=422, detail="Select a valid commercial liability type.")
    elif field_name == "property_occupancy":
        allowed = {"Residential", "Commercial", "Industrial", "Mixed Use", "Other"}
        if value is not None and value not in allowed:
            raise HTTPException(status_code=422, detail="Select a valid property occupancy.")
    elif isinstance(value, str):
        value = value.strip()
    now = datetime.now(timezone.utc).isoformat()
    try:
        updated = collection.find_one_and_update(
            {"claim_id": claim_id},
            {"$set": {field_name: value, "updated_at": now, "updated_by": user["email"]}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0}
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Claim not found.")
        return updated
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to update claim field.") from exc

@app.delete("/api/claims/{claim_id}")
def delete_claim(
    claim_id: str,
    user: dict = Depends(require_roles(*CLAIM_DELETE_ROLES))
):
    """Permanently delete a claim and its claim-linked evidence and derived assessments."""
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    existing = db["claims"].find_one({"claim_id": claim_id}, {"_id": 0, "claim_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Claim not found.")
    try:
        bucket = GridFSBucket(db, bucket_name="claim_documents")
        for stored_file in bucket.find({"metadata.claim_id": claim_id}):
            bucket.delete(stored_file._id)
        for collection_name in ("financial_estimates", "damage_assessments", "coverage_assessments"):
            db[collection_name].delete_many({"claim_id": claim_id})
        db["claims"].delete_one({"claim_id": claim_id})
        return {"deleted": True, "claim_id": claim_id}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to fully delete the claim and linked records.") from exc


@app.patch("/api/claims/{claim_id}/status")
def update_claim_status(
    claim_id: str,
    payload: ClaimStatusUpdate,
    user: dict = Depends(require_roles(*STATUS_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        updated = get_database()["claims"].find_one_and_update(
            {"claim_id": claim_id},
            {"$set": {
                "status": payload.status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "updated_by": user["email"],
                **({"closed_at": datetime.now(timezone.utc).isoformat()} if payload.status == "Closed" else {})
            }, **({"$unset": {"closed_at": ""}} if payload.status != "Closed" else {})},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0}
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Claim not found.")
        return updated
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to update claim status.") from exc



# Claim evidence documents: PDFs and image files are stored in MongoDB GridFS.
from gridfs.errors import NoFile

DOCUMENT_CONTENT_TYPES = {
    "application/pdf", "image/jpeg", "image/png", "image/webp"
}
MAX_CLAIM_DOCUMENT_BYTES = 15 * 1024 * 1024

@app.post("/api/claims/{claim_id}/documents", status_code=201)
async def upload_claim_document(
    claim_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_roles(*CREATE_CLAIM_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    claim = get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0, "claim_id": 1})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    content_type = (file.content_type or "").lower()
    if content_type not in DOCUMENT_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Allowed files: PDF, JPG, JPEG, PNG and WEBP.")
    filename = (file.filename or "evidence").replace("\\", "/").split("/")[-1]
    if not filename or len(filename) > 180:
        raise HTTPException(status_code=400, detail="File name is invalid or too long.")
    content = await file.read(MAX_CLAIM_DOCUMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The selected file is empty.")
    if len(content) > MAX_CLAIM_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Each file must be 15 MB or smaller.")
    if content_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The file content is not a valid PDF.")
    if content_type == "image/jpeg" and not content.startswith(b"\\xff\\xd8\\xff"):
        raise HTTPException(status_code=415, detail="The file content is not a valid JPEG image.")
    if content_type == "image/png" and not content.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
        raise HTTPException(status_code=415, detail="The file content is not a valid PNG image.")
    if content_type == "image/webp" and not (content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
        raise HTTPException(status_code=415, detail="The file content is not a valid WEBP image.")
    now = datetime.now(timezone.utc).isoformat()
    try:
        bucket = GridFSBucket(get_database(), bucket_name="claim_documents")
        file_id = bucket.upload_from_stream(
            filename, io.BytesIO(content),
            metadata={
                "claim_id": claim_id, "content_type": content_type,
                "size": len(content), "uploaded_at": now, "uploaded_by": user["email"]
            }
        )
        return {
            "file_id": str(file_id), "claim_id": claim_id, "filename": filename,
            "content_type": content_type, "size": len(content),
            "uploaded_at": now, "uploaded_by": user["email"]
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to store the claim document.") from exc


@app.get("/api/claims/{claim_id}/documents")
def list_claim_documents(
    claim_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if not get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Claim not found.")
    bucket = GridFSBucket(get_database(), bucket_name="claim_documents")
    rows = list(bucket.find({"metadata.claim_id": claim_id}).sort("uploadDate", DESCENDING).limit(200))
    return {"items": [{
        "file_id": str(row._id), "claim_id": claim_id, "filename": row.filename,
        "content_type": row.metadata.get("content_type", "application/octet-stream"),
        "size": row.length, "uploaded_at": row.metadata.get("uploaded_at"),
        "uploaded_by": row.metadata.get("uploaded_by")
    } for row in rows], "count": len(rows)}


@app.get("/api/claims/{claim_id}/documents/{file_id}/download")
def download_claim_document(
    claim_id: str,
    file_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        object_id = ObjectId(file_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid document ID.") from exc
    bucket = GridFSBucket(get_database(), bucket_name="claim_documents")
    try:
        stored = bucket.open_download_stream(object_id)
        if stored.metadata.get("claim_id") != claim_id:
            raise HTTPException(status_code=404, detail="Document not found for this claim.")
        content = stored.read()
        return Response(
            content=content,
            media_type=stored.metadata.get("content_type", "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{stored.filename.replace(chr(34), "")}"'}
        )
    except NoFile as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve the claim document.") from exc


# Phase 4: AI-assisted visual damage analysis
from typing import Any
import json
from fastapi import File, Form, UploadFile
from google import genai
from google.genai import types
from .config import GEMINI_API_KEY, GEMINI_MODEL

DAMAGE_ANALYSIS_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst"
)
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_DAMAGE_IMAGE_BYTES = 10 * 1024 * 1024

@app.post("/api/ai/damage-analysis")
async def analyse_damage_image(
    image: UploadFile = File(...),
    claim_id: str | None = Form(default=None, max_length=40),
    user: dict = Depends(require_roles(*DAMAGE_ANALYSIS_ROLES))
):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="AI damage analysis is not configured. Add GEMINI_API_KEY to backend/.env.")
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. The assessment cannot be saved.")
    content_type = (image.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG or WEBP image.")
    image_bytes = await image.read(MAX_DAMAGE_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(image_bytes) > MAX_DAMAGE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image size must not exceed 10 MB.")
    if claim_id:
        claim = get_database()["claims"].find_one({"claim_id": claim_id}, {
            "_id": 0, "claim_id": 1, "claim_type": 1, "casualty_segment": 1
        })
        if not claim:
            raise HTTPException(status_code=404, detail="The selected claim ID was not found.")
        if not is_in_scope_claim(claim):
            raise HTTPException(status_code=422, detail="This claim is outside Solv-In's supported scope: all Property and Commercial Casualty only.")
    prompt = """
You are an assistant supporting a licensed Property & Casualty insurance claims adjuster.
Inspect only what is visibly supported by the supplied image. Return one valid JSON object
with these keys and types:
{
  "damage_category": "short category or Undetermined",
  "affected_areas": ["visible area"],
  "visible_observations": ["objective visual observation"],
  "severity": "Minor|Moderate|Severe|Critical|Undetermined",
  "confidence": "Low|Medium|High",
  "possible_cause": "carefully qualified possibility or Undetermined",
  "recommended_next_steps": ["practical evidence or inspection step"],
  "limitations": ["what cannot be determined from this image"]
}
Do not infer policy coverage, liability, fraud, exact dimensions, hidden damage, repair quantities,
or repair costs. Do not make a final claim decision. If the image is unclear or not relevant to
property/casualty damage, state that in limitations and use Undetermined where appropriate.
Keep findings concise and distinguish observation from hypothesis.
"""
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=content_type),
                prompt
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        raw_text = response.text or ""
        result = json.loads(raw_text)
        if not isinstance(result, dict):
            raise json.JSONDecodeError("Expected a JSON object", raw_text, 0)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="The AI service returned an unreadable assessment. Please retry.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The AI damage analysis service could not complete the request.") from exc

    allowed_severity = {"Minor", "Moderate", "Severe", "Critical", "Undetermined"}
    allowed_confidence = {"Low", "Medium", "High"}
    if result.get("severity") not in allowed_severity:
        result["severity"] = "Undetermined"
    if result.get("confidence") not in allowed_confidence:
        result["confidence"] = "Low"
    for key in ("affected_areas", "visible_observations", "recommended_next_steps", "limitations"):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["damage_category"] = str(result.get("damage_category") or "Undetermined")[:120]
    result["possible_cause"] = str(result.get("possible_cause") or "Undetermined")[:500]
    record = {
        "claim_id": claim_id,
        "file_name": (image.filename or "uploaded-image")[:255],
        "mime_type": content_type,
        "damage_category": result["damage_category"],
        "affected_areas": result["affected_areas"][:20],
        "visible_observations": result["visible_observations"][:20],
        "severity": result["severity"],
        "confidence": result["confidence"],
        "possible_cause": result["possible_cause"],
        "recommended_next_steps": result["recommended_next_steps"][:20],
        "limitations": result["limitations"][:20],
        "repair_cost_estimate": None,
        "status": "Pending Adjuster Review",
        "review_history": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user["email"]
    }
    try:
        saved = get_database()["damage_assessments"].insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save the AI assessment to MongoDB.") from exc
    record.pop("_id", None)
    record["assessment_id"] = str(saved.inserted_id)
    return record

@app.get("/api/claims/{claim_id}/damage-assessments")
def list_damage_assessments(
    claim_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if not get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0}):
        raise HTTPException(status_code=404, detail="Claim not found.")
    rows = list(get_database()["damage_assessments"].find(
        {"claim_id": claim_id}
    ).sort("created_at", DESCENDING).limit(100))
    for row in rows:
        row["assessment_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}

@app.patch("/api/damage-assessments/{assessment_id}/review")
def review_damage_assessment(assessment_id: str, payload: DamageReviewUpdate, user: dict = Depends(require_roles(*DAMAGE_REVIEW_ROLES))):
    allowed_statuses = {"Reviewed", "More Evidence Required", "Approved", "Rejected"}
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Choose a valid assessment review status.")
    if payload.status in {"Approved", "Rejected"} and user["role"] not in DAMAGE_APPROVAL_ROLES:
        raise HTTPException(status_code=403, detail="Only an authorised approver can approve or reject an AI assessment.")
    if payload.status in {"More Evidence Required", "Rejected"} and not payload.notes.strip():
        raise HTTPException(status_code=422, detail="Reviewer notes are required for this decision.")
    try:
        object_id = ObjectId(assessment_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid damage assessment ID.") from exc
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    now = datetime.now(timezone.utc).isoformat()
    review_entry = {"status": payload.status, "notes": payload.notes.strip(), "reviewed_at": now, "reviewed_by": user["email"], "reviewer_role": user["role"]}
    try:
        updated = get_database()["damage_assessments"].find_one_and_update(
            {"_id": object_id, "status": {"$nin": ["Approved", "Rejected"]}},
            {"$set": {"status": payload.status, "updated_at": now, "updated_by": user["email"]}, "$push": {"review_history": review_entry}},
            return_document=ReturnDocument.AFTER, projection={"_id": 0}
        )
        if not updated:
            existing = get_database()["damage_assessments"].find_one({"_id": object_id}, {"status": 1})
            if not existing:
                raise HTTPException(status_code=404, detail="Damage assessment not found.")
            raise HTTPException(status_code=409, detail="This assessment has already been finalised.")
        updated["assessment_id"] = str(object_id)
        return updated
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save the assessment review.") from exc


# Phase 6: deterministic financial estimates (Python Decimal; no LLM arithmetic)
from .financial_calculations import FinancialEstimateRequest, TaxComponent, calculate_estimate

FINANCIAL_ESTIMATE_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst", "Approver"
)

@app.post("/api/claims/{claim_id}/financial-estimates", status_code=201)
def create_financial_estimate(
    claim_id: str,
    payload: FinancialEstimateRequest,
    user: dict = Depends(require_roles(*FINANCIAL_ESTIMATE_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. The estimate was not saved.")
    db = get_database()
    claim = db["claims"].find_one({"claim_id": claim_id}, {
        "_id": 0, "claim_id": 1, "currency": 1, "country_code": 1,
        "loss_location": 1, "region": 1, "locality": 1, "incident_date": 1,
        "claim_type": 1, "casualty_segment": 1
    })
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim):
        raise HTTPException(status_code=422, detail="Financial estimates are limited to all Property and Commercial Casualty claims.")
    source_damage_assessment = None
    if payload.source_damage_assessment_id:
        if claim.get("claim_type") != "Property":
            raise HTTPException(status_code=422, detail="AI damage assessment linking is currently available for Property claims only.")
        try:
            damage_object_id = ObjectId(payload.source_damage_assessment_id)
        except InvalidId as exc:
            raise HTTPException(status_code=422, detail="Invalid source damage assessment ID.") from exc
        source_damage_assessment = db["damage_assessments"].find_one({"_id": damage_object_id, "claim_id": claim_id})
        if not source_damage_assessment:
            raise HTTPException(status_code=404, detail="The selected damage assessment was not found for this claim.")
        if source_damage_assessment.get("status") not in {"Reviewed", "Approved"}:
            raise HTTPException(status_code=409, detail="The AI damage assessment must be reviewed by an authorised adjuster before it can be used in a financial estimate.")
    if (claim.get("currency") or "").upper() != payload.currency:
        raise HTTPException(status_code=422, detail="Estimate currency must match the claim currency.")
    if (claim.get("country_code") and claim["country_code"].upper() != payload.country_code):
        raise HTTPException(status_code=422, detail="Estimate country must match the claim's recorded loss country.")
    # Resolve every selected catalogue item server-side. Never trust a browser-supplied rate.
    from decimal import Decimal
    verified_line_items = []
    repair_rate_snapshots = []
    pricing_date = date.fromisoformat(payload.loss_date)
    for item in payload.line_items:
        if not item.repair_rate_id:
            verified_line_items.append(item)
            continue
        if claim.get("claim_type") != "Property":
            raise HTTPException(status_code=422, detail="Repair catalogue pricing is currently limited to Property claims.")
        try:
            rate_object_id = ObjectId(item.repair_rate_id)
        except InvalidId as exc:
            raise HTTPException(status_code=422, detail="Invalid repair catalogue rate ID.") from exc
        rate = db["repair_rates"].find_one({"_id": rate_object_id, "status": "Active"})
        if not rate:
            raise HTTPException(status_code=409, detail="The selected repair rate is no longer active. Refresh the catalogue.")
        def location_key(value):
            return " ".join(str(value or "").strip().casefold().split())
        if (rate.get("country_code", "").upper() != payload.country_code
            or rate.get("currency", "").upper() != payload.currency
            or location_key(rate.get("state")) != location_key(claim.get("region"))
            or location_key(rate.get("city")) != location_key(claim.get("locality"))):
            raise HTTPException(status_code=409, detail="The selected repair rate does not match this claim's country, currency, state and city.")
        try:
            effective_from = date.fromisoformat(str(rate["effective_from"])[:10])
            effective_to = date.fromisoformat(str(rate["effective_to"])[:10]) if rate.get("effective_to") else None
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="The selected repair rate has invalid effective dates.") from exc
        if effective_from > pricing_date or (effective_to and effective_to < pricing_date):
            raise HTTPException(status_code=409, detail="The selected repair rate was not effective on the claim loss date.")
        verified_rate = Decimal(str(rate["unit_rate"]))
        verified_line_items.append(item.model_copy(update={
            "description": rate["item_name"], "unit": rate["unit"], "unit_rate": verified_rate
        }))
        repair_rate_snapshots.append({
            "rate_id": str(rate["_id"]), "version": rate.get("version", 1),
            "country_code": rate["country_code"], "currency": rate["currency"],
            "state": rate["state"], "city": rate["city"], "category": rate["category"],
            "item_code": rate["item_code"], "item_name": rate["item_name"],
            "unit": rate["unit"], "unit_rate": str(verified_rate),
            "effective_from": str(rate["effective_from"])[:10],
            "effective_to": str(rate["effective_to"])[:10] if rate.get("effective_to") else None,
            "source_name": rate.get("source_name"), "source_reference": rate.get("source_reference"),
            "source_url": rate.get("source_url"), "verified_by": rate.get("verified_by"),
            "verified_at": rate.get("verified_at")
        })
    calculation_payload = payload.model_copy(update={"line_items": verified_line_items})
    tax_rule_snapshots = []
    if payload.tax_mode == "automatic":
        try:
            point_date = date.fromisoformat(payload.tax_point_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Tax point date must use YYYY-MM-DD.") from exc
        rules = list(db["tax_rules"].find({
            "country_code": payload.country_code,
            "currency": payload.currency,
            "status": "Active"
        }))
        selected_rules, ambiguous_tax_names = select_applicable_tax_rules(
            rules, country_code=payload.country_code, currency=payload.currency,
            admin_area=payload.state_or_jurisdiction, region=payload.region,
            locality=payload.locality, tax_point_date=point_date
        )
        if ambiguous_tax_names:
            raise HTTPException(status_code=409, detail={
                "message": "Conflicting active tax rules exist. Resolve the jurisdiction profile before calculating automatically.",
                "ambiguous_tax_names": ambiguous_tax_names
            })
        if selected_rules and not payload.confirm_automatic_tax_application:
            raise HTTPException(status_code=428, detail={
                "message": "Review the matched tax rules and explicitly confirm their application before saving this estimate.",
                "required_confirmation": "confirm_automatic_tax_application",
                "matched_tax_names": [rule["tax_name"] for rule in selected_rules],
                "tax_point_date": payload.tax_point_date
            })
        automatic_taxes = []
        for rule in selected_rules:
            automatic_taxes.append(TaxComponent(
                name=rule["tax_name"], tax_type=rule["tax_type"],
                jurisdiction=" / ".join(part for part in [
                    rule.get("admin_area", ""), rule.get("region", ""),
                    rule.get("locality", ""), rule["country_code"]
                ] if part),
                rate_percent=rule["rate_percent"], applies_to=rule["applies_to"],
                compound_on_prior_taxes=bool(rule.get("compound_on_prior_taxes", False)),
                authority_reference=rule["authority_reference"],
                effective_from=str(rule["effective_from"])[:10],
                effective_to=str(rule["effective_to"])[:10] if rule.get("effective_to") else None
            ))
            tax_rule_snapshots.append({
                "rule_id": str(rule["_id"]), "version": rule.get("version", 1),
                "country_code": rule["country_code"], "currency": rule["currency"],
                "admin_area": rule.get("admin_area", ""), "region": rule.get("region", ""),
                "locality": rule.get("locality", ""), "tax_name": rule["tax_name"],
                "tax_type": rule["tax_type"], "rate_percent": str(rule["rate_percent"]),
                "calculation_order": rule.get("calculation_order", 100),
                "applies_to": rule["applies_to"],
                "compound_on_prior_taxes": bool(rule.get("compound_on_prior_taxes", False)),
                "effective_from": str(rule["effective_from"])[:10],
                "effective_to": str(rule["effective_to"])[:10] if rule.get("effective_to") else None,
                "authority_name": rule["authority_name"],
                "authority_reference": rule["authority_reference"],
                "source_url": rule.get("source_url"),
                "verified_by": rule.get("verified_by"), "verified_at": rule.get("verified_at")
            })
        calculation_payload = payload.model_copy(update={"taxes": automatic_taxes})
    result = calculate_estimate(calculation_payload)
    result["tax_mode"] = payload.tax_mode
    result["tax_profile_status"] = (
        "Configured" if tax_rule_snapshots else
        ("No verified matching tax profile" if payload.tax_mode == "automatic" else "Manual tax override - verify before approval")
    )
    result["tax_rule_snapshots"] = tax_rule_snapshots
    result["tax_rule_ids"] = [rule["rule_id"] for rule in tax_rule_snapshots]
    result["repair_rate_snapshots"] = repair_rate_snapshots
    result["repair_rate_ids"] = [rate["rate_id"] for rate in repair_rate_snapshots]
    result["tax_rule_warning"] = (
        None if tax_rule_snapshots or payload.tax_mode == "manual"
        else "No active verified tax rule matched this claim jurisdiction and tax point date. No tax has been assumed; confirm whether tax applies before approval."
    )
    now = datetime.now(timezone.utc).isoformat()
    record = {
        **result, "claim_id": claim_id, "claim_type": claim.get("claim_type"),
        "source_damage_assessment": ({
            "assessment_id": str(source_damage_assessment["_id"]),
            "status": source_damage_assessment.get("status"),
            "damage_category": source_damage_assessment.get("damage_category"),
            "affected_areas": source_damage_assessment.get("affected_areas", []),
            "visible_observations": source_damage_assessment.get("visible_observations", []),
            "confidence": source_damage_assessment.get("confidence"),
            "created_at": source_damage_assessment.get("created_at")
        } if source_damage_assessment else None),
        "created_at": now, "created_by": user["email"], "status": "Preliminary - Pending Review",
        "tax_application_confirmation": {
            "confirmed": bool(payload.tax_mode == "automatic" and payload.confirm_automatic_tax_application and tax_rule_snapshots),
            "confirmed_by": user["email"] if payload.tax_mode == "automatic" and payload.confirm_automatic_tax_application and tax_rule_snapshots else None,
            "confirmed_at": now if payload.tax_mode == "automatic" and payload.confirm_automatic_tax_application and tax_rule_snapshots else None
        },
        "review_history": []
    }
    try:
        saved = db["financial_estimates"].insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save financial estimate.") from exc
    record["estimate_id"] = str(saved.inserted_id)
    record.pop("_id", None)
    return record

@app.get("/api/claims/{claim_id}/financial-estimates")
def list_financial_estimates(
    claim_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if not get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Claim not found.")
    rows = list(get_database()["financial_estimates"].find({"claim_id": claim_id}).sort("created_at", DESCENDING).limit(100))
    for row in rows:
        row["estimate_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}


@app.patch("/api/claims/{claim_id}/financial-estimates/{estimate_id}/review")
def review_financial_estimate(
    claim_id: str,
    estimate_id: str,
    payload: FinancialEstimateReviewUpdate,
    user: dict = Depends(require_roles(
        "Super Administrator", "Insurance Company Administrator", "Claims Manager",
        "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst", "Approver"
    ))
):
    allowed = {"Reviewed", "More Evidence Required", "Approved", "Rejected"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail=f"Status must be one of: {', '.join(sorted(allowed))}.")
    if payload.status in {"More Evidence Required", "Rejected"} and not payload.notes.strip():
        raise HTTPException(status_code=422, detail="Reviewer notes are required for this decision.")
    if payload.status in {"Approved", "Rejected"} and user.get("role") not in {
        "Super Administrator", "Insurance Company Administrator", "Claims Manager", "Approver"
    }:
        raise HTTPException(status_code=403, detail="Your role cannot approve or reject financial estimates.")
    try:
        object_id = ObjectId(estimate_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid financial estimate ID.") from exc
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    collection = get_database()["financial_estimates"]
    existing = collection.find_one({"_id": object_id, "claim_id": claim_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Financial estimate not found for this claim.")
    if existing.get("review_status") in {"Approved", "Rejected"}:
        raise HTTPException(status_code=409, detail="This estimate review is final. Create a new estimate to make revised calculations.")
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "status": payload.status, "notes": payload.notes.strip(),
        "reviewer_email": user["email"], "reviewer_role": user["role"], "reviewed_at": now
    }
    updated = collection.find_one_and_update(
        {"_id": object_id, "claim_id": claim_id, "review_status": existing.get("review_status")},
        {"$set": {"review_status": payload.status, "updated_at": now},
         "$push": {"review_history": event}},
        return_document=ReturnDocument.AFTER
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Estimate changed concurrently. Refresh and retry.")
    updated["estimate_id"] = str(updated.pop("_id"))
    return updated


# Phase 9: policy records and preliminary coverage screening
from .policy_coverage import PolicyCreate, CoverageAssessmentRequest, assess_policy

POLICY_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
POLICY_ADMIN_ROLES = ("Super Administrator", "Insurance Company Administrator")


@app.get("/api/policies")
def list_policies(
    policy_number: str | None = Query(default=None, max_length=80),
    user: dict = Depends(require_roles(*POLICY_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {"policy_number": policy_number.strip()} if policy_number else {}
    rows = list(get_database()["policies"].find(query).sort("created_at", DESCENDING).limit(250))
    for row in rows:
        row["policy_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}


@app.post("/api/policies", status_code=201)
def create_policy(
    payload: PolicyCreate,
    user: dict = Depends(require_roles(*POLICY_ADMIN_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    collection = get_database()["policies"]
    if collection.find_one({"policy_number": payload.policy_number}):
        raise HTTPException(status_code=409, detail="A policy with this policy number already exists in the current workspace.")
    record = payload.model_dump(mode="json")
    record.update({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user["email"]
    })
    try:
        saved = collection.insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save policy record.") from exc
    record["policy_id"] = str(saved.inserted_id)
    record.pop("_id", None)
    return record


@app.post("/api/claims/{claim_id}/coverage-assessments", status_code=201)
def create_coverage_assessment(
    claim_id: str,
    payload: CoverageAssessmentRequest,
    user: dict = Depends(require_roles(*POLICY_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    claim = db["claims"].find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if not is_in_scope_claim(claim):
        raise HTTPException(status_code=422, detail="Coverage screening is limited to all Property and Commercial Casualty claims.")
    policy = db["policies"].find_one({"policy_number": claim.get("policy_number")})
    result = assess_policy(policy, claim, payload)
    now = datetime.now(timezone.utc).isoformat()
    policy_snapshot = None
    if policy:
        policy_snapshot = {
            "policy_number": policy.get("policy_number"),
            "claim_type": policy.get("claim_type"),
            "country_code": policy.get("country_code"),
            "currency": policy.get("currency"),
            "effective_from": str(policy.get("effective_from", ""))[:10],
            "effective_to": str(policy.get("effective_to", ""))[:10],
            "per_occurrence_limit": str(policy.get("per_occurrence_limit")),
            "aggregate_limit": str(policy.get("aggregate_limit")) if policy.get("aggregate_limit") is not None else None,
            "aggregate_used": str(policy.get("aggregate_used", "0")),
            "deductible": str(policy.get("deductible", "0")),
            "excluded_loss_categories": policy.get("excluded_loss_categories", []),
            "covered_loss_categories": policy.get("covered_loss_categories", []),
            "endorsements": policy.get("endorsements", []),
            "policy_wording_reference": policy.get("policy_wording_reference")
        }
    record = {
        **result, "claim_id": claim_id, "policy_number": claim.get("policy_number"),
        "policy_snapshot": policy_snapshot, "loss_category": payload.loss_category,
        "claimed_amount": str(payload.claimed_amount) if payload.claimed_amount is not None else None,
        "notes": payload.notes, "created_at": now, "created_by": user["email"],
        "assessment_type": "Preliminary coverage screening"
    }
    try:
        saved = db["coverage_assessments"].insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save coverage assessment.") from exc
    record["coverage_assessment_id"] = str(saved.inserted_id)
    record.pop("_id", None)
    return record


@app.get("/api/claims/{claim_id}/coverage-assessments")
def list_coverage_assessments(
    claim_id: str,
    user: dict = Depends(require_roles(*POLICY_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if not get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Claim not found.")
    rows = list(get_database()["coverage_assessments"].find({"claim_id": claim_id}).sort("created_at", DESCENDING).limit(100))
    for row in rows:
        row["coverage_assessment_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}


# Phase 7: verified global tax rules and jurisdiction profiles
from datetime import date
from .tax_rules import TaxRuleCreate, TaxRuleStatusUpdate, rule_is_applicable, rule_specificity, select_applicable_tax_rules

TAX_RULE_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst", "Approver", "Auditor"
)
TAX_RULE_ADMIN_ROLES = ("Super Administrator", "Insurance Company Administrator")

@app.get("/api/tax-rules")
def list_tax_rules(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    status: str | None = Query(default=None, pattern="^(Draft|Active|Inactive)$"),
    user: dict = Depends(require_roles(*TAX_RULE_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {}
    if country_code:
        query["country_code"] = country_code.upper()
    if status:
        query["status"] = status
    rows = list(get_database()["tax_rules"].find(query).sort([("country_code", 1), ("admin_area", 1), ("tax_name", 1)]).limit(500))
    for row in rows:
        row["rule_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}

@app.post("/api/tax-rules", status_code=201)
def create_tax_rule(payload: TaxRuleCreate, user: dict = Depends(require_roles(*TAX_RULE_ADMIN_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    now = datetime.now(timezone.utc).isoformat()
    record = payload.model_dump(mode="json")
    record.update({
        "status": "Draft", "created_at": now, "created_by": user["email"],
        "verified_by": None, "verified_at": None, "activated_at": None,
        "version": 1
    })
    try:
        saved = get_database()["tax_rules"].insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save tax rule.") from exc
    record["rule_id"] = str(saved.inserted_id)
    record.pop("_id", None)
    return record

@app.patch("/api/tax-rules/{rule_id}/status")
def change_tax_rule_status(
    rule_id: str,
    payload: TaxRuleStatusUpdate,
    user: dict = Depends(require_roles(*TAX_RULE_ADMIN_ROLES))
):
    try:
        object_id = ObjectId(rule_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid tax rule ID.") from exc
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    collection = get_database()["tax_rules"]
    existing = collection.find_one({"_id": object_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Tax rule not found.")
    if existing.get("status") == "Active" and payload.status == "Active":
        raise HTTPException(status_code=409, detail="Active tax rules are immutable. Create a new version for any change.")
    now = datetime.now(timezone.utc).isoformat()
    changes = {"status": payload.status, "status_updated_at": now, "status_updated_by": user["email"]}
    if payload.status == "Active":
        if not existing.get("authority_reference") or not existing.get("authority_name"):
            raise HTTPException(status_code=422, detail="An official authority and reference are required before activation.")
        if not existing.get("source_url") and len(existing.get("authority_reference", "")) < 3:
            raise HTTPException(status_code=422, detail="A source reference is required before activation.")
        changes.update({
            "verified_by": user["email"], "verified_at": now, "activated_at": now,
            "verification_note": payload.verification_note.strip()
        })
    updated = collection.find_one_and_update(
        {"_id": object_id, "status": existing.get("status")},
        {"$set": changes}, return_document=ReturnDocument.AFTER, projection={"_id": 0}
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Tax rule changed concurrently. Refresh and retry.")
    updated["rule_id"] = rule_id
    return updated

@app.get("/api/claims/{claim_id}/applicable-tax-rules")
def applicable_tax_rules(
    claim_id: str,
    tax_point_date: str = Query(..., min_length=10, max_length=10),
    user: dict = Depends(require_roles(*TAX_RULE_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    claim = get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    try:
        point_date = date.fromisoformat(tax_point_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Tax point date must use YYYY-MM-DD.") from exc
    candidates = list(get_database()["tax_rules"].find({
        "country_code": (claim.get("country_code") or "").upper(),
        "currency": (claim.get("currency") or "").upper(),
        "status": "Active"
    }))
    selected, ambiguous = select_applicable_tax_rules(
        candidates, country_code=claim.get("country_code", ""), currency=claim.get("currency", ""),
        admin_area=claim.get("state_or_jurisdiction", "") or claim.get("region", ""),
        region=claim.get("region", ""), locality=claim.get("locality", ""), tax_point_date=point_date
    )
    for row in selected:
        row["rule_id"] = str(row.pop("_id"))
    return {
        "claim_id": claim_id, "country_code": claim.get("country_code"),
        "currency": claim.get("currency"), "tax_point_date": tax_point_date,
        "items": selected, "count": len(selected), "ambiguous_tax_names": ambiguous,
        "profile_status": "Ambiguous" if ambiguous else ("Configured" if selected else "No verified matching tax profile"),
        "message": "Only active, verified, effective-dated rules are returned. This lookup is advisory; confirm the applicable tax treatment with the authorised tax/legal source."
    }



# Phase 12: geographically scoped, verified Property repair-rate catalogue
REPAIR_RATE_READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Surveyor", "Actuarial Analyst", "Approver", "Auditor"
)
REPAIR_RATE_ADMIN_ROLES = ("Super Administrator", "Insurance Company Administrator")


@app.get("/api/repair-rates")
def list_repair_rates(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    state: str | None = Query(default=None, max_length=100),
    city: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, pattern="^(Draft|Active|Inactive)$"),
    user: dict = Depends(require_roles(*REPAIR_RATE_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {}
    if country_code:
        query["country_code"] = country_code.strip().upper()
    if state:
        query["state"] = {"$regex": f"^{state.strip()}$", "$options": "i"}
    if city:
        query["city"] = {"$regex": f"^{city.strip()}$", "$options": "i"}
    if category:
        query["category"] = {"$regex": f"^{category.strip()}$", "$options": "i"}
    if status:
        query["status"] = status
    rows = list(get_database()["repair_rates"].find(query).sort([
        ("country_code", 1), ("state", 1), ("city", 1), ("category", 1), ("item_name", 1)
    ]).limit(1000))
    for row in rows:
        row["rate_id"] = str(row.pop("_id"))
        row["unit_rate"] = str(row["unit_rate"])
    return {"items": rows, "count": len(rows)}


@app.post("/api/repair-rates", status_code=201)
def create_repair_rate(
    payload: RepairRateCreate,
    user: dict = Depends(require_roles(*REPAIR_RATE_ADMIN_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if payload.effective_to and payload.effective_to < payload.effective_from:
        raise HTTPException(status_code=422, detail="Effective-to date cannot be earlier than effective-from date.")
    now = datetime.now(timezone.utc).isoformat()
    record = payload.model_dump(mode="json")
    record["unit_rate"] = str(payload.unit_rate)
    record.update({
        "status": "Draft", "version": 1, "created_at": now, "created_by": user["email"],
        "verified_by": None, "verified_at": None, "activated_at": None,
        "verification_note": None
    })
    try:
        saved = get_database()["repair_rates"].insert_one(record)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save repair rate.") from exc
    record["rate_id"] = str(saved.inserted_id)
    record.pop("_id", None)
    return record


@app.patch("/api/repair-rates/{rate_id}/status")
def change_repair_rate_status(
    rate_id: str,
    payload: RepairRateStatusUpdate,
    user: dict = Depends(require_roles(*REPAIR_RATE_ADMIN_ROLES))
):
    try:
        object_id = ObjectId(rate_id)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail="Invalid repair-rate ID.") from exc
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    collection = get_database()["repair_rates"]
    existing = collection.find_one({"_id": object_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Repair rate not found.")
    if existing.get("status") == "Active" and payload.status == "Active":
        raise HTTPException(status_code=409, detail="Active repair rates are immutable. Add a new rate version for any change.")
    now = datetime.now(timezone.utc).isoformat()
    changes = {
        "status": payload.status, "status_updated_at": now,
        "status_updated_by": user["email"], "verification_note": payload.verification_note.strip()
    }
    if payload.status == "Active":
        if not existing.get("source_name") or not existing.get("source_reference"):
            raise HTTPException(status_code=422, detail="A named pricing source and source reference are required before activation.")
        changes.update({"verified_by": user["email"], "verified_at": now, "activated_at": now})
    updated = collection.find_one_and_update(
        {"_id": object_id, "status": existing.get("status")},
        {"$set": changes}, return_document=ReturnDocument.AFTER, projection={"_id": 0}
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Repair rate changed concurrently. Refresh and retry.")
    updated["rate_id"] = rate_id
    updated["unit_rate"] = str(updated["unit_rate"])
    return updated


@app.get("/api/claims/{claim_id}/applicable-repair-rates")
def applicable_repair_rates(
    claim_id: str,
    category: str | None = Query(default=None, max_length=80),
    user: dict = Depends(require_roles(*REPAIR_RATE_READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    claim = get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0})
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if claim.get("claim_type") != "Property":
        raise HTTPException(status_code=422, detail="Repair-rate lookup is currently available for Property claims only.")
    country = (claim.get("country_code") or "").upper()
    currency = (claim.get("currency") or "").upper()
    state = (claim.get("region") or "").strip()
    city = (claim.get("locality") or "").strip()
    query = {"country_code": country, "currency": currency, "status": "Active"}
    if state:
        query["state"] = {"$regex": f"^{state}$", "$options": "i"}
    if city:
        query["city"] = {"$regex": f"^{city}$", "$options": "i"}
    if category:
        query["category"] = {"$regex": f"^{category.strip()}$", "$options": "i"}
    rows = list(get_database()["repair_rates"].find(query).sort([
        ("category", 1), ("item_name", 1), ("effective_from", -1)
    ]).limit(500))
    try:
        pricing_date = date.fromisoformat(str(claim.get("incident_date") or date.today().isoformat())[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Claim incident date must use YYYY-MM-DD before matching repair rates.") from exc
    applicable = []
    for row in rows:
        start = row.get("effective_from")
        end = row.get("effective_to")
        try:
            start_date = date.fromisoformat(str(start)[:10])
            end_date = date.fromisoformat(str(end)[:10]) if end else None
        except ValueError:
            continue
        if start_date <= pricing_date and (end_date is None or end_date >= pricing_date):
            row["rate_id"] = str(row.pop("_id"))
            row["unit_rate"] = str(row["unit_rate"])
            applicable.append(row)
    return {
        "claim_id": claim_id, "country_code": country, "currency": currency,
        "state": state, "city": city, "pricing_date": pricing_date.isoformat(), "items": applicable, "count": len(applicable),
        "message": "Only active rates matching the claim's exact country, state and city are returned. Verify rate applicability and the pricing source before use."
    }


# Phase 11: Email FNOL intake. Incoming messages remain drafts until reviewed.
EMAIL_INTAKE_REVIEW_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager", "Approver"
)

@app.post("/api/email-intake", status_code=201)
def receive_email_intake(
    payload: EmailIntakeCreate,
    user: dict = Depends(require_roles(*CREATE_CLAIM_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. The email was not queued.")
    collection = get_database()["email_intake"]
    try:
        collection.create_index([("provider", 1), ("provider_message_id", 1)], unique=True)
        existing = collection.find_one({
            "provider": payload.provider,
            "provider_message_id": payload.provider_message_id
        }, {"_id": 0, "body_text": 0})
        if existing:
            return {"duplicate": True, "item": existing}
        parsed = extract_email_intake(payload.subject, payload.body_text)
        received_at = (payload.received_at or datetime.now(timezone.utc)).isoformat()
        record = {
            "intake_id": str(ObjectId()),
            "provider": payload.provider,
            "provider_message_id": payload.provider_message_id,
            "sender_email": str(payload.sender_email),
            "subject": payload.subject,
            "body_text": payload.body_text,
            "received_at": received_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": user["email"],
            **parsed,
            "received_at": received_at,
            "review_status": parsed["intake_status"],
            "review_history": [],
        }
        collection.insert_one(record)
        record.pop("_id", None)
        record.pop("body_text", None)
        return {"duplicate": False, "item": record}
    except HTTPException:
        raise
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError
        if isinstance(exc, DuplicateKeyError):
            existing = collection.find_one({
                "provider": payload.provider,
                "provider_message_id": payload.provider_message_id
            }, {"_id": 0, "body_text": 0})
            return {"duplicate": True, "item": existing}
        if isinstance(exc, PyMongoError):
            raise HTTPException(status_code=503, detail="Unable to store the incoming email.") from exc
        raise


@app.get("/api/email-intake")
def list_email_intake(
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=250),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {"review_status": status} if status else {}
    try:
        rows = list(get_database()["email_intake"].find(
            query, {"_id": 0, "body_text": 0}
        ).sort("received_at", DESCENDING).limit(limit))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve email intake records.") from exc


@app.get("/api/email-intake/{intake_id}")
def get_email_intake(
    intake_id: str,
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    record = get_database()["email_intake"].find_one({"intake_id": intake_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Email intake record not found.")
    return record


@app.patch("/api/email-intake/{intake_id}/review")
def review_email_intake(
    intake_id: str,
    payload: EmailIntakeReview,
    user: dict = Depends(require_roles(*EMAIL_INTAKE_REVIEW_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    if payload.status == "Rejected" and not payload.notes.strip():
        raise HTTPException(status_code=422, detail="A reason is required when rejecting an email intake.")
    collection = get_database()["email_intake"]
    record = collection.find_one({"intake_id": intake_id})
    if not record:
        raise HTTPException(status_code=404, detail="Email intake record not found.")
    if record.get("review_status") == "Out of Scope":
        raise HTTPException(status_code=409, detail="Out-of-scope messages cannot be marked as reviewed.")
    if record.get("claim_id") or record.get("review_status") in ("Rejected", "Converted"):
        raise HTTPException(status_code=409, detail="This email intake record is locked.")
    confirmed_type = payload.confirmed_claim_type or record.get("confirmed_claim_type") or record.get("suggested_claim_type")
    if payload.status == "Reviewed" and confirmed_type not in ("Property", "Commercial Casualty"):
        raise HTTPException(status_code=422, detail="Confirm a supported Property or Commercial Casualty classification before review.")
    history = record.get("review_history", [])
    history.append({
        "status": payload.status, "notes": payload.notes.strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": user["email"], "reviewer_role": user["role"]
    })
    updated = collection.find_one_and_update(
        {"intake_id": intake_id, "claim_id": {"$exists": False}},
        {"$set": {"review_status": payload.status, "review_history": history,
                  "confirmed_claim_type": confirmed_type if payload.status == "Reviewed" else record.get("confirmed_claim_type"),
                  "reviewed_at": datetime.now(timezone.utc).isoformat(),
                  "reviewed_by": user["email"]}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0, "body_text": 0}
    )
    if not updated:
        raise HTTPException(status_code=409, detail="The email intake record changed; refresh and try again.")
    return updated


@app.post("/api/email-intake/{intake_id}/create-claim", status_code=201)
def create_claim_from_email_intake(
    intake_id: str,
    payload: ClaimCreate,
    user: dict = Depends(require_roles(*CREATE_CLAIM_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    collection = db["email_intake"]
    try:
        db["claims"].create_index("source_intake_id", unique=True, sparse=True)
        existing_claim = db["claims"].find_one({"source_intake_id": intake_id}, {"_id": 0})
        if existing_claim:
            collection.update_one({"intake_id": intake_id}, {"$set": {
                "review_status": "Converted", "conversion_state": "Completed",
                "claim_id": existing_claim["claim_id"]
            }})
            return existing_claim
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to verify email-to-claim conversion state.") from exc
    record = collection.find_one_and_update(
        {"intake_id": intake_id, "review_status": "Reviewed",
         "claim_id": {"$exists": False}, "conversion_state": {"$ne": "Processing"}},
        {"$set": {"conversion_state": "Processing"}},
        return_document=ReturnDocument.AFTER
    )
    if not record:
        raise HTTPException(status_code=409, detail="The email must be reviewed first and must not already be converted.")
    confirmed_type = record.get("confirmed_claim_type") or record.get("suggested_claim_type")
    if ((confirmed_type == "Property" and payload.claim_type != "Property") or
        (confirmed_type == "Commercial Casualty" and
         (payload.claim_type != "Casualty" or payload.casualty_segment != "Commercial"))):
        collection.update_one({"intake_id": intake_id}, {"$set": {"conversion_state": "Failed"}})
        raise HTTPException(status_code=422, detail="The claim type must match the reviewed email classification.")
    db = get_database()
    try:
        year = datetime.now(timezone.utc).year
        sequence = db["counters"].find_one_and_update(
            {"_id": f"claim_id_{year}"}, {"$inc": {"sequence": 1}},
            upsert=True, return_document=ReturnDocument.AFTER
        )
        claim_id = f"CLM-{year}-{sequence['sequence']:06d}"
        now = datetime.now(timezone.utc).isoformat()
        claim_record = payload.model_dump(mode="json")
        claim_record.update({
            "claim_id": claim_id, "status": "FNOL Received",
            "created_at": now, "updated_at": now,
            "created_by": user["email"],
            "created_by_name": user.get("full_name") or user["email"],
            "created_by_email": user["email"],
            "source": "Email FNOL",
            "source_mode": "email",
            "source_intake_id": intake_id
        })
        db["claims"].insert_one(claim_record)
        collection.update_one(
            {"intake_id": intake_id, "conversion_state": "Processing"},
            {"$set": {"review_status": "Converted", "conversion_state": "Completed",
                      "claim_id": claim_id, "converted_at": now,
                      "converted_by": user["email"]}}
        )
        return claim_record
    except PyMongoError as exc:
        existing_claim = db["claims"].find_one({"source_intake_id": intake_id}, {"_id": 0})
        if existing_claim:
            collection.update_one({"intake_id": intake_id}, {"$set": {
                "review_status": "Converted", "conversion_state": "Completed",
                "claim_id": existing_claim["claim_id"]
            }})
            return existing_claim
        collection.update_one({"intake_id": intake_id, "conversion_state": "Processing"},
                              {"$set": {"conversion_state": "Failed"}})
        raise HTTPException(status_code=503, detail="Unable to create the claim from this email.") from exc


# US state water-damage restoration tax research matrix (read-only tracker).
from .state_tax_matrix import US_STATES

@app.get("/api/tax-research/us-water-restoration")
def get_us_water_restoration_tax_matrix(
    user: dict = Depends(require_roles(*READ_ROLES))
):
    """Return 50-state official-source research status; this endpoint never supplies rates."""
    items = [dict(state) for state in US_STATES]
    return {
        "country_code": "US",
        "category": "Water damage restoration",
        "state_count": len(items),
        "verified_state_count": sum(1 for item in items if item["status"].startswith("Verified")),
        "partially_researched_state_count": sum(1 for item in items if item["status"].startswith("Partially")),
        "pending_state_count": sum(1 for item in items if item["status"].startswith("Pending")),
        "automatic_tax_enabled": False,
        "items": items,
        "notice": "Research tracker only. Pending or conditional findings do not determine tax due. No tax rate is supplied or applied by this endpoint."
    }
