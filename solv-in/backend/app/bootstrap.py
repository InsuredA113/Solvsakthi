from datetime import datetime, timezone
from .auth import hash_password, ensure_user_indexes
from .config import INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_PASSWORD, INITIAL_ADMIN_NAME
from .database import database_is_available, get_database

def seed_initial_admin():
    if not database_is_available():
        return
    ensure_user_indexes()
    if not INITIAL_ADMIN_EMAIL or not INITIAL_ADMIN_PASSWORD:
        return
    users = get_database()["users"]
    email = INITIAL_ADMIN_EMAIL.strip().lower()
    existing = users.find_one({"email": email})
    if existing:
        # The configured bootstrap account is the only account promoted here.
        users.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "role": "Super Administrator",
                "roles": ["Super Administrator"],
                "is_global_admin": True,
                "organisation_id": None,
            }}
        )
        return
    users.insert_one({
        "full_name": INITIAL_ADMIN_NAME,
        "email": email,
        "password_hash": hash_password(INITIAL_ADMIN_PASSWORD),
        "role": "Super Administrator",
        "roles": ["Super Administrator"],
        "is_global_admin": True,
        "organisation_id": None,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })



def migrate_initial_organisation_membership(organisation_id: str):
    """One-time bootstrap migration for pre-existing users in the initial workspace."""
    if not database_is_available():
        return
    db = get_database()
    migrations = db["system_migrations"]
    migration_key = "initial-organisation-membership-v1"
    if migrations.find_one({"_id": migration_key}):
        return

    users = db["users"]
    # Legacy Super Administrator accounts were global before the explicit marker existed.
    users.update_many(
        {"$or": [
            {"role": "Super Administrator"},
            {"roles": "Super Administrator"},
        ]},
        {"$set": {"is_global_admin": True, "organisation_id": None}},
    )
    # All other pre-existing accounts are members of the first workspace.
    users.update_many(
        {
            "is_global_admin": {"$ne": True},
            "$or": [
                {"organisation_id": {"$exists": False}},
                {"organisation_id": None},
            ],
        },
        {"$set": {"organisation_id": organisation_id}},
    )
    migrations.insert_one({
        "_id": migration_key,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "organisation_id": organisation_id,
    })
