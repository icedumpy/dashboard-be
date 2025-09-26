from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Tuple, Optional

from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, cast, Date, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.repo.models import Item, ItemStatus, ItemDefect, DefectType
from app.utils.helper.helper import TZ

COMPLETED_STATUS_CODES = {"NORMAL", "QC_PASSED", "SCRAP", "DEFECT"}
PENDING_STATUS_CODES   = {"PENDING"}
STATUS_ORDER = ["NORMAL", "QC_PASSED", "DEFECT", "SCRAP", "REJECTED"]


@dataclass(frozen=True)
class SummaryParams:
    line_id: int
    station: str
    date_from: Optional[date]
    date_to: Optional[date]

def _guard_params(p: SummaryParams) -> None:
    if p.date_from > p.date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "date_from must be <= date_to")
    if (p.date_to - p.date_from).days > 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "date range must be <= 30 days")
    if p.station not in ("ROLL", "BUNDLE"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "station must be ROLL or BUNDLE")

def _local_range_to_utc(date_from: date, date_to: date) -> Tuple[datetime, datetime]:
    start_local = datetime.combine(date_from, time.min, TZ)
    end_local_excl = datetime.combine(date_to + timedelta(days=1), time.min, TZ)
    return start_local.astimezone(timezone.utc), end_local_excl.astimezone(timezone.utc)

class DashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_summary(self, params: SummaryParams) -> Dict:
        _guard_params(params)
        start_utc, end_utc = _local_range_to_utc(params.date_from, params.date_to)

        where_base = and_(
            Item.line_id == params.line_id,
            Item.station == params.station,
            Item.detected_at >= start_utc,
            Item.detected_at <  end_utc,
        )

        q_totals = (
            select(ItemStatus.code, func.count())
            .select_from(Item)
            .join(ItemStatus, ItemStatus.id == Item.item_status_id)
            .where(where_base)
            .group_by(ItemStatus.code)
        )
        rows_totals = (await self.db.execute(q_totals)).all()
        status_totals: Dict[str, int] = {code: int(cnt) for code, cnt in rows_totals}

        total_items = sum(status_totals.values())
        inspected_items = sum(status_totals.get(c, 0) for c in COMPLETED_STATUS_CODES)
        pending_items = sum(status_totals.get(c, 0) for c in PENDING_STATUS_CODES)

        present_codes: list[str] = []
        seen = set()
        for c in STATUS_ORDER:
            present_codes.append(c); seen.add(c)
        for c in sorted(status_totals.keys()):
            if c not in seen:
                present_codes.append(c); seen.add(c)

        day_local = cast(Item.detected_at.op("AT TIME ZONE")(str(TZ.key)), Date).label("d")
        q_daily = (
            select(day_local, ItemStatus.code, func.count())
            .select_from(Item)
            .join(ItemStatus, ItemStatus.id == Item.item_status_id)
            .where(where_base)
            .group_by(day_local, ItemStatus.code)
            .order_by(day_local.asc())
        )
        rows_daily = (await self.db.execute(q_daily)).all()

        labels = [(params.date_from + timedelta(days=i)).isoformat()
                for i in range((params.date_to - params.date_from).days + 1)]

        series_map: Dict[str, list[int]] = {c: [0]*len(labels) for c in present_codes}
        idx = {lbl: i for i, lbl in enumerate(labels)}
        for d_local, code, cnt in rows_daily:
            i = idx.get(str(d_local))
            if i is not None and code in series_map:
                series_map[code][i] += int(cnt)

        daily_stacked = {
            "labels": labels,
            "series": [{"status_code": c, "data": series_map[c]} for c in present_codes],
        }

        bar_completion = {
            "completed": inspected_items,
            "in_progress": max(total_items - inspected_items, 0),
        }

        cnt = func.count().label("cnt")
        q_pie = (
            select(
                DefectType.id.label("defect_type_id"),
                DefectType.code,
                DefectType.name_th,
                cnt,
            )
            .select_from(Item)
            .join(ItemStatus, ItemStatus.id == Item.item_status_id)
            .join(ItemDefect, ItemDefect.item_id == Item.id)
            .join(DefectType, DefectType.id == ItemDefect.defect_type_id)
            .where(where_base, ItemStatus.code == "DEFECT")
            .group_by(DefectType.id, DefectType.code, DefectType.name_th)
            .order_by(cnt.desc(), DefectType.code.asc()) 
        )
        pie_rows = (await self.db.execute(q_pie)).all()
        total_defects = sum(int(r.cnt) for r in pie_rows) if pie_rows else 0
        defect_pie = {
            "total": total_defects,
            "by_type": [
                {
                    "defect_type_id": int(r.defect_type_id),
                    "code": r.code,
                    "name_th": r.name_th,
                    "count": int(r.cnt),
                    "pct": (round(100.0 * int(r.cnt) / total_defects, 2) if total_defects else 0.0),
                }
                for r in pie_rows
            ],
        }

        return {
            "meta": {
                "line_id": params.line_id,
                "station": params.station,
                "tz": TZ.key,
                "date_from": params.date_from.isoformat(),
                "date_to": params.date_to.isoformat(),
            },
            "cards": {
                "total_items": total_items,
                "inspected_items": inspected_items,
                "pending_items": pending_items,
            },
            "status_totals": [
                {"status_code": c, "count": int(status_totals.get(c, 0))} for c in present_codes
            ],
            "daily_stacked": daily_stacked,
            "bar_completion": bar_completion,
            "defect_pie": defect_pie,
        }