# app/domain/v1/items_router.py
from fastapi import APIRouter, Query, Depends, HTTPException, Request
from typing import Optional, Annotated, List
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_, literal, text
from sqlalchemy.orm import aliased


from datetime import datetime
from app.core.config.config import settings
from io import StringIO

from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import (
    EOrderBy, Item, ItemSortField, ItemStatus, ProductionLine, ItemDefect, DefectType,
    Review, ItemImage, ItemEvent,
    EStation,EItemStatusCode,User
)

from app.domain.v1.item.schema import FixRequestBody, ItemEditIn, ItemEditOut, ItemReportRequest, ItemEventOut, ActorOut, ItemAckOut
from app.domain.v1.item.service import ItemService
from app.domain.v1.item.service import status_label, norm
from app.utils.helper.helper import (
    require_role,
    require_same_line,
    TZ
)
from fastapi.responses import StreamingResponse
import csv
import asyncio
import logging

router = APIRouter()

log = logging.getLogger(__name__)

def get_service(db: AsyncSession = Depends(get_db)) -> ItemService:
    return ItemService(db)

# ---------- GET /items ----------
@router.get("", summary="List items")
async def list_items(
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),
    sort_by: Annotated[Optional[ItemSortField], Query(description="field to sort by")] = None,
    order_by: Annotated[Optional[EOrderBy], Query(description="order direction (asc or desc)")] = None,

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

    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    return await svc.list_items(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        order_by=order_by,
        user_role=user.role,
        station=station,
        line_id=line_id,
        product_code=product_code,
        number=number,
        job_order_number=job_order_number,
        roll_width_min=roll_width_min,
        roll_width_max=roll_width_max,
        roll_id=roll_id,
        status=status,
        detected_from=detected_from,
        detected_to=detected_to,
    )
    
    
@router.get("/{item_id}")
async def get_item_detail(
    item_id: int,
    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    return await svc.get_item_detail(item_id)

@router.patch("/{item_id}", response_model=ItemEditOut)
async def edit_item(
    item_id: int,
    payload: ItemEditIn,
    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])
    item = await svc.edit_item(item_id, payload)
    return ItemEditOut.model_validate(item)

@router.post("/{item_id}/ack", response_model=ItemAckOut)
async def acknowledge_item(
    item_id: int,
    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])
    return await svc.ack_item(item_id, getattr(user, "id", None))


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

    # db.add(
    #     ItemEvent(
    #         item_id=it.id,
    #         actor_id=user.id,
    #         event_type="FIX_REQUEST_SUBMITTED",
    #         from_status_id=it.item_status_id,
    #         to_status_id=it.item_status_id,
    #     )
    # )

    await db.commit()
    return {"review_id": rv.id}

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

    base_cols = [
        Item.id.label("item_id"),
        Item.station,
        Item.line_id,
        Item.product_code,
        Item.roll_id,
        Item.roll_number,
        Item.bundle_number,
        Item.job_order_number,
        Item.roll_width,
        Item.detected_at,
        Item.ai_note,
        ItemStatus.code.label("status_code"),
    ]

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

    q = (
        select(
            base_sq.c.item_id,
            base_sq.c.station,
            base_sq.c.line_id,
            base_sq.c.product_code,
            base_sq.c.roll_id,
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

    header = [
        "PRODUCT CODE",
        "ROLL NUMBER" if body.station == EStation.ROLL else "BUNDLE NUMBER",
        "JOB ORDER NUMBER",
        "ROLL ID",
        "ROLL WIDTH",
        "TIMESTAMP",
        "STATUS",
    ]

    def row_to_list(m) -> list:
        product_code_val = m.get("product_code")
        job_order_val = m.get("job_order_number")
        width_val = m.get("roll_width")
        roll_id = m.get("roll_id")
        if body.station == EStation.BUNDLE:
            product_code_val = product_code_val or m.get("r_product_code")
            job_order_val = job_order_val or m.get("r_job_order_number")
            width_val = width_val if width_val is not None else m.get("r_roll_width")

        num_val = m.get("roll_number") if body.station == EStation.ROLL else m.get("bundle_number")
        status_str = status_label(m.get("status_code"), m.get("defects_csv"), None)
        dt = m.get("detected_at")
        readable_ts = dt.strftime("%d/%m/%Y %H:%M:%S") if dt else ""
        width_out = "" if width_val is None else str(width_val)
        return [product_code_val or "", num_val or "", job_order_val or "", roll_id, width_out, readable_ts, status_str]

    async def acsv_iter():
        buf = StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        THRESHOLD = 256 * 1024 

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


