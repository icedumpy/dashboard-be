
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional, Sequence, Union, List
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
import logging
import csv
import asyncio

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.orm import aliased
from sqlalchemy import select, or_, text, func, case, and_, asc, desc, exists, literal, literal_column, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.config import settings
from app.domain.v1.item.schema import FixRequestBody, ItemEditIn, ItemAckOut, ItemEventOut, ActorOut, ItemReportRequest
from app.utils.helper.helper import current_shift_window, TZ, require_same_line
from app.utils.helper.paginate import paginate
from app.core.db.repo.models import EStation, EItemStatusCode, DefectType, User, ItemSortField, EOrderBy, ItemEvent
from app.core.db.repo.models import Item, ItemStatus, Review, ItemDefect, ItemImage, StatusChangeRequest, ProductionLine, ReviewStateEnum

router = APIRouter()

StationT = Union[str, EStation]
StatusListT = Optional[Sequence[Union[str, EItemStatusCode]]]

log = logging.getLogger(__name__)

def _as_float(v):
    return float(v) if v is not None else None

class ItemService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_items(
        self,
        *,
        page: int,
        page_size: int,
        sort_by=Optional[ItemSortField],
        order_by=Optional[EOrderBy],
        user_role: str,
        station: Optional[EStation],
        line_id: Optional[int],
        product_code: Optional[str],
        number: Optional[str],
        job_order_number: Optional[str],
        roll_width_min: Optional[float],
        roll_width_max: Optional[float],
        roll_id: Optional[str],
        status: Optional[List[EItemStatusCode]],
        detected_from: Optional[datetime],
        detected_to: Optional[datetime],
    ) -> dict:
        q = self._build_item_query(
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
            user_role=user_role,
        )

        q = self._add_bundle_roll_fallback(q)
        
        allowed_sort_fields = {
            ItemSortField[col.name]: getattr(Item, col.name)
            for col in Item.__table__.columns
            if col.name in ItemSortField.__members__
        }

        if sort_by:
            if sort_by == ItemSortField.status_code:
                col = ItemStatus.display_order
            else:
                col = allowed_sort_fields[sort_by]
            if order_by and order_by.lower() == EOrderBy.ASC:
                q = q.order_by(col.asc())
            else:
                q = q.order_by(col.desc())
        else:
            q = q.order_by(ItemStatus.display_order.asc(), Item.detected_at.desc(), Item.id.desc())
        

        rows, total = await paginate(self.db, q, page, page_size)
        data = [self._serialize_row(r) for r in rows]

        summary = await self._summarize_station(
            self.db,
            line_id=line_id,
            station=station,
            product_code=product_code,
            number=number,
            job_order_number=job_order_number,
            roll_width_min=roll_width_min,
            roll_width_max=roll_width_max,
            status=status,
            detected_from=detected_from,
            detected_to=detected_to,
        )

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

    async def get_item_detail(self, item_id: int) -> dict:
        it = await self.db.get(Item, item_id)
        if not it or it.deleted_at:
            raise HTTPException(status_code=404, detail="Item not found")

        st = (
            await self.db.execute(
                select(ItemStatus.code).where(ItemStatus.id == it.item_status_id)
            )
        ).scalar_one_or_none()

        defs = (
            await self.db.execute(
                select(DefectType.code, ItemDefect.meta)
                .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
                .where(ItemDefect.item_id == it.id)
            )
        ).all()

        imgs = (
            await self.db.execute(
                select(ItemImage.id, ItemImage.kind, ItemImage.path)
                .where(ItemImage.item_id == it.id)
                .order_by(ItemImage.uploaded_at.desc())
            )
        ).all()
        grouped = {"DETECTED": [], "FIX": [], "OTHER": []}
        for iid, kind, path in imgs:
            grouped.setdefault(kind, []).append({"id": iid, "path": path})

        rws = (
            await self.db.execute(
                select(Review)
                .where(Review.item_id == it.id)
                .order_by(Review.submitted_at.desc())
            )
        ).scalars().all()

        user_ids = {
            *(rv.submitted_by for rv in rws if rv.submitted_by is not None),
            *(rv.reviewed_by for rv in rws if rv.reviewed_by is not None),
        }

        user_map: dict[int, dict] = {}
        if user_ids:
            users = (
                await self.db.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
            user_map = {
                u.id: {
                    "id": u.id,
                    "username": getattr(u, "username", None),
                    "display_name": (
                        getattr(u, "display_name", None)
                        or getattr(u, "name", None)
                        or getattr(u, "full_name", None)
                    ),
                    "role": getattr(u, "role", None),
                }
                for u in users
            }
        
        is_pending_review = any(getattr(r, "state", None) == "PENDING" for r in rws)

        return {
            "data": {
                "id": it.id,
                "station": it.station,
                "line_id": it.line_id,
                "product_code": it.product_code,
                "roll_id": it.roll_id,
                "roll_number": it.roll_number,
                "bundle_number": it.bundle_number,
                "job_order_number": it.job_order_number,
                "roll_width": float(it.roll_width) if it.roll_width is not None else None,
                "detected_at": it.detected_at.isoformat(),
                "is_pending_review": is_pending_review,
                "status_code": st,
                "ai_note": it.ai_note,
                "acknowledged_by": it.acknowledged_by,
                "acknowledged_at": it.acknowledged_at.isoformat() if it.acknowledged_at else None,
                "current_review_id": it.current_review_id,
            },
            "defects": [{"defect_type_code": c, "meta": m} for c, m in defs],
            "images": grouped,
            "reviews": [
                {
                    "id": rv.id,
                    "review_type": rv.review_type,
                    "state": rv.state,
                    "submitted_by": rv.submitted_by,
                    "submitted_at": rv.submitted_at.isoformat(),
                    "submitted_by_user": user_map.get(rv.submitted_by),
                    "reviewed_by": rv.reviewed_by,
                    "reviewed_at": rv.reviewed_at.isoformat() if rv.reviewed_at else None,
                    "reviewed_by_user": user_map.get(rv.reviewed_by),
                    "submit_note": rv.submit_note,
                    "review_note": rv.review_note,
                    "reject_reason": rv.reject_reason,
                }
                for rv in rws
            ],
        }

    async def edit_item(self, item_id: int, payload: ItemEditIn) -> Item:
        stmt = select(Item).where(Item.id == item_id).with_for_update()
        item = (await self.db.execute(stmt)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        def _trim(val: Optional[str]) -> Optional[str]:
            return val.strip() if isinstance(val, str) else val

        if "product_code" in data:
            item.product_code = _trim(data["product_code"])
        if "roll_number" in data:
            item.roll_number = _trim(data["roll_number"])
        if "bundle_number" in data:
            item.bundle_number = _trim(data["bundle_number"])
        if "job_order_number" in data:
            item.job_order_number = _trim(data["job_order_number"])
        if "roll_id" in data:
            item.roll_id = _trim(data["roll_id"])

        if "roll_width" in data:
            if data["roll_width"] is None:
                item.roll_width = None
            else:
                try:
                    q = Decimal(str(data["roll_width"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid roll_width")
                if abs(q) >= Decimal("100000000"):
                    raise HTTPException(status_code=422, detail="roll_width out of range for Numeric(10,2)")
                item.roll_width = q

        try:
            await self.db.flush()
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(status_code=409, detail="Integrity error while updating item")

        await self.db.refresh(item)
        return item

    async def ack_item(self, item_id: int, user_id: int):
        stmt = select(Item).where(Item.id == item_id).with_for_update()
        item = (await self.db.execute(stmt)).scalar_one_or_none()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if item.acknowledged_at is not None and item.acknowledged_by is not None:
            return ItemAckOut(
                id=item.id,
                acknowledged_at=item.acknowledged_at,
                acknowledged_by=item.acknowledged_by,
                changed=False,
            )

        item.acknowledged_at = datetime.now(TZ)
        item.acknowledged_by = user_id

        await self.db.commit()
        await self.db.refresh(item)

        return ItemAckOut(
            id=item.id,
            acknowledged_at=item.acknowledged_at,
            acknowledged_by=item.acknowledged_by,
            changed=True,
        )

    async def get_item_history(self, item_id: int):
        FromS = aliased(ItemStatus)
        ToS = aliased(ItemStatus)

        q = (
            select(
                ItemEvent.id,
                ItemEvent.event_type,
                ItemEvent.actor_id,
                ItemEvent.details,
                ItemEvent.from_status_id,
                FromS.code.label("from_status_code"),
                ItemEvent.to_status_id,
                ToS.code.label("to_status_code"),
                ItemEvent.created_at,
                User.id.label("user_id"),
                User.username,
                User.display_name,
            )
            .outerjoin(FromS, FromS.id == ItemEvent.from_status_id)
            .outerjoin(ToS, ToS.id == ItemEvent.to_status_id)
            .outerjoin(User, User.id == ItemEvent.actor_id) 
            .where(
                ItemEvent.item_id == item_id,
                ItemEvent.deleted_at.is_(None),
            )
            .order_by(ItemEvent.created_at.desc(), ItemEvent.id.desc())
        )

        rows = (await self.db.execute(q)).all()
        
        rows = (await self.db.execute(q)).all()

        data: list[ItemEventOut] = []
        for r in rows:
            defects: list[str] = []

            if r.from_status_code == "DEFECT" or r.to_status_code == "DEFECT":
                result = await self.db.execute(
                    select(DefectType.name_th)
                    .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
                    .where(ItemDefect.item_id == item_id)
                )
                defects = result.unique().scalars().all()

            v = ItemEventOut(
                id=r.id,
                event_type=r.event_type,
                from_status_id=r.from_status_id,
                from_status_code=r.from_status_code,
                to_status_id=r.to_status_id,
                to_status_code=r.to_status_code,
                created_at=(
                    r.created_at.isoformat()
                    if hasattr(r.created_at, "isoformat")
                    else str(r.created_at)
                ),
                defects=defects,
                actor=ActorOut(
                    id=r.user_id,
                    username=r.username,
                    display_name=r.display_name,
                ),
            )
            data.append(v)

        return data

    async def submit_fix_request(self, item_id: int, body: FixRequestBody, user: User):
        it = await self.db.get(Item, item_id)
        if not it or it.deleted_at:
            raise HTTPException(status_code=404, detail="Item not found")
        require_same_line(user, it)

        is_pening_review = False

        if it.current_review_id != None:
            review_data = (await self.db.execute(select(Review).where(Review.id == it.current_review_id))).scalar()
            is_pening_review = review_data.state == "PENDING"
        
        if (is_pening_review == True):
            raise HTTPException(status_code=400, detail="The fix request has been submitted")

        st_code = (
            await self.db.execute(
                select(ItemStatus.code).where(ItemStatus.id == it.item_status_id)
            )
        ).scalar()
        if st_code not in ("DEFECT", "RECHECK", "REJECTED"):
            raise HTTPException(status_code=400, detail="Fix request allowed only for DEFECT or RECHECK")

        image_ids = list(getattr(body, "image_ids", []) or [])
        if not image_ids:
            raise HTTPException(status_code=400, detail="Provide at least 1 image_id")

        try:
            image_ids = list({int(i) for i in image_ids})
        except Exception:
            raise HTTPException(status_code=400, detail="image_ids must be integers")

        rows = await self.db.execute(
            select(ItemImage.id, ItemImage.review_id, ItemImage.item_id)
            .where(ItemImage.id.in_(image_ids))
        )
        rows = rows.all()

        found_ids = {r.id for r in rows}
        missing = [i for i in image_ids if i not in found_ids]
        if missing:
            raise HTTPException(status_code=400, detail={"message": "Some image_ids do not exist", "missing": missing})

        already_linked = [r.id for r in rows if r.review_id is not None]
        deleted = [r.id for r in rows if getattr(r, "deleted_at", None)]
        wrong_item = [r.id for r in rows if (getattr(r, "item_id", None) not in (None, item_id))]

        if already_linked or deleted or wrong_item:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Invalid images for fix request",
                    "already_linked": already_linked,
                    "deleted": deleted,
                    "wrong_item": wrong_item,
                },
            )

        note = getattr(body, "note", None)

        rv = Review(
            item_id=it.id,
            review_type="DEFECT_FIX",
            state="PENDING",
            submitted_by=user.id,
            submit_note=note,
        )
        self.db.add(rv)
        await self.db.flush()

        upd = await self.b.execute(
            text(
                """
                UPDATE qc.item_images
                SET review_id = :rid, kind = 'FIX'
                WHERE id = ANY(:ids)
                AND review_id IS NULL
                AND (item_id IS NULL OR item_id = :item_id)
                """
            ),
            {"rid": rv.id, "ids": image_ids, "item_id": item_id},
        )

        if upd.rowcount != len(image_ids):
            await self.db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Images changed concurrently; please retry"
            )

        it.current_review_id = rv.id

        await self.db.commit()
        return {"review_id": rv.id}

    async def list_item_images(self, item_id: int, kinds: str):
        it = await self.db.get(Item, item_id)
        if not it or getattr(it, "deleted_at", None):
            raise HTTPException(status_code=404, detail="Item not found")

        q = select(ItemImage).where(ItemImage.item_id == item_id)
        if kinds:
            kind_list = [k.strip().upper() for k in kinds.split(",") if k.strip()]
            q = q.where(ItemImage.kind.in_(kind_list))
        rows = (await self.db.execute(q.order_by(ItemImage.uploaded_at.asc(), ItemImage.id.asc()))).scalars().all()

        data = []
        image_dir = settings.IMAGES_DIR
        for im in rows:
            path = norm(im.path)
            data.append({
                "id": im.id,
                "kind": im.kind,
                "created_at": im.uploaded_at,
                "meta": im.meta,
                "url": f"/{image_dir}/{path}" if path else None,
            })
        return {"data": data}

    async def get_csv_item_report(self, body: ItemReportRequest, request: Request):
        line_code = await self.db.scalar(select(ProductionLine.code).where(ProductionLine.id == body.line_id))
        line_code = str(line_code or body.line_id)

        roll_match = aliased(Item)

        base_cols = [
            Item.id.label("item_id"),
            Item.station,
            Item.line_id,
            Item.product_code,
            Item.roll_id,
            Item.roll_number,
            Item.bundle_number,
            Item.job_order_number,
            Item.roll_width,
            Item.detected_at,
            Item.ai_note,
            ItemStatus.code.label("status_code"),
        ]

        if body.station == EStation.BUNDLE:
            base_cols += [
                roll_match.product_code.label("r_product_code"),
                roll_match.job_order_number.label("r_job_order_number"),
                roll_match.roll_width.label("r_roll_width"),
            ]
        else:
            base_cols += [
                literal(None).label("r_product_code"),
                literal(None).label("r_job_order_number"),
                literal(None).label("r_roll_width"),
            ]

        base = select(*base_cols).join(ItemStatus, ItemStatus.id == Item.item_status_id).where(
            Item.line_id == body.line_id,
            Item.station == body.station.value,
            Item.deleted_at.is_(None),
        )

        if body.station == EStation.BUNDLE:
            base = base.outerjoin(
                roll_match,
                and_(
                    roll_match.station == EStation.ROLL.value,
                    roll_match.line_id == Item.line_id,
                    roll_match.roll_number == Item.bundle_number,
                    roll_match.deleted_at.is_(None),
                ),
            )

        if body.product_code:
            like = f"%{body.product_code}%"
            if body.station == EStation.BUNDLE:
                base = base.where(or_(Item.product_code.ilike(like), roll_match.product_code.ilike(like)))
            else:
                base = base.where(Item.product_code.ilike(like))

        if body.number:
            like = f"%{body.number}%"
            base = base.where(or_(Item.roll_number.ilike(like), Item.bundle_number.ilike(like)))

        if body.job_order_number:
            like = f"%{body.job_order_number}%"
            if body.station == EStation.BUNDLE:
                base = base.where(or_(Item.job_order_number.ilike(like), roll_match.job_order_number.ilike(like)))
            else:
                base = base.where(Item.job_order_number.ilike(like))

        if body.roll_width_min is not None or body.roll_width_max is not None:
            width_expr = func.coalesce(Item.roll_width, roll_match.roll_width) if body.station == EStation.BUNDLE else Item.roll_width
            if body.roll_width_min is not None:
                base = base.where(width_expr >= body.roll_width_min)
            if body.roll_width_max is not None:
                base = base.where(width_expr <= body.roll_width_max)

        if body.status:
            base = base.where(ItemStatus.code.in_([s.value for s in body.status]))

        if body.detected_from:
            base = base.where(Item.detected_at >= body.detected_from)
        if body.detected_to:
            base = base.where(Item.detected_at <= body.detected_to)

        base_sq = base.subquery("base")

        defects_subq = (
            select(
                ItemDefect.item_id.label("item_id"),
                func.string_agg(func.distinct(DefectType.name_th), literal(", ")).label("defects_csv"),
            )
            .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
            .where(ItemDefect.item_id.in_(select(base_sq.c.item_id)))
            .group_by(ItemDefect.item_id)
            .subquery()
        )

        q = (
            select(
                base_sq.c.item_id,
                base_sq.c.station,
                base_sq.c.line_id,
                base_sq.c.product_code,
                base_sq.c.roll_id,
                base_sq.c.roll_number,
                base_sq.c.bundle_number,
                base_sq.c.job_order_number,
                base_sq.c.roll_width,
                base_sq.c.detected_at,
                base_sq.c.ai_note,
                base_sq.c.status_code,
                defects_subq.c.defects_csv,
                base_sq.c.r_product_code,
                base_sq.c.r_job_order_number,
                base_sq.c.r_roll_width,
            )
            .outerjoin(defects_subq, defects_subq.c.item_id == base_sq.c.item_id)
            .order_by(base_sq.c.detected_at.desc(), base_sq.c.item_id.desc())
        )

        header = [
            "PRODUCT CODE",
            "ROLL NUMBER" if body.station == EStation.ROLL else "BUNDLE NUMBER",
            "JOB ORDER NUMBER",
            "ROLL ID",
            "ROLL WIDTH",
            "TIMESTAMP",
            "STATUS",
        ]

        def row_to_list(m) -> list:
            product_code_val = m.get("product_code")
            job_order_val = m.get("job_order_number")
            width_val = m.get("roll_width")
            roll_id = m.get("roll_id")
            if body.station == EStation.BUNDLE:
                product_code_val = product_code_val or m.get("r_product_code")
                job_order_val = job_order_val or m.get("r_job_order_number")
                width_val = width_val if width_val is not None else m.get("r_roll_width")

            num_val = m.get("roll_number") if body.station == EStation.ROLL else m.get("bundle_number")
            status_str = status_label(m.get("status_code"), m.get("defects_csv"), None)
            dt = m.get("detected_at")
            readable_ts = dt.strftime("%d/%m/%Y %H:%M:%S") if dt else ""
            width_out = "" if width_val is None else str(width_val)
            return [product_code_val or "", num_val or "", job_order_val or "", roll_id, width_out, readable_ts, status_str]

        async def acsv_iter():
            buf = StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            THRESHOLD = 256 * 1024 

            try:
                writer.writerow(header)
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)

                result = await self.db.stream(q)
                try:
                    async for row in result.mappings():
                        if await request.is_disconnected():
                            return
                        writer.writerow(row_to_list(row))
                        if buf.tell() >= THRESHOLD:
                            yield buf.getvalue()
                            buf.seek(0); buf.truncate(0)
                finally:
                    await result.close()

                leftover = buf.getvalue()
                if leftover:
                    yield leftover

            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("CSV /report stream crashed")
                chunk = buf.getvalue()
                if chunk:
                    try:
                        yield chunk
                    except Exception:
                        pass
                return

        today = datetime.now().strftime("%Y%m%d")
        filename = f"items_{body.station.value.lower()}_line{line_code}_{today}.csv"

        return StreamingResponse(
            acsv_iter(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    def _apply_role_default_window(self, q, user_role: str):
        now = datetime.now(TZ)
        if user_role == "VIEWER":
            q = q.where(Item.detected_at >= now - timedelta(days=365))
        elif user_role == "OPERATOR":
            q = q.where(Item.detected_at >= now - timedelta(days=30))
        return q

    def _build_item_query(
        self,
        *,
        station: Optional[EStation],
        line_id: Optional[int],
        product_code: Optional[str],
        number: Optional[str],
        job_order_number: Optional[str],
        roll_width_min: Optional[float],
        roll_width_max: Optional[float],
        roll_id: Optional[str],
        status: Optional[List[EItemStatusCode]],        # list of enum codes
        detected_from: Optional[datetime],
        detected_to: Optional[datetime],
        user_role: str,
    ):
        """
        Build the base SELECT with lightweight filters. Avoids unnecessary joins so the
        planner can leverage (item_status_id, detected_at) and (line_id, station, detected_at).
        Heavy projections (lateral fallback, per-row counts) should be applied AFTER pagination.
        """
        q = (
            select(
                Item.id,
                Item.station,
                Item.line_id,
                Item.product_code,
                Item.roll_number,
                Item.bundle_number,
                Item.job_order_number,
                Item.roll_width,
                Item.roll_id,
                Item.detected_at,
                Item.acknowledged_by,
                Item.acknowledged_at,
                Item.current_review_id,

                # Join ItemStatus only to SELECT readable fields — not for filtering.
                ItemStatus.code.label("status_code"),
                ItemStatus.display_order.label("status_display_order"),

                # Keep these scalar subqueries if you need them here (but ideally move after pagination)
                select(func.count())
                    .select_from(ItemImage)
                    .where(ItemImage.item_id == Item.id)
                    .scalar_subquery()
                    .label("images_count"),

                select(func.array_remove(func.array_agg(func.distinct(DefectType.name_th)), None))
                    .select_from(ItemDefect)
                    .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
                    .where(ItemDefect.item_id == Item.id)
                    .scalar_subquery()
                    .label("defects_array"),

                exists(
                    select(1).where(and_(
                        Review.id == Item.current_review_id,
                        Review.state == "PENDING",
                    ))
                ).label("is_pending_review"),

                exists(
                    select(1).where(and_(
                        StatusChangeRequest.item_id == Item.id,
                        StatusChangeRequest.state == "PENDING",
                    ))
                ).label("is_changing_status_pending"),
            )
            .select_from(Item)
            .join(ItemStatus, Item.item_status_id == ItemStatus.id)   # select-friendly join
            .where(Item.deleted_at.is_(None))
        )

        # Simple, sargable filters
        if line_id is not None:
            q = q.where(Item.line_id == line_id)       # no join to ProductionLine
        if station is not None:
            q = q.where(Item.station == station)
        if product_code:
            q = q.where(Item.product_code.ilike(f"%{product_code}%"))
        if number:
            q = q.where(
                (Item.roll_number.ilike(f"%{number}%")) |
                (Item.bundle_number.ilike(f"%{number}%"))
            )
        if roll_id:
            q = q.where(Item.roll_id.ilike(f"%{roll_id}%"))
        if job_order_number:
            q = q.where(Item.job_order_number.ilike(f"%{job_order_number}%"))
        if roll_width_min is not None:
            q = q.where(Item.roll_width >= roll_width_min)
        if roll_width_max is not None:
            q = q.where(Item.roll_width <= roll_width_max)

        if status:
            codes = [s.value if hasattr(s, "value") else str(s) for s in status]
            status_ids_subq = select(ItemStatus.id).where(ItemStatus.code.in_(codes))
            q = q.where(Item.item_status_id.in_(status_ids_subq))

        if detected_from:
            q = q.where(Item.detected_at >= detected_from)
        if detected_to:
            q = q.where(Item.detected_at <= detected_to)

        if detected_from is None and detected_to is None:
            q = self._apply_role_default_window(q, user_role)

        return q

    def _add_bundle_roll_fallback(self, q):
        ri = aliased(Item, name="ri")

        roll_lat = (
            select(
                ri.product_code.label("r_product_code"),
                ri.job_order_number.label("r_job_order_number"),
                ri.roll_width.label("r_roll_width"),
            )
            .where(
                ri.station == EStation.ROLL,
                ri.roll_number == Item.bundle_number,  
                ri.line_id == Item.line_id,            
                ri.deleted_at.is_(None),
            )
            .order_by(ri.detected_at.desc(), ri.id.desc())
            .limit(1)
            .correlate(Item)
            .lateral()
        )

        q = q.join(roll_lat, true(), isouter=True)

        prod_eff = case(
            (Item.station == EStation.BUNDLE, roll_lat.c.r_product_code),
            else_=Item.product_code,
        ).label("eff_product_code")

        jo_eff = case(
            (Item.station == EStation.BUNDLE, roll_lat.c.r_job_order_number),
            else_=Item.job_order_number,
        ).label("eff_job_order_number")

        width_eff = case(
            (Item.station == EStation.BUNDLE, roll_lat.c.r_roll_width),
            else_=Item.roll_width,
        ).label("eff_roll_width")

        q = q.add_columns(prod_eff, jo_eff, width_eff)
        return q
    
    def _build_item_filters(
        self,
        *,
        line_id: Optional[int] = None,
        station: StationT | None = None,
        product_code: Optional[str] = None,
        number: Optional[str] = None,
        job_order_number: Optional[str] = None,
        roll_width_min: Optional[float] = None,
        roll_width_max: Optional[float] = None,
        status: StatusListT = None,
        detected_from: Optional[datetime] = None,
        detected_to: Optional[datetime] = None,
    ) -> list[BinaryExpression]:
        clauses: list[BinaryExpression] = [Item.deleted_at.is_(None)]

        if line_id is not None:
            clauses.append(Item.line_id == line_id)

        if station is not None:
            st = station.value if hasattr(station, "value") else station
            clauses.append(Item.station == st)

        if product_code:
            clauses.append(Item.product_code.ilike(f"%{product_code}%"))

        if number:
            like = f"%{number}%"
            clauses.append(or_(Item.roll_number.ilike(like), Item.bundle_number.ilike(like)))

        if job_order_number:
            clauses.append(Item.job_order_number.ilike(f"%{job_order_number}%"))

        if roll_width_min is not None:
            clauses.append(Item.roll_width >= roll_width_min)
        if roll_width_max is not None:
            clauses.append(Item.roll_width <= roll_width_max)

        if status:
            vals = [(s.value if hasattr(s, "value") else s) for s in status]
            clauses.append(ItemStatus.code.in_(vals))

        if detected_from:
            clauses.append(Item.detected_at >= detected_from)
        if detected_to:
            clauses.append(Item.detected_at <= detected_to)

        return clauses
    
    def _serialize_row(self, r) -> dict:
        return {
            "id": r.id,
            "station": r.station,
            "line_id": r.line_id,
            "product_code": r.product_code,
            "roll_number": r.roll_number,
            "bundle_number": r.bundle_number,
            "job_order_number": r.job_order_number,
            "roll_width": _as_float(r.roll_width),
            "roll_id": r.roll_id,
            "detected_at": r.detected_at.isoformat(),
            "status_code": r.status_code,
            "acknowledged_by": r.acknowledged_by,
            "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
            "current_review_id": r.current_review_id,
            "is_pending_review": bool(r.is_pending_review),
            "is_changing_status_pending": bool(r.is_changing_status_pending),
            "images": int(r.images_count or 0),
            "defects": list(r.defects_array or []),
        }
        
    async def _summarize_station(
        self,
        *,
        line_id: Optional[int] = None,
        station: Optional[EStation | str] = None,
        product_code: Optional[str] = None,
        number: Optional[str] = None,
        job_order_number: Optional[str] = None,
        roll_width_min: Optional[float] = None,
        roll_width_max: Optional[float] = None,
        status: Optional[Sequence[EItemStatusCode | str]] = None,
        detected_from: Optional[datetime] = None,
        detected_to: Optional[datetime] = None,
    ) -> dict:
        pending_exists = (
            select(Review.id)
            .where(Review.item_id == Item.id, Review.state == "PENDING")
            .exists()
        )
        

        where_clauses = self._build_item_filters(
            line_id=line_id,
            station=station,
            product_code=product_code,
            number=number,
            job_order_number=job_order_number,
            roll_width_min=roll_width_min,
            roll_width_max=roll_width_max,
            status=status,
            detected_from=detected_from,
            detected_to=detected_to,
        )
        
        
        if detected_from is None and detected_to is None:
            shift_start, shift_end = current_shift_window()
            where_clauses.append(Item.created_at >= shift_start)
            where_clauses.append(Item.created_at <= shift_end)

        q = (
            select(
                func.count().label("total"),
                func.sum(case((ItemStatus.code == "NORMAL", 1), else_=0)).label("normal"),
                func.sum(case((ItemStatus.code == "QC_PASSED", 1), else_=0)).label("qc_passed"),
                func.sum(case((ItemStatus.code == "REJECTED", 1), else_=0)).label("rejected"),
                func.sum(case((ItemStatus.code == "SCRAP", 1), else_=0)).label("scrap"),
                func.sum(case((ItemStatus.code == "DEFECT", 1), else_=0)).label("defect"),
                func.sum(case((and_(ItemStatus.code.in_(("DEFECT", "REJECTED")), pending_exists), 1), else_=0)).label("pending_defect"),
            )
            .select_from(Item)
            .join(ItemStatus, ItemStatus.id == Item.item_status_id)
            .where(*where_clauses)
        )

        row = (await self.db.execute(q)).first() or (0, 0, 0, 0, 0)
        total, normal, qc_passed, rejected, scrap, defect, pending_defect = row
        return {
            "total": total or 0,
            "normal": normal or 0,
            "qc_passed": qc_passed or 0,
            "rejected": rejected or 0,
            "scrap": scrap or 0,
            "defect": defect or 0,
            "pending_defect": pending_defect or 0,
        }


def norm(rel: Optional[str]) -> Optional[str]:
    if not rel: return None
    p = PurePosixPath(rel).as_posix().lstrip("/")
    if ".." in p:
        raise HTTPException(status_code=400, detail="Invalid image path")
    return p

def status_label(code: str, defects_csv: Optional[str], ai_note: Optional[str]) -> str:
    if code == "DEFECT":
        return f"Defect{': ' + defects_csv if defects_csv else ''}"
    if code == "SCRAP":
        return f"Scrap{(' (' + ai_note + ')') if ai_note else ''}"
    if code == "QC_PASSED":
        return "QC Passed"
    if code == "NORMAL":
        return "Normal"
    if code == "RECHECK":
        return "Recheck"
    if code == "REJECTED":
        return "Rejected"
    return code or ""

