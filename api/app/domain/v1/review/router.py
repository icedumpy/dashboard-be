from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Optional, Annotated
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, insert, text
from datetime import datetime


from app.core.db.session import get_db
from app.domain.v1.review.service import ReviewService
from app.core.security.auth import get_current_user
from app.core.db.repo.models import (
    ItemStatus, Review, ItemEvent, Item, ItemDefect, DefectType,EReviewState,
    ReviewSortField, EOrderBy, User
)
from app.domain.v1.review.schema import DecisionRequestBody
from app.utils.helper.helper import (
    require_role,
)
from zoneinfo import ZoneInfo
import json
from datetime import datetime, timedelta
from typing import Optional, Annotated


TH = ZoneInfo("Asia/Bangkok")

router = APIRouter()




def get_service(db: AsyncSession = Depends(get_db)) -> ReviewService:
    return ReviewService(db)

@router.get("")
async def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sort_by: Annotated[Optional[ReviewSortField], Query(description="field to sort by")] = None,
    order_by: Annotated[Optional[EOrderBy], Query(description="order direction (asc or desc)")] = None,
    line_id: Optional[int] = Query(None),
    review_state: Annotated[list[EReviewState] | None, Query()] = None,
    defect_type_id: Optional[int] = Query(None),

    reviewed_at_from: Optional[datetime] = Query(None, description="reviewed_at >= this ISO8601 datetime"),
    reviewed_at_to: Optional[datetime] = Query(None, description="reviewed_at <= this ISO8601 datetime"),
    submitted_at_from: Optional[datetime] = Query(None, description="submitted_at >= this ISO8601 datetime"),
    submitted_at_to: Optional[datetime] = Query(None, description="submitted_at <= this ISO8601 datetime"),

    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    svc: ReviewService = Depends(get_service),
):
    require_role(user, ["VIEWER", "INSPECTOR"])
    return await svc.list_reviews(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        order_by=order_by,
        line_id=line_id,
        review_state=review_state,
        defect_type_id=defect_type_id,
        reviewed_at_from=reviewed_at_from,
        reviewed_at_to=reviewed_at_to,
        submitted_at_from=submitted_at_from,
        submitted_at_to=submitted_at_to,
    )


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
