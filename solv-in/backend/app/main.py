from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError
from .config import CORS_ORIGINS
from .database import get_database, database_is_available
from .models import ClaimCreate, ClaimStatusUpdate

app = FastAPI(
    title="Solv-In API",
    description="Development API for the Solv-In Property & Casualty claims platform.",
    version="0.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "application": "Solv-In",
        "database": "connected" if database_is_available() else "disconnected"
    }

@app.get("/api/dashboard/summary")
def dashboard_summary():
    if not database_is_available():
        return {
            "data_mode": "demo",
            "message": "Fictional sample figures for UI development only.",
            "total_claims": 1248,
            "new_claims": 342,
            "under_investigation": 278,
            "pending_approval": 167,
            "settled_claims": 260
        }
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
    limit: int = Query(default=100, ge=1, le=250)
):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable. Start MongoDB and try again.")
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
def create_claim(payload: ClaimCreate):
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
        record.update({
            "claim_id": claim_id,
            "status": "FNOL Received",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        })
        db["claims"].insert_one(record)
        return record
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to save claim to MongoDB.") from exc

@app.patch("/api/claims/{claim_id}/status")
def update_claim_status(claim_id: str, payload: ClaimStatusUpdate):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    try:
        updated = get_database()["claims"].find_one_and_update(
            {"claim_id": claim_id},
            {"$set": {"status": payload.status, "updated_at": datetime.now(timezone.utc).isoformat()}},
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
