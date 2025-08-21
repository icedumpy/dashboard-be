from fastapi import APIRouter, Depends
from app.utils.deps import LimitQuery
from app.core.db.repo.item.item_repo import fetch_counters

router = APIRouter()

@router.get("")
def list_items(limit: int = Depends(LimitQuery(100, 500))):
    """ตัวอย่าง endpoint: ใช้ materialized view แทนรายการ item จริง เพื่อเดโมเร็วๆ"""
    return {"items": fetch_counters(limit)}