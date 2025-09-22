from typing import List, Tuple
from sqlalchemy import func, select
from sqlalchemy.sql import Select
from sqlalchemy.ext.asyncio import AsyncSession

async def paginate(db: AsyncSession, query: Select, page: int, page_size: int) -> Tuple[List, int]:
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    count_q = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(count_q)).scalar_one() or 0

    rows = (await db.execute(query.offset(offset).limit(page_size))).all()
    return rows, total