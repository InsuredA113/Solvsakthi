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
        claim = get_database()["claims"].find_one({"claim_id": claim_id}, {"_id": 0, "claim_id": 1})
        if not claim:
            raise HTTPException(status_code=404, detail="The selected claim ID was not found.")
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
        "status": "Preliminary - adjuster review required",
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
        {"claim_id": claim_id}, {"_id": 1}
    ).sort("created_at", DESCENDING).limit(100))
    for row in rows:
        row["assessment_id"] = str(row.pop("_id"))
    return {"items": rows, "count": len(rows)}
