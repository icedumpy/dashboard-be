# app/domain/v1/auth/router.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from jose import JWTError

from app.core.db.session import get_db
from app.core.db.repo.user.user_entity import User
from app.core.security.auth import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.security.auth import get_current_user
from app.core.db.repo.user.user_schema import LoginIn, TokenPair, RefreshIn, UserOut

router = APIRouter(tags=["auth"])

@router.post("/login", response_model=TokenPair)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    # 1) Load user
    q = await db.execute(select(User).where(User.username == payload.username))
    user = q.scalar_one_or_none()

    # 2) Fail early if not found
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials",
        )

    # 3) Check password
    if not verify_password(payload.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials",
        )

    # 4) Check active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User disabled",
        )

    # 5) Issue tokens
    subject = user.username or str(user.id)
    access = create_access_token(sub=subject)
    refresh = create_refresh_token(sub=subject)
    return TokenPair(access_token=access, refresh_token=refresh)
@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    # Verify refresh token
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        sub = data.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Load user (by username first; fallback to id if sub is numeric)
    stmt = select(User).where(User.username == sub)
    if sub.isdigit():
        stmt = select(User).where(or_(User.username == sub, User.id == int(sub)))

    q = await db.execute(stmt)
    user = q.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")

    subject = user.username or str(user.id)
    access = create_access_token(sub=subject)
    refresh = create_refresh_token(sub=subject)
    return TokenPair(access_token=access, refresh_token=refresh)

@router.post("/logout")
async def logout(_: User = Depends(get_current_user)):
    # Pure JWT has no server-side revocation. Client should discard tokens.
    # If you use cookies, clear them here instead.
    return {"ok": True}

@router.get("/me", response_model=UserOut)
async def me(current: User = Depends(get_current_user)):
    return current
