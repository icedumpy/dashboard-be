# app/domain/v1/items_router.py
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from datetime import datetime
import os
from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.user.user_entity import User
from app.core.db.repo.models import (
    Item, ItemStatus, ProductionLine, ItemDefect, DefectType,
    Review, ItemImage, ItemEvent
)
from fastapi import Query, Request, Depends
from app.domain.v1.item.item_schema import FixRequestBody
from app.utils.helper.helper import (
    require_role,
    require_same_line,
    require_same_shift_if_operator,
    precondition_if_unmodified_since
)


router = APIRouter()


# ---------- GET /items ----------
@router.get("", summary="List items")
async def list_items(
    request: Request,
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),

    station: Optional[str] = Query(
        None, pattern="^(ROLL|BUNDLE)$", description="filter by station"
    ),
    line_code: Optional[str] = Query(None, description="e.g. 3 or 4"),
    product_code: Optional[str] = Query(None, description="contains match"),
    number: Optional[str] = Query(None, description="roll_number or bundle_number (contains)"),
    job_order_number: Optional[str] = Query(None, description="contains match"),
    roll_width_min: Optional[float] = Query(None, ge=0),
    roll_width_max: Optional[float] = Query(None, ge=0),

    # repeatable: ...?status=DEFECT&status=SCRAP
    status: Optional[List[str]] = Query(
        None, description="repeatable status codes"
    ),

    time_preset: Optional[str] = Query(
        None, pattern="^(today|yesterday|7d|30d|all)$"
    ),
    detected_from: Optional[datetime] = Query(None, description="ISO8601"),
    detected_to: Optional[datetime] = Query(None, description="ISO8601"),

    # repeatable include: ...?include=images&include=defects
    include: Optional[List[str]] = Query(
        None, description="optional includes: line,status,defects,images,reviews"
    ),

    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    # base query (exclude soft-deleted)
    q = select(Item).where(Item.deleted_at.is_(None))

    # joins needed for sorting & filters
    q = q.join(ItemStatus, Item.item_status_id == ItemStatus.id)
    if line_code:
        q = q.join(ProductionLine, Item.line_id == ProductionLine.id)

    # filters
    if station: q = q.where(Item.station == station)
    if line_code: q = q.where(ProductionLine.id == line_code)
    if product_code: q = q.where(Item.product_code.ilike(f"%{product_code}%"))
    if number:
        q = q.where(
            (Item.roll_number.ilike(f"%{number}%")) |
            (Item.bundle_number.ilike(f"%{number}%"))
        )
    if job_order_number: q = q.where(Item.job_order_number.ilike(f"%{job_order_number}%"))
    if roll_width_min is not None: q = q.where(Item.roll_width >= roll_width_min)
    if roll_width_max is not None: q = q.where(Item.roll_width <= roll_width_max)
    if status: q = q.where(ItemStatus.code.in_(status))

    # time range
    if detected_from: q = q.where(text("qc.items.detected_at >= :df")).params(df=detected_from)
    if detected_to: q = q.where(text("qc.items.detected_at <= :dt")).params(dt=detected_to)
    # (time_preset handling omitted here for brevity—map to df/dt)

    # sorting
    q = q.order_by(ItemStatus.display_order.asc(), Item.detected_at.desc(), Item.id.desc())

    # total
    total = (await db.execute(q.with_only_columns(text("count(*)")).order_by(None))).scalar()

    # page
    rows = (await db.execute(q.offset(offset).limit(page_size))).scalars().all()

    # counts (images/defects)
    data = []
    for it in rows:
        # eager load status & counts
        imgs = (await db.execute(
            select(text("count(*)")).select_from(ItemImage).where(ItemImage.item_id == it.id)
        )).scalar()
        defs = (await db.execute(
            select(text("count(*)")).select_from(ItemDefect).where(ItemDefect.item_id == it.id)
        )).scalar()

        # get status code
        st = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

        data.append({
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
            "images_count": imgs,
            "defects_count": defs,
        })

    resp = {
        "data": data,
        "pagination": {
            "page": page, "page_size": page_size,
            "total": total, "total_pages": (total + page_size - 1) // page_size
        }
    }
    # (optional "included" set can be added if `include` supplied)
    return resp

# ---------- GET /items/{id} ----------
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

    # defects
    defs = (await db.execute(
        select(DefectType.code, ItemDefect.meta)
        .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
        .where(ItemDefect.item_id == it.id)
    )).all()

    # images grouped by kind
    imgs = (await db.execute(
        select(ItemImage.id, ItemImage.kind, ItemImage.path)
        .where(ItemImage.item_id == it.id)
        .order_by(ItemImage.uploaded_at.desc())
    )).all()
    grouped = {"DETECTED": [], "FIX": [], "OTHER": []}
    for iid, kind, path in imgs:
        grouped.setdefault(kind, []).append({"id": iid, "path": path})

    # reviews (all)
    rws = (await db.execute(
        select(Review).where(Review.item_id == it.id).order_by(Review.submitted_at.desc())
    )).scalars().all()

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
                "reviewed_by": rv.reviewed_by, "reviewed_at": rv.reviewed_at.isoformat() if rv.reviewed_at else None,
                "submit_note": rv.submit_note, "review_note": rv.review_note, "reject_reason": rv.reject_reason
            } for rv in rws
        ]
    }

# ---------- POST /items/{id}/fix-request ----------
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
    if user.role == "OPERATOR":
        require_same_shift_if_operator(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    # status check
    st_code = (
        await db.execute(
            select(ItemStatus.code).where(ItemStatus.id == it.item_status_id)
        )
    ).scalar()
    if st_code not in ("DEFECT", "RECHECK", "REJECTED"):
        raise HTTPException(status_code=400, detail="Fix request allowed only for DEFECT or RECHECK")

    # --- Images validation ---
    image_ids = list(getattr(body, "image_ids", []) or [])
    if not image_ids:
        raise HTTPException(status_code=400, detail="Provide at least 1 image_id")

    # Normalize and de-dup
    try:
        image_ids = list({int(i) for i in image_ids})
    except Exception:
        raise HTTPException(status_code=400, detail="image_ids must be integers")

    # Read current image rows
    # Adjust ItemImage.* fields to your actual model/columns.
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

    # Create review
    rv = Review(
        item_id=it.id,
        review_type="DEFECT_FIX",
        state="PENDING",
        submitted_by=user.id,
        submit_note=note,
    )
    db.add(rv)
    await db.flush()  # rv.id available

    # Link images as FIX; protect against races (link only if still unlinked & same/none item)
    # If you don't have item_id on item_images, remove that predicate.
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

    # If concurrent process linked any image, rowcount will be lower → 409
    if upd.rowcount != len(image_ids):
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Images changed concurrently; please retry"
        )

    # keep current_review_id (per requirement)
    it.current_review_id = rv.id

    # event
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

# ---------- POST /items/{id}/scrap ----------
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
    if user.role == "OPERATOR":
        require_same_shift_if_operator(user, it)

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

# ---------- PATCH /items/{id}/decision ----------
