from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError, PyMongoError

from .auth import get_current_user
from .database import database_is_available, get_database
from .tenant_models import TenantCreate, TenantStatusUpdate

router = APIRouter(prefix="/api/tenants", tags=["Tenants"])

def require_super_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "Super Administrator":
        raise HTTPException(status_code=403, detail="Only a Super Administrator can manage insurer tenants.")
    return user

def ensure_tenant_indexes() -> None:
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    tenants = get_database()["tenants"]
    tenants.create_index("tenant_id", unique=True)
    tenants.create_index("tenant_slug", unique=True)

def _tenant_response(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "_id"}

@router.get("")
def list_tenants(_: dict = Depends(require_super_admin)):
    ensure_tenant_indexes()
    try:
        rows = list(get_database()["tenants"].find({}, {"_id": 0}).sort("created_at", -1))
        return {"items": rows, "count": len(rows)}
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to retrieve insurer tenants.") from exc

@router.post("", status_code=201)
def create_tenant(payload: TenantCreate, actor: dict = Depends(require_super_admin)):
    ensure_tenant_indexes()
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "tenant_id": f"ten_{uuid4().hex}",
        "company_name": payload.company_name,
        "tenant_slug": payload.tenant_slug.lower(),
        "contact_email": str(payload.contact_email).lower(),
        "status": "Active",
        "created_at": now,
        "updated_at": now,
        "created_by": actor["email"],
    }
    try:
        get_database()["tenants"].insert_one(record)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="An insurer tenant with this slug or identifier already exists.") from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="Unable to create insurer tenant.") from exc
    return _tenant_response(record)

@router.get("/{tenant_id}")
def get_tenant(tenant_id: str, _: dict = Depends(require_super_admin)):
    ensure_tenant_indexes()
    row = get_database()["tenants"].find_one({"tenant_id": tenant_id}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail="Insurer tenant not found.")
    return row

@router.patch("/{tenant_id}/status")
def update_tenant_status(tenant_id: str, payload: TenantStatusUpdate, actor: dict = Depends(require_super_admin)):
    ensure_tenant_indexes()
    now = datetime.now(timezone.utc).isoformat()
    result = get_database()["tenants"].find_one_and_update(
        {"tenant_id": tenant_id},
        {"$set": {"status": payload.status, "updated_at": now, "updated_by": actor["email"]}},
        return_document=True,
        projection={"_id": 0},
    )
    if not result:
        raise HTTPException(status_code=404, detail="Insurer tenant not found.")
    return result
