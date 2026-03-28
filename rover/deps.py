"""FastAPI dependency injection — auth, database, and service instances."""

import os
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from rover.db import Database
from rover.logger import get_logger
from rover.token_store import GmailTokenStore

logger = get_logger("deps")

security = HTTPBearer()


@lru_cache
def get_db() -> Database:
    return Database()


@lru_cache
def get_token_store() -> GmailTokenStore:
    return GmailTokenStore(get_db())


def get_supabase_jwt_secret() -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise ValueError("SUPABASE_JWT_SECRET must be set")
    return secret


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Verify Supabase JWT and return the user record.

    Extracts the supabase auth user ID from the JWT `sub` claim,
    then looks up (or creates) the corresponding user in our database.
    """
    token = credentials.credentials

    try:
        secret = get_supabase_jwt_secret()
    except ValueError:
        raise HTTPException(status_code=500, detail="Server auth not configured")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as e:
        logger.debug("JWT verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    supabase_auth_id = payload.get("sub")
    if not supabase_auth_id:
        raise HTTPException(status_code=401, detail="Token missing sub claim")

    email = payload.get("email", "")

    db = get_db()
    user = db.get_user_by_auth_id(supabase_auth_id)

    if not user:
        # First API call for this user — create their record
        user = db.create_user(
            email=email,
            supabase_auth_id=supabase_auth_id,
            name=payload.get("user_metadata", {}).get("full_name"),
        )
        logger.info("Created new user %s (%s)", user["id"], email)

    return user


async def get_user_id(user: dict = Depends(get_current_user)) -> str:
    """Convenience dependency that returns just the user_id string."""
    return str(user["id"])
