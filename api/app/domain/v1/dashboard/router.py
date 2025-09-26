# app/routes/dashboard_router.py
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta

from app.domain.v1.dashboard.service import DashboardService, SummaryParams
from app.core.db.session import get_db
from app.core.security.auth import get_current_user
from app.utils.helper.helper import (
    require_role,
    TZ
)

router = APIRouter()

def get_service(db: AsyncSession = Depends(get_db)) -> DashboardService:
    return DashboardService(db)

@router.get("/summary")
async def get_dashboard_summary(
    line_id: int = Query(..., description="production line id"),
    station: Literal["ROLL", "BUNDLE"] = Query(..., description="station type"),
    date_from: Optional[date] | None = Query(None, description="YYYY-MM-DD (local day in TZ)"),
    date_to: Optional[date] | None = Query(None, description="YYYY-MM-DD (local day in TZ)"),
    user = Depends(get_current_user),
    svc: DashboardService = Depends(get_service),
):
    require_role(user, ["VIEWER", "INSPECTOR"])

    today_local = datetime.now(TZ).date()
    df = date_from or (today_local - timedelta(days=30))
    dt = date_to or today_local

    params = SummaryParams(line_id=line_id, station=station, date_from=df, date_to=dt)
    return await svc.get_summary(params)
