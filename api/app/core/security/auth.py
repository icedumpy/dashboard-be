# app/core/security/auth.py
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.config import settings
from app.core.db.session import get_db
from app.core.db.repo.models import User
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- Password helpers ----------
def hash_password(raw: str) -> str:
    return pwd_ctx.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    return pwd_ctx.verify(raw, hashed)


# ---------- JWT helpers ----------
def _exp(minutes: int = 15) -> int:
    return int((datetime.now(tz=timezone.utc) + timedelta(minutes=minutes)).timestamp())

def _exp_days(days: int) -> int:
    return int((datetime.now(tz=timezone.utc) + timedelta(days=days)).timestamp())

def create_access_token(sub: str) -> str:
    payload = {
        "sub": sub,
        "type": "access",
        "exp": _exp(settings.ACCESS_TOKEN_MIN),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)

def create_refresh_token(sub: str) -> str:
    payload = {
        "sub": sub,
        "type": "refresh",
        "exp": _exp_days(settings.REFRESH_TOKEN_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)

def decode_token(token: str) -> Dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])


# ---------- Header extraction (fallback when middleware not used) ----------
def _get_bearer_from_header(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None


# ---------- Current user dependency ----------
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Preferred flow:
      - jwt_middleware already validated the token and put payload in request.state.user
    Fallback:
      - If middleware wasn't applied (e.g. tests), read Authorization header and validate here.
    """
    payload = getattr(request.state, "user", None)

    if payload is None:
        # Fallback path: validate from header
        token = _get_bearer_from_header(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
            )
        try:
            payload = decode_token(token)
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    username: Optional[str] = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Load user (no token_version checks anymore)
    res = await db.execute(select(User).where(User.username == username))
    user = res.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User disabled or not found",
        )

    return user
