# app/domain/v1/items_router.py
from fastapi import APIRouter, Query, Depends, HTTPException, Request
from typing import Optional, Annotated, List
from sqlalchemy.ext.asyncio import AsyncSession


from datetime import datetime


from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.core.db.repo.models import (
    EOrderBy, ItemSortField,
    EStation,EItemStatusCode,User
)

from app.domain.v1.item.schema import FixRequestBody, ItemEditIn, ItemEditOut, ItemReportRequest, ItemEventOut, ItemAckOut
from app.domain.v1.item.service import ItemService
from app.utils.helper.helper import (
    require_role
)

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
    svc: ItemService = Depends(get_service),
):
    return await svc.get_item_history(item_id)

@router.post("/{item_id}/fix-request")
async def submit_fix_request(
    item_id: int,
    body: FixRequestBody,
    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["OPERATOR"])
    return await svc.submit_fix_request(item_id, body, user)

@router.get("/{item_id}/images")
async def list_item_images(
    item_id: int,
    kinds: Optional[str] = Query(None, description="CSV: DETECTED,FIX,OTHER"),
    svc: ItemService = Depends(get_service),
):
    return await svc.list_item_images(item_id, kinds)

@router.post("/report", summary="Download CSV report")
async def get_csv_item_report(
    body: ItemReportRequest,
    request: Request,
    user: User = Depends(get_current_user),
    svc: ItemService = Depends(get_service),
):
    require_role(user, ["VIEWER"])
    return await svc.get_csv_item_report(body, request)


