from datetime import datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, Query
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
from .user_models import LoginRequest, UserCreate

app = FastAPI(
    title="Solv-In API",
    description="Solv-In Property & Casualty claims platform API",
    version="0.3.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
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

@app.get("/api/dashboard/summary")
def dashboard_summary(user: dict = Depends(require_roles(*READ_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        claims = get_database()["claims"]
        return {
            "data_mode": "live",
            "total_claims": claims.count_documents({}),
            "new_claims": claims.count_documents({"status": "FNOL Received"}),
            "under_investigation": claims.count_documents({"status": {"$in": ["Under Review", "Investigation", "Assessment"]}}),
            "pending_approval": claims.count_documents({"status": "Pending Approval"}),
            "settled_claims": claims.count_documents({"status": "Closed"})
        }
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to load dashboard metrics.") from exc

@app.get("/api/claims")
def list_claims(
    search: str | None = Query(default=None, max_length=120),
    claim_type: str | None = Query(default=None, pattern="^(Property|Casualty)$"),
    status: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=250),
    user: dict = Depends(require_roles(*READ_ROLES))
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    query = {}
    if claim_type:
        query["claim_type"] = claim_type
    if status:
        query["status"] = status
    if search:
        query["$or"] = [
            {"claim_id": {"$regex": search, "$options": "i"}},
            {"customer_name": {"$regex": search, "$options": "i"}},
            {"policy_number": {"$regex": search, "$options": "i"}}
        ]
    try:
        rows = list(get_database()["claims"].find(query, {"_id": 0}).sort("created_at", DESCENDING).limit(limit))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to read claims from MongoDB.") from exc

@app.post("/api/claims", status_code=201)
def create_claim(payload: ClaimCreate, user: dict = Depends(require_roles(*CREATE_CLAIM_ROLES))):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. No claim was saved.")
    db = get_database()
    try:
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
            "created_by": user["email"]
        })
        db["claims"].insert_one(record)
        return record
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save claim to MongoDB.") from exc

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
                "updated_by": user["email"]
            }},
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
