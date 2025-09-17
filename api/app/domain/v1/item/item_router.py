# app/domain/v1/items_router.py
from fastapi import APIRouter, Query, Depends, HTTPException, Request, status
from typing import Optional, Annotated, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_, literal, text, update, insert
from sqlalchemy.orm import aliased

from datetime import datetime, timedelta
from app.core.config.config import settings
from io import StringIO

from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import (
    Item, ItemStatus, ProductionLine, ItemDefect, DefectType,
    Review, ItemImage, ItemEvent,
    StatusChangeRequest,
    EStation,EItemStatusCode,User
)

from app.domain.v1.item.item_schema import FixRequestBody, UpdateItemStatusBody, ItemReportRequest, ItemEventOut, ActorOut
from app.domain.v1.item.item_service import summarize_station, status_label, norm
from app.utils.helper.helper import (
    require_role,
    require_same_line,
    precondition_if_unmodified_since,
    TZ
)
from fastapi.responses import StreamingResponse
import csv
import asyncio
import logging

router = APIRouter()

log = logging.getLogger(__name__)

# ---------- GET /items ----------
@router.get("", summary="List items")
async def list_items(
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),

    station: Annotated[Optional[EStation], Query(description="filter by station")] = None,
    line_id: Optional[int] = Query(None, description="e.g. 1 = Line 3, 2 = Line 4"),
    product_code: Optional[str] = Query(None, description="contains match"),
    number: Optional[str] = Query(None, description="roll_number or bundle_number (contains)"),
    job_order_number: Optional[str] = Query(None, description="contains match"),
    roll_width_min: Optional[float] = Query(None, ge=0),
    roll_width_max: Optional[float] = Query(None, ge=0),
    roll_id: Optional[str] = Query(None, description="filter by roll_id"),

    status: Annotated[list[EItemStatusCode] | None, Query(description="repeatable status codes")] = None,

    detected_from: Optional[datetime] = Query(None, description="ISO8601"),
    detected_to: Optional[datetime] = Query(None, description="ISO8601"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size
    
    q = select(Item).where(Item.deleted_at.is_(None))

    q = q.join(ItemStatus, Item.item_status_id == ItemStatus.id)
    if line_id:
        q = q.join(ProductionLine, Item.line_id == ProductionLine.id)

    if station: q = q.where(Item.station == station)
    if line_id: q = q.where(ProductionLine.id == line_id)
    if product_code: q = q.where(Item.product_code.ilike(f"%{product_code}%"))
    if number:
        q = q.where(
            (Item.roll_number.ilike(f"%{number}%")) |
            (Item.bundle_number.ilike(f"%{number}%"))
        )
    if roll_id: q = q.where(Item.roll_id.ilike(f"%{roll_id}%"))
    if job_order_number: q = q.where(Item.job_order_number.ilike(f"%{job_order_number}%"))
    if roll_width_min is not None: q = q.where(Item.roll_width >= roll_width_min)
    if roll_width_max is not None: q = q.where(Item.roll_width <= roll_width_max)
    if status: 
        station_values = [s.value for s in (status or [])]
        q = q.where(ItemStatus.code.in_(station_values))

    if detected_from: q = q.where(text("qc.items.detected_at >= :df")).params(df=detected_from)
    if detected_to: q = q.where(text("qc.items.detected_at <= :dt")).params(dt=detected_to)
    
    if detected_from is None and detected_to is None:
        now = datetime.now(TZ)

        if user.role == "VIEWER":
            # subtract 365 days (approx 1 year)
            dt = now - timedelta(days=365)
            q = q.where(text("qc.items.detected_at >= :dt")).params(dt=dt)

        elif user.role == "OPERATOR":
            # subtract 30 days
            dt = now - timedelta(days=30)
            q = q.where(text("qc.items.detected_at >= :dt")).params(dt=dt)
    

    q = q.order_by(ItemStatus.display_order.asc(), Item.detected_at.desc(), Item.id.desc())

    total = (await db.execute(q.with_only_columns(text("count(*)")).order_by(None))).scalar()

    rows = (await db.execute(q.offset(offset).limit(page_size))).scalars().all()
    
    summary = await summarize_station(
        db,
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

    data = []
    for it in rows:
        imgs = (await db.execute(
            select(text("count(*)")).select_from(ItemImage).where(ItemImage.item_id == it.id)
        )).scalar()
        item_defects = await db.execute(
            select(DefectType.name_th).join(ItemDefect, DefectType.id == ItemDefect.defect_type_id).where(ItemDefect.item_id == it.id)
        )
        defs = item_defects.unique().scalars().all()
        
        st = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

        if it.station == EStation.BUNDLE:
            q = (
                select(Item)
                .where(
                    Item.station == EStation.ROLL,              
                    Item.roll_number == it.bundle_number,
                    Item.line_id == it.line_id,           
                    Item.deleted_at.is_(None),             
                )
                .order_by(Item.detected_at.desc(), Item.id.desc()) 
                .limit(1)
            )
            roll_item = (await db.execute(q)).scalars().first()
            it.product_code = roll_item.product_code if roll_item is not None else None
            it.job_order_number = roll_item.job_order_number if roll_item is not None else None
            it.roll_width = roll_item.roll_width if roll_item is not None else None

        is_pending_review = False

        if it.current_review_id != None:
            review_data = (await db.execute(select(Review).where(Review.id == it.current_review_id))).scalar()
            is_pending_review = review_data.state == "PENDING" if review_data is not None else False

        change_status_data = (await db.execute(select(StatusChangeRequest).where(and_(StatusChangeRequest.item_id == it.id, StatusChangeRequest.state == "PENDING")))).scalar()
        is_changing_status_pending = True if change_status_data is not None else False
        


        data.append({
            "id": it.id,
            "station": it.station,
            "line_id": it.line_id,
            "product_code": it.product_code,
            "roll_number": it.roll_number,
            "bundle_number": it.bundle_number,
            "job_order_number": it.job_order_number,
            "roll_width": float(it.roll_width) if it.roll_width is not None else None,
            "roll_id": it.roll_id,
            "detected_at": it.detected_at.isoformat(),
            "status_code": st,
            "scrap_requires_qc": it.scrap_requires_qc,
            "scrap_confirmed_by": it.scrap_confirmed_by,
            "scrap_confirmed_at": it.scrap_confirmed_at.isoformat() if it.scrap_confirmed_at else None,
            "current_review_id": it.current_review_id,
            "is_pending_review": is_pending_review,
            "is_changing_status_pending": is_changing_status_pending,
            "images": imgs,
            "defects": defs,
        })

    resp = {
        "data": data,
        "summary": summary,
        "pagination": {
            "page": page, 
            "page_size": page_size,
            "total": total, 
            "total_pages": (total + page_size - 1) // page_size
        }
    }
    return resp

@router.get("/{item_id}")
async def get_item_detail(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    it = (await db.get(Item, item_id))
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")

    st = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

    defs = (await db.execute(
        select(DefectType.code, ItemDefect.meta)
        .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
        .where(ItemDefect.item_id == it.id)
    )).all()

    imgs = (await db.execute(
        select(ItemImage.id, ItemImage.kind, ItemImage.path)
        .where(ItemImage.item_id == it.id)
        .order_by(ItemImage.uploaded_at.desc())
    )).all()
    grouped = {"DETECTED": [], "FIX": [], "OTHER": []}
    for iid, kind, path in imgs:
        grouped.setdefault(kind, []).append({"id": iid, "path": path})

    rws = (await db.execute(
        select(Review).where(Review.item_id == it.id).order_by(Review.submitted_at.desc())
    )).scalars().all()
    user_ids = {
        *[rv.submitted_by for rv in rws if rv.submitted_by is not None],
        *[rv.reviewed_by for rv in rws if rv.reviewed_by is not None],
    }

    user_map: dict[int, dict] = {}
    if user_ids:
        users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        user_map = {
            u.id: {
                "id": u.id,
                "username": getattr(u, "username", None),
                "display_name": (
                    getattr(u, "display_name", None)
                    or getattr(u, "name", None)
                    or getattr(u, "full_name", None)
                ),
                "role": getattr(u, "role", None),
            }
            for u in users
        }

    return {
        "data": {
            "id": it.id,
            "station": it.station,
            "line_id": it.line_id,
            "product_code": it.product_code,
            "roll_number": it.roll_number,
            "bundle_number": it.bundle_number,
            "job_order_number": it.job_order_number,
            "roll_width": float(it.roll_width) if it.roll_width is not None else None,
            "detected_at": it.detected_at.isoformat(),
            "status_code": st,
            "ai_note": it.ai_note,
            "scrap_requires_qc": it.scrap_requires_qc,
            "scrap_confirmed_by": it.scrap_confirmed_by,
            "scrap_confirmed_at": it.scrap_confirmed_at.isoformat() if it.scrap_confirmed_at else None,
            "current_review_id": it.current_review_id,
        },
        "defects": [{"defect_type_code": c, "meta": m} for c, m in defs],
        "images": grouped,
        "reviews": [
            {
                "id": rv.id, "review_type": rv.review_type, "state": rv.state,
                "submitted_by": rv.submitted_by, "submitted_at": rv.submitted_at.isoformat(),
                "submitted_by_user": user_map.get(rv.submitted_by),
                "reviewed_by": rv.reviewed_by, "reviewed_at": rv.reviewed_at.isoformat() if rv.reviewed_at else None,
                "reviewed_by_user": user_map.get(rv.reviewed_by),
                "submit_note": rv.submit_note, "review_note": rv.review_note, "reject_reason": rv.reject_reason
            } for rv in rws
        ]
    }

@router.get("/{item_id}/history", response_model=List[ItemEventOut])
async def get_item_history(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    FromS = aliased(ItemStatus)
    ToS = aliased(ItemStatus)

    q = (
        select(
            ItemEvent.id,
            ItemEvent.event_type,
            ItemEvent.actor_id,
            ItemEvent.details,
            ItemEvent.from_status_id,
            FromS.code.label("from_status_code"),
            ItemEvent.to_status_id,
            ToS.code.label("to_status_code"),
            ItemEvent.created_at,
            User.id.label("user_id"),
            User.username,
            User.display_name,
        )
        .outerjoin(FromS, FromS.id == ItemEvent.from_status_id)
        .outerjoin(ToS, ToS.id == ItemEvent.to_status_id)
        .outerjoin(User, User.id == ItemEvent.actor_id) 
        .where(ItemEvent.item_id == item_id)
        .order_by(ItemEvent.created_at.desc(), ItemEvent.id.desc())
    )

    rows = (await db.execute(q)).all()
    
    rows = (await db.execute(q)).all()

    data: list[ItemEventOut] = []
    for r in rows:
        defects: list[str] = []

        if r.from_status_code == "DEFECT" or r.to_status_code == "DEFECT":
            result = await db.execute(
                select(DefectType.name_th)
                .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
                .where(ItemDefect.item_id == item_id)
            )
            defects = result.unique().scalars().all()

        v = ItemEventOut(
            id=r.id,
            event_type=r.event_type,
            from_status_id=r.from_status_id,
            from_status_code=r.from_status_code,
            to_status_id=r.to_status_id,
            to_status_code=r.to_status_code,
            created_at=(
                r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at)
            ),
            defects=defects,
            actor=ActorOut(
                id=r.user_id,
                username=r.username,
                display_name=r.display_name,
            ),
        )
        data.append(v)

    return data

# @router.patch("/{item_id}/status")
# async def change_item_status(
#     item_id: int,
#     body: UpdateItemStatusBody,
#     db: AsyncSession = Depends(get_db),
#     user = Depends(get_current_user),
# ):
#     require_role(user, ["OPERATOR", "INSPECTOR"])

#     allowed_line_ids = getattr(user, "line_ids", None)

#     result = await operator_change_status(
#         db,
#         item_id=item_id,
#         new_status_business=body.status,
#         actor_user_id=user.id,
#         actor_role=user.role,
#         defect_type_ids=body.defect_type_ids,
#         meta=body.meta,
#         guard_line_ids=allowed_line_ids,
#     )
#     return result

@router.post("/{item_id}/fix-request")
async def submit_fix_request(
    request: Request,
    item_id: int,
    body: FixRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["OPERATOR"])
    it = await db.get(Item, item_id)
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")
    require_same_line(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    is_pening_review = False

    if it.current_review_id != None:
        review_data = (await db.execute(select(Review).where(Review.id == it.current_review_id))).scalar()
        is_pening_review = review_data.state == "PENDING"
    
    if (is_pening_review == True):
        raise HTTPException(status_code=400, detail="The fix request has been submitted")

    st_code = (
        await db.execute(
            select(ItemStatus.code).where(ItemStatus.id == it.item_status_id)
        )
    ).scalar()
    if st_code not in ("DEFECT", "RECHECK", "REJECTED"):
        raise HTTPException(status_code=400, detail="Fix request allowed only for DEFECT or RECHECK")

    image_ids = list(getattr(body, "image_ids", []) or [])
    if not image_ids:
        raise HTTPException(status_code=400, detail="Provide at least 1 image_id")

    try:
        image_ids = list({int(i) for i in image_ids})
    except Exception:
        raise HTTPException(status_code=400, detail="image_ids must be integers")

    rows = await db.execute(
        select(ItemImage.id, ItemImage.review_id, ItemImage.item_id)
        .where(ItemImage.id.in_(image_ids))
    )
    rows = rows.all()

    found_ids = {r.id for r in rows}
    missing = [i for i in image_ids if i not in found_ids]
    if missing:
        raise HTTPException(status_code=400, detail={"message": "Some image_ids do not exist", "missing": missing})

    already_linked = [r.id for r in rows if r.review_id is not None]
    deleted = [r.id for r in rows if getattr(r, "deleted_at", None)]
    wrong_item = [r.id for r in rows if (getattr(r, "item_id", None) not in (None, item_id))]

    if already_linked or deleted or wrong_item:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid images for fix request",
                "already_linked": already_linked,
                "deleted": deleted,
                "wrong_item": wrong_item,
            },
        )

    note = getattr(body, "note", None)

    rv = Review(
        item_id=it.id,
        review_type="DEFECT_FIX",
        state="PENDING",
        submitted_by=user.id,
        submit_note=note,
    )
    db.add(rv)
    await db.flush()

    upd = await db.execute(
        text(
            """
            UPDATE qc.item_images
               SET review_id = :rid, kind = 'FIX'
             WHERE id = ANY(:ids)
               AND review_id IS NULL
               AND (item_id IS NULL OR item_id = :item_id)
            """
        ),
        {"rid": rv.id, "ids": image_ids, "item_id": item_id},
    )

    if upd.rowcount != len(image_ids):
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Images changed concurrently; please retry"
        )

    it.current_review_id = rv.id

    db.add(
        ItemEvent(
            item_id=it.id,
            actor_id=user.id,
            event_type="FIX_REQUEST_SUBMITTED",
            to_status_id=it.item_status_id,
        )
    )

    await db.commit()
    return {"review_id": rv.id}

@router.post("/{item_id}/scrap")
async def mark_scrap(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])
    it = await db.get(Item, item_id)
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")
    require_same_line(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    st_code = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

    review_id = None
    if st_code == "SCRAP":
        it.scrap_confirmed_by = user.id
        it.scrap_confirmed_at = datetime.utcnow()
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="OPERATOR_CONFIRM_SCRAP", to_status_id=it.item_status_id))
    elif st_code == "RECHECK":
        # move to SCRAP and open a pending review for QC
        scrap_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "SCRAP"))).scalar_one()
        it.item_status_id = scrap_id
        it.scrap_requires_qc = True
        rv = Review(item_id=it.id, review_type="SCRAP_FROM_RECHECK", state="PENDING", submitted_by=user.id)
        db.add(rv)
        await db.flush()
        review_id = rv.id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="SCRAP_FROM_RECHECK", to_status_id=scrap_id))
    else:
        raise HTTPException(status_code=400, detail="Scrap allowed only when status is SCRAP or RECHECK")

    await db.commit()
    return {"ok": True, "review_id": review_id}

@router.get("/{item_id}/images")
async def list_item_images(
    item_id: int,
    kinds: Optional[str] = Query(None, description="CSV: DETECTED,FIX,OTHER"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    it = await db.get(Item, item_id)
    if not it or getattr(it, "deleted_at", None):
        raise HTTPException(status_code=404, detail="Item not found")

    q = select(ItemImage).where(ItemImage.item_id == item_id)
    if kinds:
        kind_list = [k.strip().upper() for k in kinds.split(",") if k.strip()]
        q = q.where(ItemImage.kind.in_(kind_list))
    rows = (await db.execute(q.order_by(ItemImage.uploaded_at.asc(), ItemImage.id.asc()))).scalars().all()

    data = []
    image_dir = settings.IMAGES_DIR
    for im in rows:
        path = norm(im.path)
        data.append({
            "id": im.id,
            "kind": im.kind,
            "created_at": im.uploaded_at,
            "meta": im.meta,
            "url": f"/{image_dir}/{path}" if path else None,
        })
    return {"data": data}

@router.post("/report", summary="Download CSV report")
async def get_csv_item_report(
    body: ItemReportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER"])

    line_code = await db.scalar(select(ProductionLine.code).where(ProductionLine.id == body.line_id))
    line_code = str(line_code or body.line_id)

    roll_match = aliased(Item)

    # build base columns first
    base_cols = [
        Item.id.label("item_id"),
        Item.station,
        Item.line_id,
        Item.product_code,
        Item.roll_number,
        Item.bundle_number,
        Item.job_order_number,
        Item.roll_width,
        Item.detected_at,
        Item.ai_note,
        ItemStatus.code.label("status_code"),
    ]

    # add r_* columns depending on station
    if body.station == EStation.BUNDLE:
        base_cols += [
            roll_match.product_code.label("r_product_code"),
            roll_match.job_order_number.label("r_job_order_number"),
            roll_match.roll_width.label("r_roll_width"),
        ]
    else:
        base_cols += [
            literal(None).label("r_product_code"),
            literal(None).label("r_job_order_number"),
            literal(None).label("r_roll_width"),
        ]

    base = select(*base_cols).join(ItemStatus, ItemStatus.id == Item.item_status_id).where(
        Item.line_id == body.line_id,
        Item.station == body.station.value,
        Item.deleted_at.is_(None),
    )

    if body.station == EStation.BUNDLE:
        base = base.outerjoin(
            roll_match,
            and_(
                roll_match.station == EStation.ROLL.value,
                roll_match.line_id == Item.line_id,
                roll_match.roll_number == Item.bundle_number,
                roll_match.deleted_at.is_(None),
            ),
        )

    # filters
    if body.product_code:
        like = f"%{body.product_code}%"
        if body.station == EStation.BUNDLE:
            base = base.where(or_(Item.product_code.ilike(like), roll_match.product_code.ilike(like)))
        else:
            base = base.where(Item.product_code.ilike(like))

    if body.number:
        like = f"%{body.number}%"
        base = base.where(or_(Item.roll_number.ilike(like), Item.bundle_number.ilike(like)))

    if body.job_order_number:
        like = f"%{body.job_order_number}%"
        if body.station == EStation.BUNDLE:
            base = base.where(or_(Item.job_order_number.ilike(like), roll_match.job_order_number.ilike(like)))
        else:
            base = base.where(Item.job_order_number.ilike(like))

    if body.roll_width_min is not None or body.roll_width_max is not None:
        width_expr = func.coalesce(Item.roll_width, roll_match.roll_width) if body.station == EStation.BUNDLE else Item.roll_width
        if body.roll_width_min is not None:
            base = base.where(width_expr >= body.roll_width_min)
        if body.roll_width_max is not None:
            base = base.where(width_expr <= body.roll_width_max)

    if body.status:
        base = base.where(ItemStatus.code.in_([s.value for s in body.status]))

    if body.detected_from:
        base = base.where(Item.detected_at >= body.detected_from)
    if body.detected_to:
        base = base.where(Item.detected_at <= body.detected_to)

    base_sq = base.subquery("base")

    # defects only for items in base
    defects_subq = (
        select(
            ItemDefect.item_id.label("item_id"),
            func.string_agg(func.distinct(DefectType.name_th), literal(", ")).label("defects_csv"),
        )
        .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
        .where(ItemDefect.item_id.in_(select(base_sq.c.item_id)))
        .group_by(ItemDefect.item_id)
        .subquery()
    )

    # final query: NO DISTINCT, order for streaming
    q = (
        select(
            base_sq.c.item_id,
            base_sq.c.station,
            base_sq.c.line_id,
            base_sq.c.product_code,
            base_sq.c.roll_number,
            base_sq.c.bundle_number,
            base_sq.c.job_order_number,
            base_sq.c.roll_width,
            base_sq.c.detected_at,
            base_sq.c.ai_note,
            base_sq.c.status_code,
            defects_subq.c.defects_csv,
            base_sq.c.r_product_code,
            base_sq.c.r_job_order_number,
            base_sq.c.r_roll_width,
        )
        .outerjoin(defects_subq, defects_subq.c.item_id == base_sq.c.item_id)
        .order_by(base_sq.c.detected_at.desc(), base_sq.c.item_id.desc())
    )

    # ---------- CSV ----------
    header = [
        "PRODUCT CODE",
        "ROLL NUMBER" if body.station == EStation.ROLL else "BUNDLE NUMBER",
        "JOB ORDER NUMBER",
        "ROLL WIDTH",
        "TIMESTAMP",
        "STATUS",
    ]

    def row_to_list(m) -> list:
        product_code_val = m.get("product_code")
        job_order_val = m.get("job_order_number")
        width_val = m.get("roll_width")
        if body.station == EStation.BUNDLE:
            product_code_val = product_code_val or m.get("r_product_code")
            job_order_val = job_order_val or m.get("r_job_order_number")
            width_val = width_val if width_val is not None else m.get("r_roll_width")

        num_val = m.get("roll_number") if body.station == EStation.ROLL else m.get("bundle_number")
        status_str = status_label(m.get("status_code"), m.get("defects_csv"))
        dt = m.get("detected_at")
        ts = dt.isoformat(timespec="seconds") if dt else ""
        width_out = "" if width_val is None else str(width_val)
        return [product_code_val or "", num_val or "", job_order_val or "", width_out, ts, status_str]

    async def acsv_iter():
        buf = StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        THRESHOLD = 256 * 1024  # ~256 KiB

        try:
            writer.writerow(header)
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

            result = await db.stream(q)
            try:
                async for row in result.mappings():
                    if await request.is_disconnected():
                        return
                    writer.writerow(row_to_list(row))
                    if buf.tell() >= THRESHOLD:
                        yield buf.getvalue()
                        buf.seek(0); buf.truncate(0)
            finally:
                await result.close()

            leftover = buf.getvalue()
            if leftover:
                yield leftover

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CSV /report stream crashed")
            chunk = buf.getvalue()
            if chunk:
                try:
                    yield chunk
                except Exception:
                    pass
            return

    today = datetime.now().strftime("%Y%m%d")
    filename = f"items_{body.station.value.lower()}_line{line_code}_{today}.csv"

    return StreamingResponse(
        acsv_iter(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


