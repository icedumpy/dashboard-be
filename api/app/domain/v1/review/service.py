# services/review_service.py
from __future__ import annotations
from typing import Optional, Iterable, Sequence, Dict, Any, List, Tuple
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.repo.models import (
    ItemStatus, Review, ItemEvent, Item, ItemDefect, DefectType,EReviewState,
    ReviewSortField, EOrderBy, User
)

class ReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- tiny helpers ----------
    @staticmethod
    def _page_window(page: int, page_size: int) -> Tuple[int, int]:
        page_size = max(1, min(page_size, 100))
        return page_size, (page - 1) * page_size

    @staticmethod
    def _window_rn(order_cols: Sequence):
        return func.row_number().over(
            partition_by=Review.item_id, order_by=order_cols
        ).label("rn")

    @staticmethod
    def _default_tiebreakers(s):
        return (s.c.r_updated_at.desc(), s.c.i_detected_at.desc(), s.c.item_pk.desc())

    @staticmethod
    def _apply_common_filters(q,
          *,
          line_id: Optional[int],
          defect_type_id: Optional[int],
          review_state: Optional[Iterable[EReviewState]],
          reviewed_at_from: Optional[datetime],
          reviewed_at_to: Optional[datetime],
          submitted_at_from: Optional[datetime],
          submitted_at_to: Optional[datetime]):
        if line_id:
            q = q.where(Item.line_id == line_id)
        if defect_type_id:
            q = q.join(ItemDefect, ItemDefect.item_id == Item.id).where(ItemDefect.defect_type_id == defect_type_id)
        if review_state:
            q = q.where(Review.state.in_(review_state))
        if reviewed_at_from:
            q = q.where(Review.reviewed_at >= reviewed_at_from)
        if reviewed_at_to:
            q = q.where(Review.reviewed_at <= reviewed_at_to)
        if submitted_at_from:
            q = q.where(Review.created_at >= submitted_at_from)
        if submitted_at_to:
            q = q.where(Review.created_at <= submitted_at_to)
        return q

    # ---------- public API ----------
    async def list_reviews(
        self,
        *,
        page: int,
        page_size: int,
        sort_by: Optional[ReviewSortField],
        order_by: Optional[EOrderBy],
        line_id: Optional[int],
        review_state: Optional[Iterable[EReviewState]],
        defect_type_id: Optional[int],
        reviewed_at_from: Optional[datetime],
        reviewed_at_to: Optional[datetime],
        submitted_at_from: Optional[datetime],
        submitted_at_to: Optional[datetime],
    ) -> Dict[str, Any]:

        page_size, offset = self._page_window(page, page_size)

        rn = self._window_rn(order_cols=(Review.updated_at.desc(), Review.id.desc()))
        base = (
            select(
              Review.id.label("rid"),
              Review.item_id.label("iid"),
              Review.state.label("r_state"),
              Review.reviewed_by.label("r_reviewed_by"),
              Review.reviewed_at.label("r_reviewed_at"),
              Review.updated_at.label("r_updated_at"),
              Review.created_at.label("r_submitted_at"),
              func.coalesce(Review.review_note, Review.reject_reason).label("r_decision"),

              Item.detected_at.label("i_detected_at"),
              Item.line_id.label("i_line_id"),
              Item.station.label("i_station"),
              Item.product_code.label("i_product_code"),
              Item.job_order_number.label("i_job_order_number"),
              func.coalesce(Item.roll_number, Item.bundle_number).label("i_number"),

              Item.id.label("item_pk"),
              rn,
          )
          .join(Item, Item.id == Review.item_id)
          .join(ItemStatus, Item.item_status_id == ItemStatus.id)
        )
        base = self._apply_common_filters(
            base,
            line_id=line_id,
            defect_type_id=defect_type_id,
            review_state=review_state,
            reviewed_at_from=reviewed_at_from,
            reviewed_at_to=reviewed_at_to,
            submitted_at_from=submitted_at_from,
            submitted_at_to=submitted_at_to,
        )
        s = base.subquery("s")

        total = await self.db.scalar(
            select(func.count()).select_from(select(s.c.rid).where(s.c.rn == 1).subquery())
        ) or 0

        ALLOWED_SORT = {
          ReviewSortField.production_line: s.c.i_line_id,
          ReviewSortField.station:         s.c.i_station,
          ReviewSortField.product_code:    s.c.i_product_code,
          ReviewSortField.number:          s.c.i_number,
          ReviewSortField.job_order:       s.c.i_job_order_number,
          ReviewSortField.state:           s.c.r_state,
          ReviewSortField.decision:        s.c.r_decision,
          ReviewSortField.reviewed_by:     s.c.r_reviewed_by,
          ReviewSortField.reviewed_at:     s.c.r_reviewed_at,
        }


        ids_q = select(s.c.rid).where(s.c.rn == 1)
        sort_col = ALLOWED_SORT.get(sort_by)
        if sort_col is not None:
            if order_by == EOrderBy.ASC:
                ids_q = ids_q.order_by(sort_col.asc().nulls_last(), *self._default_tiebreakers(s))
            else:
                ids_q = ids_q.order_by(sort_col.desc().nulls_last(), *self._default_tiebreakers(s))
        else:
            ids_q = ids_q.order_by(*self._default_tiebreakers(s))
        ids_q = ids_q.offset(offset).limit(page_size)
        
        review_ids = [row[0] for row in (await self.db.execute(ids_q)).all()]

        rn2 = self._window_rn(order_cols=(Review.updated_at.desc(), Review.id.desc()))
        sum_base = select(Review.state.label("state"), rn2).join(Item, Item.id == Review.item_id)
        sum_base = self._apply_common_filters(
            sum_base,
            line_id=line_id,
            defect_type_id=defect_type_id,
            review_state=review_state,
            reviewed_at_from=reviewed_at_from,
            reviewed_at_to=reviewed_at_to,
            submitted_at_from=submitted_at_from,
            submitted_at_to=submitted_at_to,
        )
        sb = sum_base.subquery("sb")
        sum_rows = (await self.db.execute(
            select(sb.c.state, func.count().label("cnt"))
            .where(sb.c.rn == 1)
            .group_by(sb.c.state)
        )).all()
        sum_map = {row.state: int(row.cnt) for row in sum_rows}
        summary = {
            "pending":  sum_map.get("PENDING", 0),
            "approved": sum_map.get("APPROVED", 0),
            "rejected": sum_map.get("REJECTED", 0),
            "total":    sum(sum_map.values()),
        }

        if not review_ids:
            return {
                "data": [],
                "summary": summary,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "total_pages": 0,
                },
            }

        reviews = (await self.db.execute(select(Review).where(Review.id.in_(review_ids)))).scalars().all()
        id_pos = {rid: i for i, rid in enumerate(review_ids)}
        reviews.sort(key=lambda rv: id_pos[rv.id])

        item_ids = {rv.item_id for rv in reviews}
        items_rows = await self.db.execute(
            select(
                Item.id, Item.station, Item.line_id, Item.product_code, Item.roll_number, Item.roll_id,
                Item.bundle_number, Item.job_order_number, Item.roll_width, Item.detected_at,
                Item.item_status_id, Item.ai_note
            ).where(Item.id.in_(item_ids))
        )
        items = {r.id: r for r in items_rows.all()}

        status_ids = {getattr(items[iid], "item_status_id") for iid in item_ids if iid in items}
        status_rows = await self.db.execute(
            select(ItemStatus.id, ItemStatus.code, ItemStatus.name_th, ItemStatus.display_order)
            .where(ItemStatus.id.in_(status_ids))
        )
        statuses = {r.id: r for r in status_rows.all()}

        defects_rows = await self.db.execute(
            select(
                ItemDefect.item_id,
                ItemDefect.id,
                ItemDefect.defect_type_id,
                DefectType.code,
                DefectType.name_th,
                ItemDefect.meta
            )
            .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
            .where(ItemDefect.item_id.in_(item_ids))
        )
        defects_by_item: Dict[int, List[dict]] = {}
        for row in defects_rows.all():
            defects_by_item.setdefault(row.item_id, []).append({
                "id": row.id,
                "defect_type_id": row.defect_type_id,
                "defect_type_code": row.code,
                "defect_type_name": row.name_th,
                "meta": row.meta,
            })

        data = []
        for rv in reviews:
            it = items.get(rv.item_id)
            if not it:
                continue
            st = statuses.get(it.item_status_id)
            number = it.roll_number or it.bundle_number
            decision_note = getattr(rv, "review_note", None) or getattr(rv, "reject_reason", None)

            data.append({
                "id": rv.id,
                "type": rv.review_type,
                "state": rv.state,
                "submitted_by": rv.submitted_by,
                "submitted_at": getattr(rv, "created_at", None),
                "submit_note": getattr(rv, "submit_note", None),
                "reviewed_by": getattr(rv, "reviewed_by", None),
                "reviewed_at": getattr(rv, "reviewed_at", None),
                "decision_note": decision_note,
                "item": {
                    "id": it.id,
                    "station": it.station,
                    "line_id": it.line_id,
                    "product_code": it.product_code,
                    "number": number,
                    "roll_id": it.roll_id,
                    "job_order_number": it.job_order_number,
                    "roll_width": it.roll_width,
                    "detected_at": it.detected_at,
                    "ai_note": it.ai_note,
                    "status": {
                        "id": it.item_status_id,
                        "code": getattr(st, "code", None),
                        "name": getattr(st, "name_th", None),
                        "display_order": getattr(st, "display_order", None),
                    },
                },
                "defects": defects_by_item.get(it.id, []),
            })

        return {
            "data": data,
            "summary": summary,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }
