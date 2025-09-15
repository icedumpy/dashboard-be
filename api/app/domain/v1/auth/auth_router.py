# app/domain/v1/auth/router.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from jose import JWTError
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


from app.core.db.session import get_db
from app.core.security.auth import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.db.repo.models import (
    User,
    ProductionLine,
)
from app.core.security.auth import get_current_user
from app.core.db.repo.user.user_schema import LoginIn, TokenPair, RefreshIn, UserOut

TZ = ZoneInfo("Asia/Bangkok")

def current_shift_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = (now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)) if now else datetime.now(TZ)
    today = now.date()

    day_start = datetime.combine(today, time(8, 0), TZ)
    day_end   = datetime.combine(today, time(20, 0), TZ)

    if day_start <= now < day_end:
        return day_start, day_end

    # - if now >= 20:00 → [20:00 today, 08:00 tomorrow)
    # - if now  < 08:00 → [20:00 yesterday, 08:00 today)
    if now >= day_end:
        return day_end, day_start + timedelta(days=1)
    else:
        return day_end - timedelta(days=1), day_start




router = APIRouter()

@router.post("/login", response_model=TokenPair)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(User).where(User.username == payload.username))
    user = q.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials",
        )

    if not verify_password(payload.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User disabled",
        )

    subject = user.username or str(user.id)
    access = create_access_token(sub=subject)
    refresh = create_refresh_token(sub=subject)
    return TokenPair(access_token=access, refresh_token=refresh)

@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        sub = data.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

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

@router.get("/me", response_model=UserOut)
async def me(
    current: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(User, ProductionLine)
        .outerjoin(ProductionLine, User.line_id == ProductionLine.id)
        .where(User.id == current.id)
        .limit(1)
    )

    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user, line = row
    shift_start, shift_end = current_shift_window()

    payload = {
        "id": user.id,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "username": user.username,
        "line": None if line is None else {
            "id": line.id,
            "code": getattr(line, "code", None),
            "name": getattr(line, "name", None),
        },
        "shift": {
            "start_time": shift_start.time(), 
            "end_time":   shift_end.time(),
        },
    }

    return UserOut(**payload) 

