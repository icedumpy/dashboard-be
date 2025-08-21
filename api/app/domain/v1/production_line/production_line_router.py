from fastapi import APIRouter, Depends, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.db.session import get_db
from app.core.db.repo.models import (
    ProductionLine
)

router = APIRouter()


@router.get("", summary="List production lines")
async def list_lines(
    line_code: Optional[str] = Query(None, description="e.g. 3 or 4"), 
    db: AsyncSession = Depends(get_db),
):
    q = select(ProductionLine)
    if line_code: q = q.where(ProductionLine.code == line_code)
    
    q = q.order_by(ProductionLine.code.asc())
    rows = (await db.execute(q)).scalars().all()
    
    resp = {
        "data": rows,
    }
    return resp
