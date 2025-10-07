from fastapi import APIRouter, Depends, Query
from typing import Optional, Union, List
from app.core.db.repo.models import EItemStatusCode, EOrderBy
from app.domain.v1.item_status.service import ItemStatusService 
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db.session import get_db

router = APIRouter()


def get_service(db: AsyncSession = Depends(get_db)) -> ItemStatusService:
    return ItemStatusService(db)

@router.get("", summary="List item statuses")
async def list_item_statuses(
    svc: ItemStatusService = Depends(get_service),
    include_inactive: bool = Query(False, description="Include inactive statuses"),
    ids: Optional[List[int]] = Query(None, description="Filter by IDs, e.g., ?ids=1&ids=2"),
    codes: Optional[List[Union[str, EItemStatusCode]]] = Query(None, description="Filter by codes, e.g., ?codes=QC_PASSED&codes=SCRAP"),
    search: Optional[str] = Query(None, description="Case-insensitive match on code/name"),
    order_by: str = Query("display_order", description="Sort column (e.g., display_order, code, id)"),
    direction: EOrderBy = Query(EOrderBy.ASC, description="ASC or DESC"),
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(20, ge=1, le=100, description="items per page (max 100)"),
):
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    rows = await svc.list_item_statuses(
        include_inactive=include_inactive,
        ids=ids,
        codes=codes,
        search=search,
        order_by=order_by,
        direction=direction,
        limit=page_size,
        offset=offset,
    )

    data = [
        {
            "id": r.id,
            "code": getattr(r, "code", None),
            "name_th": getattr(r, "name_th", None),
            "is_active": getattr(r, "is_active", None),
            "display_order": getattr(r, "display_order", None),
        }
        for r in rows
    ]

    return {
        "data": data,
        "meta": {
            "page": page,
            "page_size": page_size,
            "count": len(data),
        },
    }
