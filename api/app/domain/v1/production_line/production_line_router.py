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
    ItemStatus, ProductionLine, ItemDefect, DefectType,
    Review, ItemImage, ItemEvent
)
from fastapi import Query, Request, Depends
from app.domain.v1.item.item_schema import FixRequestBody, DecisionRequestBody

router = APIRouter()


@router.get("", summary="List production lines")
async def list_lines(
    request: Request, 
    line_code: Optional[str] = Query(None, description="e.g. 3 or 4"), db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(ProductionLine)
    if line_code: q = q.where(ProductionLine.id == line_code)
    
    q = q.order_by(ProductionLine.code.asc())
    rows = (await db.execute(q)).scalars().all()
    
    resp = {
        "data": rows,
    }
    return resp
