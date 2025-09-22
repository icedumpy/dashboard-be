from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Optional, Annotated
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, insert, text
from datetime import datetime


from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import User
from app.core.db.repo.models import (
    ItemStatus, Review, ItemEvent, Item, ItemDefect, DefectType,EReviewState
)
from app.domain.v1.review.schema import DecisionRequestBody
from app.utils.helper.helper import (
    require_role,
)
from zoneinfo import ZoneInfo
import json


TH = ZoneInfo("Asia/Bangkok")

router = APIRouter()


from datetime import datetime, timedelta
from typing import Optional, Annotated

@router.get("")
async def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    line_id: Optional[int] = Query(None),
    review_state: Annotated[list[EReviewState] | None, Query()] = None,
    defect_type_id: Optional[int] = Query(None),

    reviewed_at_from: Optional[datetime] = Query(None, description="reviewed_at >= this ISO8601 datetime"),
    reviewed_at_to: Optional[datetime] = Query(None, description="reviewed_at <= this ISO8601 datetime"),
    submitted_at_from: Optional[datetime] = Query(None, description="submitted_at >= this ISO8601 datetime"),
    submitted_at_to: Optional[datetime] = Query(None, description="submitted_at <= this ISO8601 datetime"),

    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "INSPECTOR"])
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    rn = func.row_number().over(
        partition_by=Review.item_id,
        order_by=(Review.updated_at.desc(), Review.id.desc())
    ).label("rn")

    base_cols = (
        select(
            Review.id.label("rid"),
            Review.item_id.label("iid"),
            Review.state.label("state"),
            Review.updated_at.label("r_updated_at"),
            Item.detected_at.label("i_detected_at"),
            Item.id.label("item_pk"),
            rn,
        )
        .join(Item, Item.id == Review.item_id)
        .join(ItemStatus, Item.item_status_id == ItemStatus.id)
    )

    if line_id:
        base_cols = base_cols.where(Item.line_id == line_id)
    if defect_type_id:
        base_cols = base_cols.join(ItemDefect, ItemDefect.item_id == Item.id).where(
            ItemDefect.defect_type_id == defect_type_id
        )
    if review_state:
        base_cols = base_cols.where(Review.state.in_(review_state))
    if reviewed_at_from:
        base_cols = base_cols.where(Review.reviewed_at >= reviewed_at_from)
    if reviewed_at_to:
        base_cols = base_cols.where(Review.reviewed_at <= reviewed_at_to)
    if submitted_at_from:
        base_cols = base_cols.where(Review.created_at >= submitted_at_from)
    if submitted_at_to:
        base_cols = base_cols.where(Review.created_at <= submitted_at_to)

    s = base_cols.subquery("s")

    total = await db.scalar(
        select(func.count()).select_from(select(s.c.rid).where(s.c.rn == 1).subquery())
    )

    ids_q = (
        select(s.c.rid)
        .where(s.c.rn == 1)
        .order_by(s.c.r_updated_at.desc(), s.c.i_detected_at.desc(), s.c.item_pk.desc())
        .offset(offset)
        .limit(page_size)
    )
    review_ids = [row[0] for row in (await db.execute(ids_q)).all()]

    rn2 = func.row_number().over(
        partition_by=Review.item_id,
        order_by=(Review.updated_at.desc(), Review.id.desc())
    ).label("rn")

    sum_cols = (
        select(
            Review.state.label("state"),
            rn2,
        )
        .join(Item, Item.id == Review.item_id)
    )

    if line_id:
        sum_cols = sum_cols.where(Item.line_id == line_id)
    if defect_type_id:
        sum_cols = sum_cols.join(ItemDefect, ItemDefect.item_id == Item.id).where(
            ItemDefect.defect_type_id == defect_type_id
        )
    if review_state:
        sum_cols = sum_cols.where(Review.state.in_(review_state))
    if reviewed_at_from:
        sum_cols = sum_cols.where(Review.reviewed_at >= reviewed_at_from)
    if reviewed_at_to:
        sum_cols = sum_cols.where(Review.reviewed_at <= reviewed_at_to)
    if submitted_at_from:
        sum_cols = sum_cols.where(Review.created_at >= submitted_at_from)
    if submitted_at_to:
        sum_cols = sum_cols.where(Review.created_at <= submitted_at_to)

    sb = sum_cols.subquery("sb")

    sum_rows = (await db.execute(
        select(sb.c.state, func.count().label("cnt"))
        .where(sb.c.rn == 1)             
        .group_by(sb.c.state)
    )).all()
    sum_map = {row.state: int(row.cnt) for row in sum_rows}
    summary = {
        "pending":  sum_map.get("PENDING", 0),
        "approved": sum_map.get("APPROVED", 0),
        "rejected": sum_map.get("REJECTED", 0),
        "total":    sum(sum_map.values()),
    }

    if not review_ids:
        return {
            "data": [],
            "summary": summary,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
            },
        }

    reviews = (await db.execute(select(Review).where(Review.id.in_(review_ids)))).scalars().all()
    id_pos = {rid: i for i, rid in enumerate(review_ids)}
    reviews.sort(key=lambda rv: id_pos[rv.id])

    item_ids = {rv.item_id for rv in reviews}
    items_rows = await db.execute(
        select(
            Item.id, Item.station, Item.line_id, Item.product_code, Item.roll_number, Item.roll_id,
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

    data = []
    for rv in reviews:
        it = items.get(rv.item_id)
        if not it:
            continue
        st = statuses.get(it.item_status_id)
        number = it.roll_number or it.bundle_number
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
                "number": number,
                "roll_id": it.roll_id,
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
        "summary": summary,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total or 0,
            "total_pages": ((total or 0) + page_size - 1) // page_size,
        },
    }
@router.get("/{review_id}")
async def get_review_by_id(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["INSPECTOR"])

    rv = await db.get(Review, review_id)
    if not rv:
        raise HTTPException(status_code=404, detail="Review not found")

    defects_rows = (
        await db.execute(
            select(
                ItemDefect.item_id,
                ItemDefect.id.label("item_defect_id"),
                ItemDefect.defect_type_id,
                DefectType.code,
                DefectType.name_th,
                ItemDefect.meta,
            )
            .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
            .where(ItemDefect.item_id == rv.item_id)
        )
    ).all()

    defects = [
        {
            "item_defect_id": r.item_defect_id,
            "item_id": r.item_id,
            "defect_type_id": r.defect_type_id,
            "defect_code": r.code,
            "defect_name_th": r.name_th,
            "meta": r.meta or {},
        }
        for r in defects_rows
    ]

    request_event = (
        await db.execute(
            select(ItemEvent)
            .where(
                ItemEvent.item_id == rv.item_id,
                ItemEvent.event_type == "REQUEST_STATUS_CHANGE",
            )
            .order_by(ItemEvent.created_at.desc(), ItemEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    details = {}
    if request_event:
        details = request_event.details or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}

    defect_type_ids = sorted({int(x) for x in (details.get("defect_type_ids") or []) if x is not None})

    request_defects = []
    if defect_type_ids:
        rows = (
            await db.execute(
                select(
                    DefectType.id.label("defect_type_id"),
                    DefectType.code,
                    DefectType.name_th,
                ).where(DefectType.id.in_(defect_type_ids))
            )
        ).all()
        request_defects = [
            {
                "defect_type_id": r.defect_type_id,
                "defect_code": r.code,
                "defect_name_th": r.name_th,
            }
            for r in rows
        ]

    request_event_payload = (
        {
            "id": request_event.id,
            "event_type": request_event.event_type,
            "from_status_id": request_event.from_status_id,
            "to_status_id": request_event.to_status_id,
            "defects": request_defects,
            "created_at": request_event.created_at.isoformat()
            if getattr(request_event, "created_at", None)
            else None,
        }
        if request_event
        else None
    )

    review_payload = {
        "id": rv.id,
        "item_id": rv.item_id,
        "review_type": rv.review_type,     
        "state": rv.state,                
        "submitted_by": getattr(rv, "submitted_by", None),
        "submitted_at": rv.submitted_at.isoformat() if getattr(rv, "submitted_at", None) else None,
        "reviewed_by": getattr(rv, "reviewed_by", None),
        "reviewed_at": rv.reviewed_at.isoformat() if getattr(rv, "reviewed_at", None) else None,
        "review_note": getattr(rv, "review_note", None),
        "reject_reason": getattr(rv, "reject_reason", None),
        "current_review_id": rv.id,
    }

    return {
        "review": review_payload,
        "defects": defects,
        "request_status": request_event_payload,
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
    if not it:
        raise HTTPException(status_code=404, detail="Item not found")


    previous_status_id = it.item_status_id
    decision = getattr(body, "decision")
    note = getattr(body, "note")

    if rv.item_id != it.id or rv.state != "PENDING":
        raise HTTPException(status_code=400, detail="Invalid or non-pending review")

    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    rv.reviewed_by = user.id
    rv.reviewed_at = datetime.now(TH)

    # defect_status_id = (
    #     await db.execute(
    #         select(ItemStatus.id).where(ItemStatus.code == "DEFECT")
    #     )
    # ).scalar_one()

    if decision == "APPROVED":
        rv.state = "APPROVED"
        rv.review_note = note

        qc_pass_status_id = (
            await db.execute(
                select(ItemStatus.id).where(ItemStatus.code == "QC_PASSED")
            )
        ).scalar_one()
        
        db.add(
            ItemEvent(
                item_id=it.id,
                actor_id=rv.submitted_by,
                event_type="FIX_DECISION_APPROVED",
                from_status_id=previous_status_id,
                to_status_id=qc_pass_status_id,
            )
        )
        it.item_status_id = qc_pass_status_id

    else:
        rv.state = "REJECTED"
        rv.reject_reason = note

        rej_status_id = (
            await db.execute(
                select(ItemStatus.id).where(ItemStatus.code == "REJECTED")
            )
        ).scalar_one()
        it.item_status_id = rej_status_id

        db.add(
            ItemEvent(
                item_id=it.id,
                actor_id=rv.submitted_by,
                event_type="FIX_DECISION_REJECTED",
                from_status_id=previous_status_id,
                to_status_id=rej_status_id,
            )
        )

    await db.commit()

    return {
        "ok": True,
        "new_status": (
            await db.scalar(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))
        ),
    }
