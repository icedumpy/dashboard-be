
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt
from app.core.config.config import settings
from app.core.db.repo.user.user_entity import User
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError
from app.core.db.session import get_db

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(raw: str) -> str:
    return pwd_ctx.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    return pwd_ctx.verify(raw, hashed)

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

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Verify access token
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        username: str = payload.get("sub")
        tv: int = payload.get("tv", -1)
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Load & check token_version
    res = await db.execute(select(User).where(User.username == username))
    user = res.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User disabled or not found")
    if user.token_version != tv:
        raise HTTPException(status_code=401, detail="Token revoked")
    return user
