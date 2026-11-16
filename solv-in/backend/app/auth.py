from datetime import datetime, timedelta, timezone
import re
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pymongo.errors import PyMongoError
from .config import JWT_SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_MINUTES
from .database import get_database, database_is_available

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)

ROLES = (
    "Super Administrator",
    "Insurance Company Administrator",
    "Claims Manager",
    "Property Adjuster",
    "Casualty Adjuster",
    "Surveyor",
    "Actuarial Analyst",
    "Approver",
    "Auditor",
    "Customer",
)

def normalize_email(email: str) -> str:
    return email.strip().lower()

def hash_password(password: str) -> str:
    if len(password) < 12 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters and include letters and numbers.")
    return password_hash.hash(password)

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return password_hash.verify(password, stored_hash)
    except Exception:
        return False

def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["email"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not database_is_available():
        raise unauthorized
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise unauthorized
        user = get_database()["users"].find_one({"email": email, "is_active": True}, {"_id": 0, "password_hash": 0})
        if not user:
            raise unauthorized
        return user
    except (jwt.PyJWTError, PyMongoError, ValueError):
        raise unauthorized

def require_roles(*allowed_roles: str):
    def dependency(user: dict = Depends(get_current_user)):
        if user.get("role") not in allowed_roles:
            raise HTTPException(status_code=403, detail="Your account does not have permission for this action.")
        return user
    return dependency

def ensure_user_indexes():
    if database_is_available():
        get_database()["users"].create_index("email", unique=True)
