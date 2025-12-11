from __future__ import annotations
import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.core.db.repo.models import Item, ItemStatus
from datetime import datetime, timedelta
from typing import Iterable, Optional, Dict

from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from app.utils.helper.helper import TZ
from app.domain.v1.item.service import StationT
from app.core.db.repo.models import User  # your User ORM

def _json(payload):
    if not isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, list):
        return payload
    if "data" in payload and isinstance(payload["data"], dict):
        return payload["data"].get("attributes", payload["data"])
    return payload

def _json_list(payload):
    """
    Support both plain list and JSON:API list.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [it.get("attributes", it) for it in payload["data"]]
    return payload

def make_items(
    db: Session,
    *,
    station: StationT,                # REQUIRED (enum or str)
    count: int = 3,
    line_id: int = 3,
    status_id: Optional[int] = None,
    start_at: Optional[datetime] = None,
    step_minutes: int = 10,
    extra_overrides: Optional[Iterable[dict]] = None,
) -> list[int]:
    """
    Create N rows in qc.items spaced by `step_minutes` in detected_at.
    Returns list of created item IDs (ascending by insertion order).

    NOTE:
    - `station` is required by your model (qc.station enum).
    - `detected_at` is timezone-aware (UTC).
    - You can override any field per row via `extra_overrides` (list[dict]).
    """
    # tz-aware start time (UTC)
    start = start_at or datetime.now(TZ) - timedelta(minutes=count * step_minutes)

    rows: list[dict] = []
    overrides_list = list(extra_overrides) if extra_overrides is not None else None

    for i in range(count):
        payload = {
            "station": station,
            "line_id": line_id,
            "product_code": f"PROD-{line_id}-{i}",
            "roll_number": f"R{line_id}{i:04d}",
            "bundle_number": None,
            "job_order_number": "JOB-001",
            "roll_width": 1250.0,  # Numeric(10,2) is fine with float
            "roll_id": None,
            "detected_at": start + timedelta(minutes=i * step_minutes),
            "item_status_id": status_id,   # must be valid FK (ensure via ensure_statuses)
            "ai_note": None,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "current_review_id": None,
            "deleted_at": None,
        }

        if overrides_list and i < len(overrides_list) and isinstance(overrides_list[i], dict):
            payload.update(overrides_list[i])

        rows.append(payload)

    # Use RETURNING to get ids in one round-trip (preserves insertion order)
    result = db.execute(insert(Item).values(rows).returning(Item.id))
    ids = [row[0] for row in result.fetchall()]
    db.flush()  # make visible to the same transaction/session

    return ids

def ensure_statuses(db: Session) -> dict[str, int]:
    """
    Idempotent seed for lookup statuses.
    Returns mapping: {"DEFECT": id, "QC_PASSED": id}
    """
    rows = db.execute(select(ItemStatus)).scalars().all()
    return {r.code: r.id for r in rows}

async def login_as(
    async_client,
    db: Session,
    *,
    username: str,
    password: Optional[str] = None,
    **extra_user_fields,
) -> Dict[str, str]:
    """Ensure user exists (with role), then log in via API and return Bearer headers."""
    db.flush()

    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"login failed for {username}: {resp.text}"
    body = resp.json()
    token = body.get("access_token") or body.get("data", {}).get("access_token")
    assert token, f"no access_token in response: {body}"
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_items_list_as_inspector(async_client, db_session: Session):
    headers = await login_as(async_client, db_session,
                             username="OPTH03A",
                             password="133Abc###")

    statuses = ensure_statuses(db_session)
    make_items(db_session, station="ROLL", line_id=1, status_id=statuses["DEFECT"])
    make_items(db_session, station="ROLL", line_id=1, status_id=statuses["QC_PASSED"])
    db_session.flush()

    resp = await async_client.get("/api/v1/items", headers=headers)
    assert resp.status_code == 200, resp.text