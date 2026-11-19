
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

