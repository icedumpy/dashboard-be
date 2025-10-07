
from fastapi import APIRouter, HTTPException, status
from typing import Optional, Sequence, Union, List, Dict, Any, Set, Iterable
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.orm import aliased
from sqlalchemy import select, update, delete, insert, or_, func, case, and_, asc, desc, exists, literal, literal_column, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.v1.item.schema import FixRequestBody, ItemEditIn, ItemAckOut
from app.utils.helper.helper import current_shift_window, TZ
from app.utils.helper.paginate import paginate
from app.core.db.repo.models import EStation, EItemStatusCode, DefectType, User, ItemSortField, EOrderBy
from app.core.db.repo.models import Item, ItemStatus, Review, ItemDefect, ItemImage, StatusChangeRequest, ProductionLine, ReviewStateEnum



router = APIRouter()

StationT = Union[str, EStation]
StatusListT = Optional[Sequence[Union[str, EItemStatusCode]]]


class ItemStatusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_item_statuses(
        self,
        *,
        include_inactive: bool = False,
        ids: Optional[Iterable[int]] = None,
        codes: Optional[Iterable[Union[str, EItemStatusCode]]] = None,
        search: Optional[str] = None,          
        order_by: str = "display_order",        
        direction: EOrderBy = EOrderBy.ASC,     
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[ItemStatus]:
        stmt = select(ItemStatus)

        if not include_inactive and hasattr(ItemStatus, "is_active"):
            stmt = stmt.where(ItemStatus.is_active == True)

        if ids:
            stmt = stmt.where(ItemStatus.id.in_(list(ids)))

        if codes:
            stmt = stmt.where(ItemStatus.code.in_([str(c) for c in codes]))

        if search:
            like_val = f"%{search}%"
            or_clauses = []
            if hasattr(ItemStatus, "code"):
                or_clauses.append(ItemStatus.code.ilike(like_val))
            if hasattr(ItemStatus, "name_th"):
                or_clauses.append(ItemStatus.name_th.ilike(like_val))
            if hasattr(ItemStatus, "name_en"):
                or_clauses.append(ItemStatus.name_en.ilike(like_val))
            if or_clauses:
                from sqlalchemy import or_
                stmt = stmt.where(or_(*or_clauses))

        col = getattr(ItemStatus, order_by, None)
        if col is None:
            col = ItemStatus.id
        stmt = stmt.order_by(asc(col) if direction == EOrderBy.ASC else desc(col))

        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        return (await self.db.execute(stmt)).scalars().all()