from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.repo.models import Item, ItemStatus, Review, Shift, User

router = APIRouter()


TZ = ZoneInfo("Asia/Bangkok")
async def resolve_shift_window(db: AsyncSession, user: User):
    """
    Returns (start_utc, end_utc, start_local, end_local)
    Uses user's shift if available: supports start/end hour or time fields.
    Falls back to the full local day.
    """
    now_local = datetime.now(TZ)
    # default: whole local day
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)

    if getattr(user, "shift_id", None) and Shift is not None:
        sh = await db.get(Shift, user.shift_id)
        if sh:
            # prefer integer hours if present, else time fields
            s_hour = getattr(sh, "start_hour", None)
            e_hour = getattr(sh, "end_hour", None)
            s_time = getattr(sh, "start_time", None)
            e_time = getattr(sh, "end_time", None)
            if s_hour is not None and e_hour is not None:
                start_local = now_local.replace(hour=int(s_hour), minute=0, second=0, microsecond=0)
                end_local = now_local.replace(hour=int(e_hour), minute=0, second=0, microsecond=0)
            elif s_time is not None and e_time is not None:
                start_local = now_local.replace(hour=s_time.hour, minute=s_time.minute, second=0, microsecond=0)
                end_local = now_local.replace(hour=e_time.hour, minute=e_time.minute, second=0, microsecond=0)
            # handle cross-midnight
            if end_local <= start_local:
                end_local = end_local + timedelta(days=1)

    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc = end_local.astimezone(ZoneInfo("UTC"))
    return start_utc, end_utc, start_local, end_local

async def summarize_station(db: AsyncSession, line_id: int, station: str, start_utc: datetime, end_utc: datetime):
    """
    Compute counts for one station within the time window on the given line.
    scrap = SCRAP + RECHECK
    pending_* = items in that status with an existing pending review (any type)
    """
    pending_exists = (
        select(Review.id)
        .where(Review.item_id == Item.id, Review.state == "PENDING")
        .exists()
    )

    q = (
        select(
            func.count().label("total"),
            func.sum(case((ItemStatus.code == "DEFECT", 1), else_=0)).label("defects"),
            func.sum(case((ItemStatus.code.in_(("SCRAP", "RECHECK")), 1), else_=0)).label("scrap"),
            func.sum(
                case((and_(ItemStatus.code == "DEFECT", pending_exists), 1), else_=0)
            ).label("pending_defect"),
            func.sum(
                case((and_(ItemStatus.code.in_(("SCRAP", "RECHECK")), pending_exists), 1), else_=0)
            ).label("pending_scrap"),
        )
        .select_from(Item)
        .join(ItemStatus, ItemStatus.id == Item.item_status_id)
        .where(
            Item.deleted_at.is_(None),
            Item.line_id == line_id if line_id != None else True,
            Item.station == station, 
            # Item.detected_at >= start_utc,
            # Item.detected_at < end_utc,
        )
    )

    row = (await db.execute(q)).first()
    if not row:
        return {"total": 0, "defects": 0, "scrap": 0, "pending_defect": 0, "pending_scrap": 0}

    total, defects, scrap, pending_defect, pending_scrap = row
    return {
        "total": total or 0,
        "defects": defects or 0,
        "scrap": scrap or 0,
        "pending_defect": pending_defect or 0,
        "pending_scrap": pending_scrap or 0,
    }