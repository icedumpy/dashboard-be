from typing import List, Optional, Literal, Annotated, Tuple
import math

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert, delete, text, and_, literal
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.sql import func

from app.core.db.repo.models import StatusChangeRequest, StatusChangeRequestDefect, ItemEvent, Item, ItemDefect, DefectType, ItemStatus, StatusChangeSortField, EOrderBy
from app.domain.v1.change_status.schema import StatusChangeRequestOut, DecisionRequestBody, StatusChangeRequestCreate, ListResponseOut, SummaryOut, PaginationOut, ListResponseOut

class ChangeStatusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _pagination(page: int, page_size: int) -> Tuple[int, int]:
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size
        return page_size, offset

    async def list_requests(
        self,
        *,
        page: int,
        page_size: int,
        line_id: Optional[int],
        station: Optional[str], 
        sort_by: Optional["StatusChangeSortField"],
        order_by: Optional["EOrderBy"],
    ) -> "ListResponseOut":
        where_clauses = [StatusChangeRequest.state == "PENDING"]
        if line_id is not None:
            where_clauses.append(Item.line_id == line_id)
        if station is not None:
            where_clauses.append(Item.station == station)

        page_size, offset = self._pagination(page, page_size)

        FromStatus = aliased(ItemStatus)
        ToStatus   = aliased(ItemStatus)

        from_status_key = func.coalesce(FromStatus.display_order, literal(999_999))
        to_status_key   = func.coalesce(ToStatus.display_order,   literal(999_999))

        s_base = (
            select(
                StatusChangeRequest.id.label("rid"),
                StatusChangeRequest.requested_at.label("requested_at"),

                Item.line_id.label("i_line_id"),
                Item.station.label("i_station"),
                Item.product_code.label("i_product_code"),
                func.coalesce(Item.roll_number, Item.bundle_number).label("i_number"),
                Item.job_order_number.label("i_job_order_number"),

                from_status_key.label("from_status_order"),
                func.coalesce(FromStatus.code, literal("")).label("from_status_code"),
                to_status_key.label("to_status_order"),
                func.coalesce(ToStatus.code, literal("")).label("to_status_code"),
            )
            .join(Item, Item.id == StatusChangeRequest.item_id)
            .join(FromStatus, FromStatus.id == StatusChangeRequest.from_status_id)
            .join(ToStatus, ToStatus.id == StatusChangeRequest.to_status_id)
        )
        if where_clauses:
            s_base = s_base.where(and_(*where_clauses))

        s = s_base.subquery("s")

        total = await self.db.scalar(select(func.count()).select_from(s)) or 0
        total_pages = math.ceil(total / page_size) if page_size else 0

        ALLOWED_SORT = {
            StatusChangeSortField.production_line: s.c.i_line_id,
            StatusChangeSortField.station:         s.c.i_station,
            StatusChangeSortField.product_code:    s.c.i_product_code,
            StatusChangeSortField.number:          s.c.i_number,
            StatusChangeSortField.job_order_number:s.c.i_job_order_number,
            StatusChangeSortField.status_before:   (s.c.from_status_order, s.c.from_status_code),
            StatusChangeSortField.status_after:    (s.c.to_status_order,   s.c.to_status_code),
            StatusChangeSortField.requested_at:    s.c.requested_at,
        }

        def tiebreakers():
            return (s.c.requested_at.desc(), s.c.rid.desc())

        ids_q = select(s.c.rid)
        key = ALLOWED_SORT.get(sort_by)
        if key is None:
            ids_q = ids_q.order_by(*tiebreakers())
        else:
            cols = key if isinstance(key, tuple) else (key,)
            if order_by == EOrderBy.ASC:
                ids_q = ids_q.order_by(*[c.asc().nulls_last() for c in cols], *tiebreakers())
            else:
                ids_q = ids_q.order_by(*[c.desc().nulls_last() for c in cols], *tiebreakers())

        ids_q = ids_q.offset(offset).limit(page_size)
        req_ids = [r[0] for r in (await self.db.execute(ids_q)).all()]

        data: List["StatusChangeRequestOut"] = []
        if req_ids:
            pos = {rid: i for i, rid in enumerate(req_ids)}
            list_q = (
                select(StatusChangeRequest)
                .options(selectinload(StatusChangeRequest.defects))
                .where(StatusChangeRequest.id.in_(req_ids))
            )
            rows = (await self.db.execute(list_q)).scalars().all()
            rows.sort(key=lambda r: pos[r.id])

            data = [
                StatusChangeRequestOut(
                    id=r.id,
                    item_id=r.item_id,
                    from_status_id=r.from_status_id,
                    to_status_id=r.to_status_id,
                    state=r.state,
                    requested_by=r.requested_by,
                    requested_at=r.requested_at.isoformat()
                        if hasattr(r.requested_at, "isoformat") else str(r.requested_at),
                    approved_by=r.approved_by,
                    approved_at=r.approved_at.isoformat()
                        if r.approved_at and hasattr(r.approved_at, "isoformat")
                        else (str(r.approved_at) if r.approved_at else None),
                    reason=r.reason,
                    meta=r.meta,
                    defect_type_ids=[d.defect_type_id for d in (r.defects or [])],
                )
                for r in rows
            ]

        summary_q = (
            select(Item.station, func.count(StatusChangeRequest.id))
            .select_from(StatusChangeRequest)
            .join(Item, Item.id == StatusChangeRequest.item_id)
        )
        if where_clauses:
            summary_q = summary_q.where(and_(*where_clauses))
        summary_q = summary_q.group_by(Item.station)

        station_counts = (await self.db.execute(summary_q)).all()
        by_station = {k: v for k, v in station_counts}
        roll_cnt = int(by_station.get("ROLL", 0))
        bundle_cnt = int(by_station.get("BUNDLE", 0))

        return ListResponseOut(
            data=data,
            summary=SummaryOut(
                roll=roll_cnt,
                bundle=bundle_cnt,
                total=int(total),
            ),
            pagination=PaginationOut(
                page=page,
                page_size=page_size,
                total=int(total),
                total_pages=total_pages,
            ),
        )