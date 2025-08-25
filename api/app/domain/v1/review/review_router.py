from fastapi import APIRouter, Query, Depends, HTTPException, Request
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct
from datetime import datetime


from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import User
from app.core.db.repo.models import (
    ItemStatus, Review, ItemEvent, Item, ItemDefect, DefectType,EReviewState
)
from app.domain.v1.review.review_schema import DecisionRequestBody
from app.utils.helper.helper import (
    require_role,
)

router = APIRouter()


@router.get("")
async def list_reviews(
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),
    line_id: Optional[int] = Query(None, description="line id"),
    review_state: Optional[EReviewState] = Query(None, description="PENDING | APPROVED | REJECTED"),
    defect_type_id: Optional[int] = Query(None, description="defect type id"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "INSPECTOR"])

    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    # ----- build base query (joins kept minimal to avoid duplication) -----
    base = (
        select(Review.id)
        .join(Item, Item.id == Review.item_id)
        .join(ItemStatus, Item.item_status_id == ItemStatus.id)
    )
    if review_state:
        base = base.where(Review.state == review_state)
    if line_id:
        base = base.where(Item.line_id == line_id)
    if defect_type_id:
        base = base.join(ItemDefect, ItemDefect.item_id == Item.id).where(
            ItemDefect.defect_type_id == defect_type_id
        )

    # ----- total count (distinct reviews) -----
    total = (
        await db.execute(
            select(func.count(distinct(Review.id))).select_from(base.subquery())
        )
    ).scalar_one()

    # ----- fetch paginated review ids in desired order -----
    ordered = base.order_by(
        ItemStatus.display_order.asc(),
        Item.detected_at.desc(),
        Item.id.desc(),
    )
    id_rows = await db.execute(ordered.offset(offset).limit(page_size))
    review_ids = [r[0] for r in id_rows.all()]
    if not review_ids:
        return {
            "data": [],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
            },
        }

    # ----- load core reviews & items -----
    reviews = (
        await db.execute(
            select(Review).where(Review.id.in_(review_ids))
        )
    ).scalars().all()

    # preserve original ordering
    id_pos = {rid: i for i, rid in enumerate(review_ids)}
    reviews.sort(key=lambda rv: id_pos[rv.id])

    item_ids = {rv.item_id for rv in reviews}
    items_rows = await db.execute(
        select(
            Item.id, Item.station, Item.line_id, Item.product_code, Item.roll_number,
            Item.bundle_number, Item.job_order_number, Item.roll_width, Item.detected_at,
            Item.item_status_id, Item.ai_note
        ).where(Item.id.in_(item_ids))
    )
    items = {r.id: r for r in items_rows.all()}

    status_ids = {getattr(items[iid], "item_status_id") for iid in item_ids if iid in items}
    status_rows = await db.execute(
        select(ItemStatus.id, ItemStatus.code, ItemStatus.name_th, ItemStatus.display_order)
        .where(ItemStatus.id.in_(status_ids))
    )
    statuses = {r.id: r for r in status_rows.all()}

    # defects (batched)
    defects_rows = await db.execute(
        select(
            ItemDefect.item_id,
            ItemDefect.id,
            ItemDefect.defect_type_id,
            DefectType.code,
            DefectType.name_th,
            ItemDefect.meta
        )
        .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
        .where(ItemDefect.item_id.in_(item_ids))
    )
    defects_by_item: dict[int, list[dict]] = {}
    for row in defects_rows.all():
        defects_by_item.setdefault(row.item_id, []).append({
            "id": row.id,
            "defect_type_id": row.defect_type_id,
            "defect_type_code": row.code,
            "defect_type_name": row.name_th,
            "meta": row.meta,
        })

    # ----- shape response -----
    data = []
    for rv in reviews:
        it = items.get(rv.item_id)
        if not it:
            # item was deleted between queries; skip
            continue
        st = statuses.get(it.item_status_id)
        number = it.roll_number or it.bundle_number  # unify into one field for FE use
        decision_note = getattr(rv, "review_note", None) or getattr(rv, "reject_reason", None)

        data.append({
            "id": rv.id,
            "type": rv.review_type,
            "state": rv.state,
            "submitted_by": rv.submitted_by,
            "submitted_at": getattr(rv, "created_at", None),
            "submit_note": getattr(rv, "submit_note", None),
            "reviewed_by": getattr(rv, "reviewed_by", None),
            "reviewed_at": getattr(rv, "reviewed_at", None),
            "decision_note": decision_note,
            "item": {
                "id": it.id,
                "station": it.station,
                "line_id": it.line_id,
                "product_code": it.product_code,
                "number": number,  # roll_number or bundle_number
                "job_order_number": it.job_order_number,
                "roll_width": it.roll_width,
                "detected_at": it.detected_at,
                "ai_note": it.ai_note,
                "status": {
                    "id": it.item_status_id,
                    "code": getattr(st, "code", None),
                    "name": getattr(st, "name", None),
                    "display_order": getattr(st, "display_order", None),
                },
            },
            "defects": defects_by_item.get(it.id, []),
        })

    return {
        "data": data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }
    
    
@router.post("/{review_id}/decision")
async def decide_fix(
    review_id: int,
    body: DecisionRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["INSPECTOR"])
    rv = await db.get(Review, review_id)
    if not rv:
        raise HTTPException(status_code=404, detail="Review not found")

    it = await db.get(Item, rv.item_id)
    
    decision = getattr(body, 'decision')
    note = getattr(body, 'note')

    if not rv or rv.item_id != it.id or rv.state != "PENDING":
        raise HTTPException(status_code=400, detail="Invalid or non-pending review")

    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    rv.reviewed_by = user.id
    rv.reviewed_at = datetime.utcnow()

    if decision == "APPROVED":
        rv.state = "APPROVED"
        rv.review_note = note
        new_status_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "QC_PASSED"))).scalar_one()
        it.item_status_id = new_status_id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="FIX_DECISION_APPROVED", from_status_id=None, to_status_id=new_status_id))
    else:
        rv.state = "REJECTED"
        rv.reject_reason = note
        rej_status_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "REJECTED"))).scalar_one()
        it.item_status_id = rej_status_id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="FIX_DECISION_REJECTED", from_status_id=None, to_status_id=rej_status_id))

    # keep current_review_id as-is (history)
    await db.commit()
    return {"ok": True, "new_status": "QC_PASSED" if decision=="APPROVED" else "REJECTED"}
