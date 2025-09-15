
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Sequence, Union, List, Dict, Any, Set, Iterable
from datetime import datetime
from sqlalchemy import or_, update, text, delete, insert
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.orm import selectinload
from pathlib import PurePosixPath
from typing import Optional, List, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy import select, update, delete, insert, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.utils.helper.helper import current_shift_window
from app.core.db.repo.models import EStation, EItemStatusCode, DefectType
from app.core.db.repo.models import Item, ItemStatus, Review, ItemDefect, ItemEvent

router = APIRouter()

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

async def get_status_id(db: AsyncSession, code: str) -> int:
    q = select(ItemStatus.id).where(ItemStatus.code == code)
    r = await db.execute(q)
    row = r.first()
    if not row:
        raise ValueError(f"Unknown status code: {code}")
    return row[0]

STATUS_MAP = {
    "DEFECT": "DEFECT",
    "SCRAP": "SCRAP",
    "NORMAL": "NORMAL",  
}




async def get_missing_defect_type_ids(db: AsyncSession, ids: Iterable[int]) -> List[int]:
    uniq_ids: Set[int] = {int(x) for x in ids}
    if not uniq_ids:
        return []

    q = select(DefectType.id).where(DefectType.id.in_(uniq_ids))
    res = await db.execute(q)
    found = {row[0] for row in res.fetchall()}
    missing = sorted(uniq_ids - found)
    return missing

def norm(rel: Optional[str]) -> Optional[str]:
    if not rel: return None
    p = PurePosixPath(rel).as_posix().lstrip("/")
    if ".." in p:
        raise HTTPException(status_code=400, detail="Invalid image path")
    return p

async def operator_change_status_no_audit(
    db: AsyncSession,
    *,
    item_id: int,
    new_status_business: str,
    actor_user_id: int,
    defect_type_ids: Optional[List[int]],
    meta: Optional[Dict[str, Any]] = None,
    guard_line_ids: Optional[List[int]] = None,
    replace_defects_when_setting_defect: bool = False, 
) -> dict:
    try:
        q = (
            select(Item)
            .options(selectinload(Item.status)) 
            .where(Item.id == item_id)
            .with_for_update()
        )
        res = await db.execute(q)
        item: Optional[Item] = res.scalar_one_or_none()
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

        if guard_line_ids is not None and item.line_id not in guard_line_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Operator cannot change status for this line")

        try:
            target_code = STATUS_MAP[new_status_business]
        except KeyError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported status: {new_status_business}")

        target_status_id = await get_status_id(db, target_code)

        current_status = item.status
        current_code = getattr(current_status, "code", None) if current_status else None
        is_normal_like = current_code in ("NORMAL", "QC_PASSED") 
        going_to_defect = (target_code == "DEFECT")
        from_status_id = item.item_status_id
        
        if target_code == current_code:
            raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Item is already set to {target_code}"
                )
        
        

        if is_normal_like and going_to_defect:
            if not defect_type_ids or len(defect_type_ids) == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "defect_type_ids is required when changing NORMAL -> DEFECT"
                )
            missing = await get_missing_defect_type_ids(db, defect_type_ids)
            if missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Invalid defect_type_ids (not found): {missing}"
                )

        await db.execute(
            update(Item)
            .where(Item.id == item_id)
            .values(item_status_id=target_status_id, updated_at=text("now()"))
        )

        if going_to_defect and defect_type_ids is not None:
            uniq_ids = list({int(x) for x in defect_type_ids})

            if replace_defects_when_setting_defect:
                await db.execute(delete(ItemDefect).where(ItemDefect.item_id == item_id))

            if uniq_ids:
                rows = [{"item_id": item_id, "defect_type_id": dtid, "meta": meta or {}} for dtid in uniq_ids]
                await db.execute(insert(ItemDefect).values(rows))

        ev = ItemEvent(
            item_id=item_id,
            actor_id=actor_user_id,
            event_type="CHANGE_STATUS",
            from_status_id=from_status_id,
            to_status_id=target_status_id,
        )
        db.add(ev)
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "item_id": item_id,
        "new_status_code": target_code,
        "defect_type_ids_applied": defect_type_ids if going_to_defect and defect_type_ids else [],
    }

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
    
    
    if detected_from is None and detected_to is None:
        shift_start, shift_end = current_shift_window()
        where_clauses.append(Item.created_at >= shift_start)
        where_clauses.append(Item.created_at <= shift_end)

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