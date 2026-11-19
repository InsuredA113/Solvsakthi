
from datetime import datetime, timezone
from uuid import uuid4

from pymongo.errors import PyMongoError


DEFAULT_ORGANISATION_KEY = "solv-in-default"
DEFAULT_ORGANISATION_NAME = "Solv-In Default Organisation"


def ensure_default_organisation(db):
    """
    Create the initial Solv-In organisation if it does not exist.
    Safe to call every time the backend starts.
    """

    organisations = db["organisations"]

    # Prevent duplicate organisations with the same stable key.
    organisations.create_index("key", unique=True)

    now = datetime.now(timezone.utc).isoformat()

    organisation_data = {
        "organisation_id": str(uuid4()),
        "key": DEFAULT_ORGANISATION_KEY,
        "name": DEFAULT_ORGANISATION_NAME,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": "system",
        "updated_by": "system",
    }

    try:
        organisations.update_one(
            {"key": DEFAULT_ORGANISATION_KEY},
            {"$setOnInsert": organisation_data},
            upsert=True,
        )

        return organisations.find_one(
            {"key": DEFAULT_ORGANISATION_KEY},
            {"_id": 0},
        )

    except PyMongoError:
        raise




def organisation_filter(user: dict) -> dict:
    """Return an explicit tenant filter; only marked global admins bypass it."""
    from fastapi import HTTPException

    if user.get("is_global_admin") is True:
        return {}
    organisation_id = user.get("organisation_id")
    if not organisation_id:
        raise HTTPException(status_code=403, detail="Your account is not assigned to an organisation.")
    return {"organisation_id": organisation_id}


def scoped_claim_filter(claim_id: str, user: dict) -> dict:
    """Build a claim lookup that is tenant-scoped for ordinary users."""
    return {"$and": [{"claim_id": claim_id}, organisation_filter(user)]}


def get_claim_for_user(db, claim_id: str, user: dict, projection=None):
    """Fetch a claim only when it belongs to the authenticated user's organisation."""
    return db["claims"].find_one(scoped_claim_filter(claim_id, user), projection)



def resolve_write_organisation_id(db, user: dict) -> str:
    """Resolve the organisation for newly created tenant-owned records."""
    from fastapi import HTTPException

    if user.get("is_global_admin") is True:
        organisation = db["organisations"].find_one(
            {"key": DEFAULT_ORGANISATION_KEY, "status": "active"},
            {"_id": 0, "organisation_id": 1}
        )
        organisation_id = organisation.get("organisation_id") if organisation else None
    else:
        organisation_id = user.get("organisation_id")
    if not organisation_id:
        raise HTTPException(status_code=403, detail="An active organisation is required for this operation.")
    return organisation_id
