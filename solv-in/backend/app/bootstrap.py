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
    if users.find_one({"email": email}):
        return
    users.insert_one({
        "full_name": INITIAL_ADMIN_NAME,
        "email": email,
        "password_hash": hash_password(INITIAL_ADMIN_PASSWORD),
        "role": "Super Administrator",
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
