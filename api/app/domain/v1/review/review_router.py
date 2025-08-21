# app/domain/v1/items_router.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.user.user_entity import User
from app.core.db.repo.models import (
    ItemStatus, Review, ItemEvent, Item
)
from app.domain.v1.review.review_schema import DecisionRequestBody
from fastapi import Request, Depends
from app.utils.helper.helper import (
    require_role,
    require_same_line,
)


router = APIRouter()

@router.post("/{review_id}/decision")
async def decide_fix(
    request: Request,
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
      
    require_same_line(user, it)

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
