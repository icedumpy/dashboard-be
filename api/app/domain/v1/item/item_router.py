# app/domain/v1/items_router.py
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
    Item, ItemStatus, ProductionLine, ItemDefect, DefectType,
    Review, ItemImage, ItemEvent
)
from fastapi import Query, Request, Depends
from app.domain.v1.item.item_schema import FixRequestBody, DecisionRequestBody

router = APIRouter()

# ---------- helpers ----------
def require_role(user: User, allowed: List[str]):
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden")

def require_same_line(user: User, item: Item):
    if user.line_id != item.line_id:
        raise HTTPException(status_code=403, detail="Cross-line operation not allowed")

def require_same_shift_if_operator(user: User, item: Item):
    if user.role == "OPERATOR" and user.shift_id is not None and user.shift_id != getattr(item, "shift_id", user.shift_id):
        # item has no shift; we check only user's shift rule as you requested
        raise HTTPException(status_code=403, detail="Operator shift mismatch")

def precondition_if_unmodified_since(request: Request, last_updated_at: datetime):
    ims = request.headers.get("If-Unmodified-Since")
    if not ims:
        return
    try:
        # Expect RFC1123 or ISO8601; accept ISO for simplicity
        ts = datetime.fromisoformat(ims.replace("Z","+00:00"))
        if last_updated_at and last_updated_at > ts:
            raise HTTPException(status_code=412, detail="Precondition Failed")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid If-Unmodified-Since")

def status_code(item: Item) -> str:
    # convenience: map id -> code via relationship if loaded; otherwise do a subquery in callers
    return getattr(item, "status").code if getattr(item, "status", None) else "UNKNOWN"

# ---------- GET /items ----------
@router.get("", summary="List items")
async def list_items(
    request: Request,
    page: int = Query(1, ge=1, description="1-based page index"),
    page_size: int = Query(10, ge=1, le=100, description="items per page (max 100)"),

    station: Optional[str] = Query(
        None, pattern="^(ROLL|BUNDLE)$", description="filter by station"
    ),
    line_code: Optional[str] = Query(None, description="e.g. 3 or 4"),
    product_code: Optional[str] = Query(None, description="contains match"),
    number: Optional[str] = Query(None, description="roll_number or bundle_number (contains)"),
    job_order_number: Optional[str] = Query(None, description="contains match"),
    roll_width_min: Optional[float] = Query(None, ge=0),
    roll_width_max: Optional[float] = Query(None, ge=0),

    # repeatable: ...?status=DEFECT&status=SCRAP
    status: Optional[List[str]] = Query(
        None, description="repeatable status codes"
    ),

    time_preset: Optional[str] = Query(
        None, pattern="^(today|yesterday|7d|30d|all)$"
    ),
    detected_from: Optional[datetime] = Query(None, description="ISO8601"),
    detected_to: Optional[datetime] = Query(None, description="ISO8601"),

    # repeatable include: ...?include=images&include=defects
    include: Optional[List[str]] = Query(
        None, description="optional includes: line,status,defects,images,reviews"
    ),

    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    # base query (exclude soft-deleted)
    q = select(Item).where(Item.deleted_at.is_(None))

    # joins needed for sorting & filters
    q = q.join(ItemStatus, Item.item_status_id == ItemStatus.id)
    if line_code:
        q = q.join(ProductionLine, Item.line_id == ProductionLine.id)

    # filters
    if station: q = q.where(Item.station == station)
    if line_code: q = q.where(ProductionLine.id == line_code)
    if product_code: q = q.where(Item.product_code.ilike(f"%{product_code}%"))
    if number:
        q = q.where(
            (Item.roll_number.ilike(f"%{number}%")) |
            (Item.bundle_number.ilike(f"%{number}%"))
        )
    if job_order_number: q = q.where(Item.job_order_number.ilike(f"%{job_order_number}%"))
    if roll_width_min is not None: q = q.where(Item.roll_width >= roll_width_min)
    if roll_width_max is not None: q = q.where(Item.roll_width <= roll_width_max)
    if status: q = q.where(ItemStatus.code.in_(status))

    # time range
    if detected_from: q = q.where(text("qc.items.detected_at >= :df")).params(df=detected_from)
    if detected_to: q = q.where(text("qc.items.detected_at <= :dt")).params(dt=detected_to)
    # (time_preset handling omitted here for brevity—map to df/dt)

    # sorting
    q = q.order_by(ItemStatus.display_order.asc(), Item.detected_at.desc(), Item.id.desc())

    # total
    total = (await db.execute(q.with_only_columns(text("count(*)")).order_by(None))).scalar()

    # page
    rows = (await db.execute(q.offset(offset).limit(page_size))).scalars().all()

    # counts (images/defects)
    data = []
    for it in rows:
        # eager load status & counts
        imgs = (await db.execute(
            select(text("count(*)")).select_from(ItemImage).where(ItemImage.item_id == it.id)
        )).scalar()
        defs = (await db.execute(
            select(text("count(*)")).select_from(ItemDefect).where(ItemDefect.item_id == it.id)
        )).scalar()

        # get status code
        st = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

        data.append({
            "id": it.id,
            "station": it.station,
            "line_id": it.line_id,
            "product_code": it.product_code,
            "roll_number": it.roll_number,
            "bundle_number": it.bundle_number,
            "job_order_number": it.job_order_number,
            "roll_width": float(it.roll_width) if it.roll_width is not None else None,
            "detected_at": it.detected_at.isoformat(),
            "status_code": st,
            "ai_note": it.ai_note,
            "scrap_requires_qc": it.scrap_requires_qc,
            "scrap_confirmed_by": it.scrap_confirmed_by,
            "scrap_confirmed_at": it.scrap_confirmed_at.isoformat() if it.scrap_confirmed_at else None,
            "current_review_id": it.current_review_id,
            "images_count": imgs,
            "defects_count": defs,
        })

    resp = {
        "data": data,
        "pagination": {
            "page": page, "page_size": page_size,
            "total": total, "total_pages": (total + page_size - 1) // page_size
        }
    }
    # (optional "included" set can be added if `include` supplied)
    return resp

# ---------- GET /items/{id} ----------
@router.get("/{item_id}")
async def get_item_detail(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["VIEWER", "OPERATOR", "INSPECTOR"])
    it = (await db.get(Item, item_id))
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")

    st = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

    # defects
    defs = (await db.execute(
        select(DefectType.code, ItemDefect.meta)
        .join(ItemDefect, DefectType.id == ItemDefect.defect_type_id)
        .where(ItemDefect.item_id == it.id)
    )).all()

    # images grouped by kind
    imgs = (await db.execute(
        select(ItemImage.id, ItemImage.kind, ItemImage.path)
        .where(ItemImage.item_id == it.id)
        .order_by(ItemImage.uploaded_at.desc())
    )).all()
    grouped = {"DETECTED": [], "FIX": [], "OTHER": []}
    for iid, kind, path in imgs:
        grouped.setdefault(kind, []).append({"id": iid, "path": path})

    # reviews (all)
    rws = (await db.execute(
        select(Review).where(Review.item_id == it.id).order_by(Review.submitted_at.desc())
    )).scalars().all()

    return {
        "data": {
            "id": it.id,
            "station": it.station,
            "line_id": it.line_id,
            "product_code": it.product_code,
            "roll_number": it.roll_number,
            "bundle_number": it.bundle_number,
            "job_order_number": it.job_order_number,
            "roll_width": float(it.roll_width) if it.roll_width is not None else None,
            "detected_at": it.detected_at.isoformat(),
            "status_code": st,
            "ai_note": it.ai_note,
            "scrap_requires_qc": it.scrap_requires_qc,
            "scrap_confirmed_by": it.scrap_confirmed_by,
            "scrap_confirmed_at": it.scrap_confirmed_at.isoformat() if it.scrap_confirmed_at else None,
            "current_review_id": it.current_review_id,
        },
        "defects": [{"defect_type_code": c, "meta": m} for c, m in defs],
        "images": grouped,
        "reviews": [
            {
                "id": rv.id, "review_type": rv.review_type, "state": rv.state,
                "submitted_by": rv.submitted_by, "submitted_at": rv.submitted_at.isoformat(),
                "reviewed_by": rv.reviewed_by, "reviewed_at": rv.reviewed_at.isoformat() if rv.reviewed_at else None,
                "submit_note": rv.submit_note, "review_note": rv.review_note, "reject_reason": rv.reject_reason
            } for rv in rws
        ]
    }

# ---------- POST /items/{id}/fix-request ----------
@router.post("/{item_id}/fix-request")
async def submit_fix_request(
    request: Request,
    item_id: int,
    body: FixRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])
    it = await db.get(Item, item_id)
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")
    require_same_line(user, it)
    if user.role == "OPERATOR":
        require_same_shift_if_operator(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    # status check
    st_code = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()
    if st_code not in ("DEFECT", "RECHECK"):
        raise HTTPException(status_code=400, detail="Fix request allowed only for DEFECT or RECHECK")

    # 409 if pending review exists
    existing = (await db.execute(
        select(Review.id).where(Review.item_id == it.id, Review.state == "PENDING")
    )).first()
    if existing:
        raise HTTPException(status_code=409, detail="Pending review exists")
    
    note = getattr(body, "note", None)
    image_ids = list(getattr(body, "image_ids", []) or [])

    rv = Review(
        item_id=it.id, review_type="DEFECT_FIX", state="PENDING",
        submitted_by=user.id, submit_note=note
    )
    db.add(rv)
    await db.flush()  # rv.id

    # link images as FIX
    if image_ids:
        await db.execute(
            text("""
                UPDATE qc.item_images
                   SET review_id=:rid, kind='FIX'
                 WHERE id = ANY(:ids)
            """),
            {"rid": rv.id, "ids": image_ids}
        )

    # keep current_review_id (per requirement)
    it.current_review_id = rv.id

    # event
    db.add(ItemEvent(
        item_id=it.id, actor_id=user.id, event_type="FIX_REQUEST_SUBMITTED",
        to_status_id=it.item_status_id
    ))

    await db.commit()
    return {"review_id": rv.id}

# ---------- POST /items/{id}/scrap ----------
@router.post("/{item_id}/scrap")
async def mark_scrap(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["OPERATOR", "INSPECTOR"])
    it = await db.get(Item, item_id)
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")
    require_same_line(user, it)
    if user.role == "OPERATOR":
        require_same_shift_if_operator(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    st_code = (await db.execute(select(ItemStatus.code).where(ItemStatus.id == it.item_status_id))).scalar()

    review_id = None
    if st_code == "SCRAP":
        it.scrap_confirmed_by = user.id
        it.scrap_confirmed_at = datetime.utcnow()
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="OPERATOR_CONFIRM_SCRAP", to_status_id=it.item_status_id))
    elif st_code == "RECHECK":
        # move to SCRAP and open a pending review for QC
        scrap_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "SCRAP"))).scalar_one()
        it.item_status_id = scrap_id
        it.scrap_requires_qc = True
        rv = Review(item_id=it.id, review_type="SCRAP_FROM_RECHECK", state="PENDING", submitted_by=user.id)
        db.add(rv)
        await db.flush()
        review_id = rv.id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="SCRAP_FROM_RECHECK", to_status_id=scrap_id))
    else:
        raise HTTPException(status_code=400, detail="Scrap allowed only when status is SCRAP or RECHECK")

    await db.commit()
    return {"ok": True, "review_id": review_id}

# ---------- PATCH /items/{id}/decision ----------
@router.patch("/{item_id}/decision")
async def decide_fix(
    request: Request,
    item_id: int,
    body: DecisionRequestBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_role(user, ["INSPECTOR"])
    it = await db.get(Item, item_id)
    if not it or it.deleted_at:
        raise HTTPException(status_code=404, detail="Item not found")
    require_same_line(user, it)

    precondition_if_unmodified_since(request, it.updated_at)

    review_id = getattr(body, 'review_id')
    decision = getattr(body, 'decision')
    note = getattr(body, 'note')

    rv: Review = await db.get(Review, review_id)
    if not rv or rv.item_id != it.id or rv.state != "PENDING":
        raise HTTPException(status_code=400, detail="Invalid or non-pending review")

    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    rv.reviewed_by = user.id
    rv.reviewed_at = datetime.utcnow()

    if decision == "APPROVED":
        rv.state = "APPROVED"
        rv.review_note = note
        new_status_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "QC_PASSED"))).scalar_one()
        it.item_status_id = new_status_id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="FIX_DECISION_APPROVED",
                         from_status_id=None, to_status_id=new_status_id))
    else:
        rv.state = "REJECTED"
        rv.reject_reason = note
        rej_status_id = (await db.execute(select(ItemStatus.id).where(ItemStatus.code == "REJECTED"))).scalar_one()
        it.item_status_id = rej_status_id
        db.add(ItemEvent(item_id=it.id, actor_id=user.id, event_type="FIX_DECISION_REJECTED",
                         from_status_id=None, to_status_id=rej_status_id))

    # keep current_review_id as-is (history)
    await db.commit()
    return {"ok": True, "new_status": "QC_PASSED" if decision=="APPROVED" else "REJECTED"}
