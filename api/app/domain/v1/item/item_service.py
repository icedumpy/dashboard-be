from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Sequence, Union
from datetime import datetime
from sqlalchemy import or_
from sqlalchemy.sql.elements import BinaryExpression

from app.core.db.repo.models import Item, ItemStatus
from app.core.db.repo.models import EStation, EItemStatusCode
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
    return start_utc, end_utc

StationT = Union[str, EStation]
StatusListT = Optional[Sequence[Union[str, EItemStatusCode]]]

def build_item_filters(
    *,
    line_id: Optional[int] = None,
    station: StationT | None = None,
    product_code: Optional[str] = None,
    number: Optional[str] = None,
    job_order_number: Optional[str] = None,
    roll_width_min: Optional[float] = None,
    roll_width_max: Optional[float] = None,
    status: StatusListT = None,
    detected_from: Optional[datetime] = None,
    detected_to: Optional[datetime] = None,
) -> list[BinaryExpression]:
    """Return SQLAlchemy WHERE clauses matching list_items semantics."""
    clauses: list[BinaryExpression] = [Item.deleted_at.is_(None)]

    if line_id is not None:
        clauses.append(Item.line_id == line_id)

    if station is not None:
        st = station.value if hasattr(station, "value") else station
        clauses.append(Item.station == st)

    if product_code:
        clauses.append(Item.product_code.ilike(f"%{product_code}%"))

    if number:
        like = f"%{number}%"
        clauses.append(or_(Item.roll_number.ilike(like), Item.bundle_number.ilike(like)))

    if job_order_number:
        clauses.append(Item.job_order_number.ilike(f"%{job_order_number}%"))

    if roll_width_min is not None:
        clauses.append(Item.roll_width >= roll_width_min)
    if roll_width_max is not None:
        clauses.append(Item.roll_width <= roll_width_max)

    if status:
        vals = [(s.value if hasattr(s, "value") else s) for s in status]
        clauses.append(ItemStatus.code.in_(vals))

    if detected_from:
        clauses.append(Item.detected_at >= detected_from)
    if detected_to:
        clauses.append(Item.detected_at <= detected_to)

    return clauses


async def summarize_station(
    db: AsyncSession,
    *,
    line_id: Optional[int] = None,
    station: Optional[EStation | str] = None,
    product_code: Optional[str] = None,
    number: Optional[str] = None,
    job_order_number: Optional[str] = None,
    roll_width_min: Optional[float] = None,
    roll_width_max: Optional[float] = None,
    status: Optional[Sequence[EItemStatusCode | str]] = None,
    detected_from: Optional[datetime] = None,
    detected_to: Optional[datetime] = None,
) -> dict:
    pending_exists = (
        select(Review.id)
        .where(Review.item_id == Item.id, Review.state == "PENDING")
        .exists()
    )
    

    where_clauses = build_item_filters(
        line_id=line_id,
        station=station,
        product_code=product_code,
        number=number,
        job_order_number=job_order_number,
        roll_width_min=roll_width_min,
        roll_width_max=roll_width_max,
        status=status,
        detected_from=detected_from,
        detected_to=detected_to,
    )

    q = (
        select(
            func.count().label("total"),
            func.sum(case((ItemStatus.code.in_(("DEFECT", "REJECTED")), 1), else_=0)).label("defects"),
            func.sum(case((ItemStatus.code == "SCRAP", 1), else_=0)).label("scrap"),
            func.sum(case((and_(ItemStatus.code.in_(("DEFECT", "REJECTED")), pending_exists), 1), else_=0)).label("pending_defect"),
            func.sum(case((and_(ItemStatus.code == "RECHECK", pending_exists), 1), else_=0)).label("pending_scrap"),
        )
        .select_from(Item)
        .join(ItemStatus, ItemStatus.id == Item.item_status_id)
        .where(*where_clauses)
    )

    row = (await db.execute(q)).first() or (0, 0, 0, 0, 0)
    total, defects, scrap, pending_defect, pending_scrap = row
    return {
        "total": total or 0,
        "defects": defects or 0,
        "scrap": scrap or 0,
        "pending_defect": pending_defect or 0,
        "pending_scrap": pending_scrap or 0,
    }