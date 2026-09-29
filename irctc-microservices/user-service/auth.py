import datetime
import os

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


# --------------------------------------------------
# JWT Configuration
# --------------------------------------------------
#
# JWT_SECRET_KEY has an insecure default so the service still boots without
# any extra setup in local/dev use, exactly like the DATABASE_URL defaults
# elsewhere in this codebase - it must be overridden via the environment
# anywhere tokens need to be trusted beyond this one process.

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-insecure-secret-change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_MINUTES = int(os.getenv("JWT_EXPIRY_MINUTES", "60"))


# --------------------------------------------------
# Password Hashing
# --------------------------------------------------

def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        # Not a valid bcrypt hash (e.g. a legacy/placeholder value from a
        # user created before authentication existed) - never a match.
        return False


# --------------------------------------------------
# JWT Creation & Validation
# --------------------------------------------------

def create_access_token(user_id: int, email: str) -> str:
    now = datetime.datetime.utcnow()

    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=JWT_EXPIRY_MINUTES),
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")

    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


# --------------------------------------------------
# FastAPI Dependency: Current User
# --------------------------------------------------
#
# HTTPBearer(auto_error=True) rejects a request with no Authorization
# header at all (403) before this function is even called; a header that
# is present but invalid/expired is rejected inside decode_access_token
# (401). Either way, an unauthenticated request never reaches a protected
# route.

bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> int:
    payload = decode_access_token(credentials.credentials)

    try:
        return int(payload["sub"])

    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid authentication token")
