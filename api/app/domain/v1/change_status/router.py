from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Literal, Annotated
import math

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert, delete, text, and_, literal
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.sql import func

from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import StatusChangeRequest, StatusChangeRequestDefect, ItemEvent, Item, ItemDefect, DefectType, ItemStatus, StatusChangeSortField, EOrderBy
from app.domain.v1.change_status.schema import StatusChangeRequestOut, DecisionRequestBody, StatusChangeRequestCreate, ListResponseOut, SummaryOut, PaginationOut, ListResponseOut
from app.domain.v1.change_status.service import ChangeStatusService
from app.utils.helper.helper import (
    require_role,
)

router = APIRouter()

def get_service(db: AsyncSession = Depends(get_db)) -> ChangeStatusService:
    return ChangeStatusService(db)

async def _validate_defect_type_ids(db: AsyncSession, ids: list[int]) -> list[int]:
    uniq = sorted({int(x) for x in ids or []})
    if not uniq:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "defect_type_ids is required and cannot be empty")
    q = select(DefectType.id).where(DefectType.id.in_(uniq))
    found = {r[0] for r in (await db.execute(q)).all()}
    missing = [i for i in uniq if i not in found]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid defect_type_ids (not found): {missing}")
    return uniq


@router.post("", response_model=StatusChangeRequestOut)
async def create_status_change_request(
    body: StatusChangeRequestCreate,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    require_role(user, ["OPERATOR"])
    
    
    # [NORMAL, SCRAP, DEFECT, LEFT]
    # Normal, Scrap -> Defct ( NO QC )
    # Defect -> Normal, Scrap ( REQUIRE QC )
    # Defect -> LeftOverRoll, Defect ( NO QC )

    try:
        item_q = (
            select(Item)
            .options(selectinload(Item.status))
            .where(Item.id == body.item_id)
            .with_for_update()
        )
        item = (await db.execute(item_q)).scalar_one_or_none()
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        from_status_id = item.item_status_id

        tgt = (
            await db.execute(select(ItemStatus).where(ItemStatus.id == body.to_status_id))
        ).scalar_one_or_none()
        
        if not tgt:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"to_status_id not found: {body.to_status_id}"
            )
            
        pending_id = await db.scalar(
            select(StatusChangeRequest.id)
            .where(
                StatusChangeRequest.item_id == body.item_id,
                StatusChangeRequest.state == "PENDING",
            )
            .with_for_update(skip_locked=True) 
            .limit(1)
        )
        if pending_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Item already has a pending request (id={pending_id})",
            )

        current_code = getattr(item.status, "code", None)
        target_code = tgt.code
        going_to_defect = (target_code == "DEFECT")
        going_to_leftover_roll = (target_code == "LEFTOVER_ROLL")
        is_normal_like = current_code in ("NORMAL", "LEFTOVER_ROLL")

        req = StatusChangeRequest(
            item_id=body.item_id,
            from_status_id=from_status_id,
            to_status_id=body.to_status_id,
            reason=body.reason,
            meta=body.meta,
            requested_by=user.id,
        )
        db.add(req)
        await db.flush()

        if body.defect_type_ids:
            uniq = sorted({int(x) for x in body.defect_type_ids})
            if uniq:
                rows = [{"request_id": req.id, "defect_type_id": dtid} for dtid in uniq]
                await db.execute(insert(StatusChangeRequestDefect).values(rows))

        if going_to_defect or going_to_leftover_roll:
            defect_ids_applied: list[int] = []
            if going_to_defect:
                if not body.defect_type_ids:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "defect_type_ids is required when changing NORMAL -> DEFECT"
                        if is_normal_like
                        else "defect_type_ids is required when setting status to DEFECT",
                    )
                defect_ids_applied = await _validate_defect_type_ids(db, body.defect_type_ids)

            await db.execute(
                update(Item)
                .where(Item.id == item.id)
                .values(item_status_id=body.to_status_id, updated_at=func.now())
            )

            if going_to_defect:
                await db.execute(delete(ItemDefect).where(ItemDefect.item_id == item.id))
                if defect_ids_applied:
                    rows = [
                        {"item_id": item.id, "defect_type_id": dtid, "meta": body.meta or {}}
                        for dtid in defect_ids_applied
                    ]
                    await db.execute(insert(ItemDefect).values(rows))

            await db.execute(
                update(StatusChangeRequest)
                .where(StatusChangeRequest.id == req.id)
                .values(state="APPROVED", approved_by=user.id, approved_at=func.now())
            )
            
            db.add(
                ItemEvent(
                    item_id=item.id,
                    actor_id=user.id,
                    event_type="STATUS_CHANGED",
                    from_status_id=from_status_id,
                    to_status_id=body.to_status_id,
                    details={
                        "source": "AUTO_APPROVE",
                        "reason": body.reason,
                        "meta": body.meta,
                        "defect_type_ids": defect_ids_applied if going_to_defect else [],
                    },
                )
            )

        await db.commit()

        defect_rows = await db.execute(
            select(StatusChangeRequestDefect.defect_type_id)
            .where(StatusChangeRequestDefect.request_id == req.id)
            .order_by(StatusChangeRequestDefect.defect_type_id)
        )
        defect_type_ids = [r[0] for r in defect_rows.all()]

        req_row = (
            await db.execute(
                select(
                    StatusChangeRequest.id,
                    StatusChangeRequest.item_id,
                    StatusChangeRequest.from_status_id,
                    StatusChangeRequest.to_status_id,
                    StatusChangeRequest.state,
                    StatusChangeRequest.requested_by,
                    StatusChangeRequest.requested_at,
                    StatusChangeRequest.approved_by,
                    StatusChangeRequest.approved_at,
                    StatusChangeRequest.reason,
                    StatusChangeRequest.meta,
                ).where(StatusChangeRequest.id == req.id)
            )
        ).one()

        return StatusChangeRequestOut(
            id=req_row.id,
            item_id=req_row.item_id,
            from_status_id=req_row.from_status_id,
            to_status_id=req_row.to_status_id,
            state=req_row.state,
            requested_by=req_row.requested_by,
            requested_at=req_row.requested_at.isoformat()
                if hasattr(req_row.requested_at, "isoformat")
                else str(req_row.requested_at),
            approved_by=req_row.approved_by,
            approved_at=req_row.approved_at.isoformat()
                if req_row.approved_at and hasattr(req_row.approved_at, "isoformat")
                else (str(req_row.approved_at) if req_row.approved_at else None),
            reason=req_row.reason,
            meta=req_row.meta,
            defect_type_ids=defect_type_ids,
        )

    except Exception:
        await db.rollback()
        raise

@router.get("", response_model=ListResponseOut)
async def list_status_change_requests(
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),
    line_id: Optional[int] = Query(None, description="filter by production line id"),
    station: Optional[Literal["ROLL", "BUNDLE"]] = Query(
        None, description='filter by station type: "ROLL" or "BUNDLE"'
    ),
    sort_by: Annotated[Optional[StatusChangeSortField], Query(description="field to sort by")] = None,
    order_by: Annotated[Optional[EOrderBy], Query(description="order direction (asc or desc)")] = None,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
    svc: ChangeStatusService = Depends(get_service),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])

    return await svc.list_requests(
        page=page,
        page_size=page_size,
        line_id=line_id,
        station=station,
        sort_by=sort_by,
        order_by=order_by,
    )
    
    
@router.patch("/{request_id}/decision", response_model=StatusChangeRequestOut)
async def decide_status_change_request(
    request_id: int,
    body: DecisionRequestBody,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    require_role(user, ["INSPECTOR"])

    decision = (body.decision or "").upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be APPROVED or REJECTED")

    try:
        q_req = (
            select(StatusChangeRequest)
            .options(selectinload(StatusChangeRequest.defects))
            .where(StatusChangeRequest.id == request_id)
            .with_for_update()
        )
        req = (await db.execute(q_req)).scalar_one_or_none()
        if not req:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")

        if req.state != "PENDING":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Request already processed")

        new_reason = req.reason
        if body.note:
            new_reason = f"{(req.reason or '').strip()} | {body.note}".strip(" |")

        if decision == "REJECTED":
            await db.execute(
                update(StatusChangeRequest)
                .where(StatusChangeRequest.id == request_id)
                .values(
                    state="REJECTED",
                    approved_by=user.id,
                    approved_at=func.now(),
                    reason=new_reason,
                )
            )
            await db.commit()
            await db.refresh(req)
            return StatusChangeRequestOut(
                id=req.id,
                item_id=req.item_id,
                from_status_id=req.from_status_id,
                to_status_id=req.to_status_id,
                state=req.state,
                requested_by=req.requested_by,
                requested_at=req.requested_at.isoformat() if hasattr(req.requested_at, "isoformat") else str(req.requested_at),
                approved_by=req.approved_by,
                approved_at=req.approved_at.isoformat() if req.approved_at and hasattr(req.approved_at, "isoformat") else (str(req.approved_at) if req.approved_at else None),
                reason=req.reason,
                meta=req.meta,
                defect_type_ids=[d.defect_type_id for d in req.defects],
            )

        q_item = (
            select(Item)
            # .options(selectinload(Item.item_status))
            .where(Item.id == req.item_id)
            .with_for_update()
        )
        item = (await db.execute(q_item)).scalar_one_or_none()
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

        if item.item_status_id != req.from_status_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Item status has changed: expected from_status_id={req.from_status_id}, actual={item.item_status_id}",
            )

        tgt = (await db.execute(select(ItemStatus).where(ItemStatus.id == req.to_status_id))).scalar_one_or_none()
        if not tgt:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"to_status_id not found: {req.to_status_id}")

        going_to_defect = (tgt.code == "DEFECT")
        defect_ids = [d.defect_type_id for d in req.defects]

        if going_to_defect and not defect_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "defect_type_ids required when approving to DEFECT"
            )

        await db.execute(
            update(Item)
            .where(Item.id == item.id)
            .values(item_status_id=req.to_status_id, updated_at=text("now()"))
        )

        if going_to_defect:
            await db.execute(delete(ItemDefect).where(ItemDefect.item_id == item.id))
            if defect_ids:
                rows = [{"item_id": item.id, "defect_type_id": dtid, "meta": req.meta or {}} for dtid in sorted(set(defect_ids))]
                await db.execute(insert(ItemDefect).values(rows))
        await db.execute(
            update(StatusChangeRequest)
            .where(StatusChangeRequest.id == request_id)
            .values(
                state="APPROVED",
                approved_by=user.id,
                approved_at=func.now(),
                reason=new_reason,
            )
        )

        if body.decision == "APPROVED":
            db.add(
                ItemEvent(
                    item_id=item.id,
                    actor_id=req.requested_by,
                    event_type="STATUS_CHANGED",
                    from_status_id=req.from_status_id,
                    to_status_id=req.to_status_id,
                    details={
                        "source": "QC_DECISION",
                        "note": body.note,
                        "defect_type_ids": defect_ids if going_to_defect else [],
                    },
                )
            )

        await db.commit()
        await db.refresh(req)

        return StatusChangeRequestOut(
            id=req.id,
            item_id=req.item_id,
            from_status_id=req.from_status_id,
            to_status_id=req.to_status_id,
            state=req.state,
            requested_by=req.requested_by,
            requested_at=req.requested_at.isoformat() if hasattr(req.requested_at, "isoformat") else str(req.requested_at),
            approved_by=req.approved_by,
            approved_at=req.approved_at.isoformat() if req.approved_at and hasattr(req.approved_at, "isoformat") else (str(req.approved_at) if req.approved_at else None),
            reason=req.reason,
            meta=req.meta,
            defect_type_ids=[d.defect_type_id for d in req.defects],
        )

    except Exception:
        await db.rollback()
        raise