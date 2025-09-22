from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.db.session import get_db
from app.core.db.repo.models import (
    DefectType
)

router = APIRouter()


@router.get("", summary="List defect types")
async def defect_types(
    defect_code: Optional[str] = Query(None, description="e.g. LABEL, BARCODE, TOP, BOTOOM"), 
    db: AsyncSession = Depends(get_db),
):
    q = select(DefectType)
    if defect_code: q = q.where(DefectType.code == defect_code)
    
    q = q.order_by(DefectType.display_order.asc())
    rows = (await db.execute(q)).scalars().all()
    
    resp = {
        "data": rows,
    }
    return resp
